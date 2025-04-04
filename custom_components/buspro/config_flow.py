import aiohttp
import asyncio
import json
import socket
import ipaddress
from collections import namedtuple
from typing import Dict, List, Tuple, Optional
import requests
import re
import logging
import voluptuous as vol
from zeroconf import ServiceBrowser, Zeroconf, ServiceInfo
from homeassistant import config_entries, exceptions
from homeassistant.core import HomeAssistant
from homeassistant.core import callback
import homeassistant.helpers.config_validation as cv
from homeassistant.components import ssdp
from homeassistant.helpers import aiohttp_client
import os
import time

from .const import (
    DOMAIN,
    CONF_HOST,
    CONF_PORT,
    CONF_DEVICES,
    CONF_DEVICE_TYPE,
    CONF_DEVICE_NAME,
    CONF_DEVICE_ADDRESS,
    CONF_DEVICE_CHANNEL
)

_LOGGER = logging.getLogger(__name__)

# HDL Buspro Protocol Commands
HEADER_START = bytes([0x48, 0x44, 0x4C, 0x4D, 0x49, 0x52, 0x41, 0x43, 0x4C, 0x45])  # "HDLMIRACLE"
BROADCAST_SUBNET = 0xFF
BROADCAST_DEVICE_ID = 0xFF
READ_DEVICE_INFO = 0x000E

# Actual HDL Buspro commands
BUSPRO_BROADCAST_DISCOVERY_COMMAND = HEADER_START + bytes([
    0xFF, 0xFF,  # Subnet ID, Device ID (broadcast)
    0x00, 0x00,  # Device type
    0x00, 0x0E,  # Operation code (READ_DEVICE_INFO)
    0x00, 0x00   # Data length
])

BUSPRO_DEVICE_DISCOVERY_COMMAND = HEADER_START + bytes([
    0xFF, 0xFF,  # Subnet ID, Device ID (broadcast)
    0x00, 0x00,  # Device type
    0x00, 0x0E,  # Operation code (READ_DEVICE_INFO)
    0x00, 0x00   # Data length
])

BUSPRO_PORTS = [6000, 6001]

class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Buspro."""
    
    VERSION = 1
    CONNECTION_CLASS = config_entries.CONN_CLASS_LOCAL_POLL

    def __init__(self):
        """Initialize the config flow."""
        self._devices = []
        self._host = None
        self._port = None
        self._discovered_devices = []
        self._discovered_gateways = []

    async def async_step_user(self, user_input=None):
        """Handle the initial step: Discover or prompt for manual entry."""
        errors = {}

        # Discover gateways only once
        if not hasattr(self, '_discovery_attempted'):
            _LOGGER.info("Starting Buspro gateway discovery...")
            self._discovered_gateways = await self.hass.async_add_executor_job(self._discover_gateways)
            _LOGGER.info(f"Discovery finished. Found {len(self._discovered_gateways)} potential gateways: {self._discovered_gateways}")
            self._discovery_attempted = True # Mark that discovery has run
        
        # If user_input is provided, process it
        if user_input is not None:
            try:
                if "gateway" in user_input:
                    # User selected a discovered gateway
                    host, port = user_input["gateway"].split(":")
                    self._host = host
                    self._port = int(port)
                    _LOGGER.info(f"User selected discovered gateway: {self._host}:{self._port}")
                else:
                    # Manual entry (only possible if no gateways were discovered)
                    self._host = user_input[CONF_HOST]
                    self._port = user_input[CONF_PORT]
                    _LOGGER.info(f"User entered gateway manually: {self._host}:{self._port}")
                
                # Set unique ID to prevent configuring the same gateway twice
                await self.async_set_unique_id(f"{self._host}:{self._port}")
                self._abort_if_unique_id_configured()
                
                # Proceed directly to device discovery step - connection is tested implicitly there
                return await self.async_step_discovery()
            
            except exceptions.ConfigEntryNotReady:
                 # This shouldn't happen here, but handle just in case
                 errors["base"] = "cannot_connect" 
            except AbortFlow as e:
                 return self.async_abort(reason=e.reason)
            except Exception:
                _LOGGER.exception("Unexpected exception during user step processing")
                errors["base"] = "unknown"

        # --- Show the appropriate form --- 
        
        # If we found gateways, force selection
        if self._discovered_gateways:
            gateway_options = {
                f"{gateway['host']}:{gateway['port']}": f"{gateway['host']} (Port {gateway['port']})"
                for gateway in self._discovered_gateways
            }
            
            return self.async_show_form(
                step_id="user",
                data_schema=vol.Schema({
                    vol.Required("gateway"): vol.In(gateway_options)
                }),
                description_placeholders={
                    "found_gateways": len(self._discovered_gateways)
                },
                errors=errors # Show errors if processing failed
            )
        else:
            # If no gateways found, allow manual entry
            _LOGGER.warning("No valid Buspro gateways discovered, prompting for manual entry.")
            return self.async_show_form(
                step_id="user",
                data_schema=vol.Schema({
                    vol.Required(CONF_HOST): cv.string,
                    vol.Required(CONF_PORT, default=6000): vol.In(BUSPRO_PORTS)
                }),
                errors=errors, # Show errors if processing failed
                description_placeholders={"found_gateways": 0} 
            )

    def _is_valid_buspro_response(self, data: bytes, addr: tuple) -> bool:
        """Check if the received data is a valid Buspro discovery response."""
        try:
            # Check minimum length (header + basic info)
            if len(data) < len(HEADER_START):
                return False

            # Verify HDL Buspro header
            if data[:len(HEADER_START)] != HEADER_START:
                return False

            # Additional validation based on response format
            # HDL response should contain at least:
            # - Header (10 bytes)
            # - Subnet ID (1 byte)
            # - Device ID (1 byte)
            # - Device type (2 bytes)
            # - Operation code (2 bytes)
            # - Data length (2 bytes)
            if len(data) < 18:  # Header + minimum response data
                return False

            _LOGGER.debug(f"Received valid HDL Buspro response from {addr}: {data.hex()}")
            return True

        except Exception as e:
            _LOGGER.debug(f"Error validating Buspro response: {e}")
            return False

    def _discover_gateways(self) -> List[Dict]:
        """Discover Buspro gateways on the network using broadcast."""
        discovered = []
        found_addrs = set()

        # Create socket for discovery
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.settimeout(0.5)

        try:
            # Bind to all interfaces
            sock.bind(('', 0))
            
            # Send discovery command to both ports
            for port in BUSPRO_PORTS:
                try:
                    # Send multiple times to increase reliability
                    for _ in range(3):
                        sock.sendto(BUSPRO_BROADCAST_DISCOVERY_COMMAND, ('255.255.255.255', port))
                        _LOGGER.debug(f"Sent discovery broadcast to port {port}")
                except Exception as e:
                    _LOGGER.debug(f"Error sending to port {port}: {e}")

            # Listen for responses
            start_time = time.time()
            while time.time() - start_time < 3:  # Listen for 3 seconds
                try:
                    data, addr = sock.recvfrom(1024)
                    if addr[0] not in found_addrs and self._is_valid_buspro_response(data, addr):
                        discovered.append({
                            "host": addr[0],
                            "port": addr[1]
                        })
                        found_addrs.add(addr[0])
                        _LOGGER.info(f"Found HDL Buspro gateway at {addr[0]}:{addr[1]}")
                except socket.timeout:
                    continue
                except Exception as e:
                    _LOGGER.debug(f"Error receiving discovery response: {e}")

        except Exception as e:
            _LOGGER.error(f"Error during gateway discovery: {e}")
        finally:
            sock.close()

        return discovered

    async def async_step_discovery(self, user_input=None):
        """Discover devices on the selected gateway."""
        errors = {}
        
        if not self._discovered_devices:
            try:
                # Create socket for device discovery
                sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                sock.settimeout(2.0)
                
                try:
                    # Send device discovery command
                    sock.sendto(BUSPRO_DEVICE_DISCOVERY_COMMAND, (self._host, self._port))
                    
                    # Listen for responses
                    start_time = time.time()
                    self._discovered_devices = []
                    
                    while time.time() - start_time < 3:  # Listen for 3 seconds
                        try:
                            data, addr = sock.recvfrom(1024)
                            if self._is_valid_buspro_response(data, addr):
                                # Parse device info from response
                                device_info = self._parse_device_info(data)
                                if device_info:
                                    self._discovered_devices.append(device_info)
                        except socket.timeout:
                            continue
                        except Exception as e:
                            _LOGGER.debug(f"Error receiving device info: {e}")
                            
                finally:
                    sock.close()
                    
                if not self._discovered_devices:
                    raise exceptions.ConfigEntryNotReady("No devices found")
                    
            except exceptions.ConfigEntryNotReady as e:
                _LOGGER.error(f"Failed to discover devices: {e}")
                errors["base"] = "cannot_connect"
                return self.async_show_form(
                    step_id="user",
                    data_schema=vol.Schema({
                        vol.Required(CONF_HOST): cv.string,
                        vol.Required(CONF_PORT, default=6000): vol.In(BUSPRO_PORTS)
                    }),
                    errors=errors
                )

        # --- Process user selection of devices --- 
        if user_input is not None:
            selected_device_addresses = user_input.get("selected_devices", [])
            _LOGGER.info(f"User selected device addresses: {selected_device_addresses}")
            self._devices = [] # Reset list before adding selected
            for device_addr in selected_device_addresses:
                device = next((d for d in self._discovered_devices if d[CONF_DEVICE_ADDRESS] == device_addr), None)
                if device:
                    self._devices.append(device)
                else:
                    _LOGGER.warning(f"Selected device address {device_addr} not found in discovered list.")
            
            if not self._devices:
                 _LOGGER.warning("User submitted discovery step without selecting any devices.")
                 errors["base"] = "no_devices_selected" # Add error for this case
                 # Re-show the discovery form below
            else:
                _LOGGER.info(f"Creating config entry with {len(self._devices)} selected devices.")
                return self.async_create_entry(
                    title=f"HDL Buspro ({self._host})", # Use a more descriptive title
                    data={
                        CONF_HOST: self._host,
                        CONF_PORT: self._port,
                        CONF_DEVICES: self._devices # Save the list of selected device dicts
                    }
                )

        # --- Show device selection form --- 
        if not self._discovered_devices:
            # If discovery (placeholder or real) resulted in zero devices
            _LOGGER.warning(f"No devices found on gateway {self._host}:{self._port}. Aborting.")
            return self.async_abort(reason="no_devices_found")

        # Create options for the multi-select form
        device_options = {
            device[CONF_DEVICE_ADDRESS]: f"{device[CONF_DEVICE_NAME]} (Type: {device[CONF_DEVICE_TYPE]}, Addr: {device[CONF_DEVICE_ADDRESS]}, Ch: {device.get(CONF_DEVICE_CHANNEL, 'N/A')})"
            for device in self._discovered_devices
        }

        return self.async_show_form(
            step_id="discovery",
            data_schema=vol.Schema({
                # Use Optional if you want to allow adding zero devices, Required otherwise
                vol.Optional("selected_devices", default=list(device_options.keys())) # Pre-select all devices
                : cv.multi_select(device_options)
            }),
            errors=errors, # Show errors like 'no_devices_selected'
            description_placeholders={
                "gateway_host": self._host,
                "gateway_port": self._port,
                "discovered_count": len(self._discovered_devices)
            }
        )

    def _parse_device_info(self, data: bytes) -> Optional[Dict]:
        """Parse device information from HDL Buspro response."""
        try:
            # Skip header
            pos = len(HEADER_START)
            
            # Extract device information
            subnet_id = data[pos]
            device_id = data[pos + 1]
            device_type = int.from_bytes(data[pos + 2:pos + 4], byteorder='big')
            
            # Determine device type based on HDL type code
            device_category = self._determine_device_category(device_type)
            if not device_category:
                return None
                
            return {
                CONF_DEVICE_TYPE: device_category,
                CONF_DEVICE_NAME: f"HDL Device {subnet_id}.{device_id}",
                CONF_DEVICE_ADDRESS: f"{subnet_id}.{device_id}",
                CONF_DEVICE_CHANNEL: 1  # Default channel, modify if needed
            }
            
        except Exception as e:
            _LOGGER.debug(f"Error parsing device info: {e}")
            return None

    def _determine_device_category(self, device_type: int) -> Optional[str]:
        """Map HDL device type to Home Assistant category."""
        # Add proper HDL device type mappings
        HDL_DEVICE_TYPES = {
            # Example mappings - replace with actual HDL type codes
            0x0001: "light",    # Dimmer
            0x0002: "switch",   # Relay
            0x0003: "cover",    # Curtain
            0x0004: "climate",  # HVAC
        }
        return HDL_DEVICE_TYPES.get(device_type)

    # --- Helper methods (remove_service, add_service, _determine_device_type) --- 
    # These were related to Zeroconf/SSDP or previous discovery methods and can be removed 
    # or adapted if needed for UDP parsing later.
    # Let's remove them for now to clean up.
    
    # def remove_service(self, zeroconf, type, name):
    #     """Handle removal of a service."""
    #     pass

    # def add_service(self, zeroconf, type, name):
    #     """Handle addition of a service."""
    #     info = zeroconf.get_service_info(type, name)
    #     if info:
    #         self._discovered_gateways[name] = {
    #             CONF_HOST: socket.inet_ntoa(info.addresses[0]),
    #             CONF_PORT: info.port
    #         }

    # def _determine_device_type(self, device_data: Dict) -> Optional[str]:
    #     """Determine the device type based on device data."""
    #     # This logic should move into the device discovery parsing
    #     device_type = device_data.get("type")
    #     if device_type in ["light", "switch", "cover", "climate"]:
    #         return device_type
    #     return None
