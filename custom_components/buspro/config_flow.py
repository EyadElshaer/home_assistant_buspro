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

# Placeholder commands - Replace with actual Buspro commands
BUSPRO_BROADCAST_DISCOVERY_COMMAND = bytes.fromhex('EA 00 00 00 01 00 00 00 00 00 00 00 00 00 00 01') # Example command
BUSPRO_DEVICE_DISCOVERY_COMMAND = bytes.fromhex('EA 00 00 00 02 00 00 00 00 00 00 00 00 00 00 02') # Example command
BUSPRO_READ_COMMAND = bytes.fromhex('0D AB 00 00 00 00 00 00 00 00 00 00 00 00 00 00')
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
        """Handle the initial step."""
        errors = {}

        # Try to discover gateways first
        if not self._discovered_gateways:
            _LOGGER.info("Starting Buspro gateway discovery...")
            self._discovered_gateways = await self.hass.async_add_executor_job(self._discover_gateways)
            _LOGGER.info(f"Discovered {len(self._discovered_gateways)} potential gateways: {self._discovered_gateways}")

        if not user_input:
            # If we found gateways, show them in a selection form
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
                    }
                )
            else:
                # If no gateways found, allow manual entry
                _LOGGER.warning("No Buspro gateways found, prompting for manual entry.")
                return self.async_show_form(
                    step_id="user",
                    data_schema=vol.Schema({
                        vol.Required(CONF_HOST): cv.string,
                        vol.Required(CONF_PORT, default=6000): vol.In([6000, 6001])
                    }),
                    errors=errors,
                    description_placeholders={"found_gateways": 0} # Update placeholder for manual entry message
                )

        # Handle user selection or manual entry
        try:
            if "gateway" in user_input:
                # User selected a discovered gateway
                host, port = user_input["gateway"].split(":")
                self._host = host
                self._port = int(port)
            else:
                # Manual entry
                self._host = user_input[CONF_HOST]
                self._port = user_input[CONF_PORT]

            _LOGGER.info(f"Testing connection to {self._host}:{self._port}")
            if await self.hass.async_add_executor_job(self._test_connection):
                _LOGGER.info("Connection successful.")
                return await self.async_step_discovery()
            else:
                _LOGGER.warning("Connection failed.")
                errors["base"] = "cannot_connect"
        except Exception:
            _LOGGER.exception("Unexpected exception during connection/setup")
            errors["base"] = "unknown"

        # If we get here, there was an error, redisplay the form
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
                errors=errors,
                description_placeholders={"found_gateways": len(self._discovered_gateways)}
            )
        else:
            return self.async_show_form(
                step_id="user",
                data_schema=vol.Schema({
                    vol.Required(CONF_HOST): cv.string,
                    vol.Required(CONF_PORT, default=6000): vol.In([6000, 6001])
                }),
                errors=errors,
                description_placeholders={"found_gateways": 0}
            )

    def _is_valid_buspro_response(self, data: bytes, addr: tuple) -> bool:
        """Check if the received data is a valid Buspro discovery response."""
        # Add logic here to validate the response based on the Buspro protocol
        # For example, check message length, specific header bytes, etc.
        # This is a placeholder - needs actual validation logic!
        if data and len(data) >= 16: # Example: Check minimum length
            _LOGGER.debug(f"Received potential Buspro response from {addr}: {data.hex()}")
            return True # Assume valid for now if we get any data back
        return False

    def _discover_gateways(self) -> List[Dict]:
        """Discover Buspro gateways on the network using broadcast."""
        discovered = []
        found_addrs = set()
        broadcast_address = '255.255.255.255'
        timeout = 2.0 # Seconds to wait for responses

        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.settimeout(timeout)
            
            try:
                # Send broadcast command to both ports
                for port in BUSPRO_PORTS:
                    try:
                        sock.sendto(BUSPRO_BROADCAST_DISCOVERY_COMMAND, (broadcast_address, port))
                        _LOGGER.debug(f"Sent broadcast discovery to {broadcast_address}:{port}")
                    except socket.error as e:
                        _LOGGER.warning(f"Error sending broadcast to port {port}: {e}")
                
                # Listen for responses
                start_time = asyncio.get_event_loop().time()
                while asyncio.get_event_loop().time() - start_time < timeout:
                    try:
                        data, addr = sock.recvfrom(1024)
                        # Check if it's a valid response and not already found
                        if addr[0] not in found_addrs and self._is_valid_buspro_response(data, addr):
                            discovered.append({
                                "host": addr[0],
                                "port": addr[1] # Use the port the gateway responded on
                            })
                            found_addrs.add(addr[0]) # Avoid adding the same gateway multiple times
                            _LOGGER.info(f"Discovered valid Buspro gateway at {addr[0]}:{addr[1]}")
                    except socket.timeout:
                        # No more responses within the timeout window for this read
                        pass
                    except socket.error as e:
                        _LOGGER.debug(f"Socket error during discovery receive: {e}")
                        break # Stop listening on error

            except Exception as ex:
                _LOGGER.error(f"Gateway discovery failed: {ex}")

        return discovered

    def _test_connection(self) -> bool:
        """Test connection to the Buspro gateway."""
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.settimeout(2)
                try:
                    # Send a command and wait for *any* response as a basic check
                    sock.sendto(BUSPRO_READ_COMMAND, (self._host, self._port))
                    data, addr = sock.recvfrom(1024) 
                    _LOGGER.debug(f"Received response during connection test from {addr}: {data.hex()}")
                    return True # If we receive anything, assume connection is okay
                except socket.timeout:
                    _LOGGER.warning("Timeout during connection test - no response received.")
                    return False
                except socket.error as e:
                    _LOGGER.warning(f"Socket error during connection test: {e}")
                    return False
        except Exception as ex:
            _LOGGER.error(f"Connection test failed with exception: {ex}")
            return False

    async def async_step_discovery(self, user_input=None):
        """Handle the device discovery step."""
        errors = {}
        
        if not self._discovered_devices:
            _LOGGER.info(f"Starting device discovery from gateway {self._host}:{self._port}")
            try:
                # Implement actual device discovery here
                # Send BUSPRO_DEVICE_DISCOVERY_COMMAND to the gateway
                # Parse responses to get device info (subnet, id, channel, type)
                
                # -------- Placeholder for actual discovery --------
                await asyncio.sleep(1) # Simulate discovery time
                _LOGGER.warning("Using placeholder test devices - implement actual device discovery!")
                self._discovered_devices = [
                    {
                        CONF_DEVICE_TYPE: "light",
                        CONF_DEVICE_NAME: "Living Room Light (Test)",
                        CONF_DEVICE_ADDRESS: "1.1.1", # Combine subnet/id
                        CONF_DEVICE_CHANNEL: 1
                    },
                    {
                        CONF_DEVICE_TYPE: "switch",
                        CONF_DEVICE_NAME: "Kitchen Switch (Test)",
                        CONF_DEVICE_ADDRESS: "1.1.2", # Combine subnet/id
                        CONF_DEVICE_CHANNEL: 1
                    }
                ]
                _LOGGER.info(f"Finished device discovery. Found {len(self._discovered_devices)} devices.")
                # -------------------------------------------------
                
            except Exception as ex:
                _LOGGER.error(f"Error discovering devices from gateway: {ex}")
                errors["base"] = "discovery_failed"
                # Show error on the discovery step form (which doesn't exist yet, 
                # but we might add one or show it on the final step)
                # For now, let's abort
                return self.async_abort(reason="discovery_failed")

        if user_input is not None:
            selected_devices = user_input.get("selected_devices", [])
            _LOGGER.info(f"User selected devices: {selected_devices}")
            for device_id in selected_devices:
                # Find the selected device from the discovered list
                device = next((d for d in self._discovered_devices if d[CONF_DEVICE_ADDRESS] == device_id), None)
                if device:
                    self._devices.append(device)
                else:
                    _LOGGER.warning(f"Selected device ID {device_id} not found in discovered list.")
            
            _LOGGER.info(f"Creating config entry with {len(self._devices)} devices.")
            return self.async_create_entry(
                title=f"Buspro Gateway ({self._host})",
                data={
                    CONF_HOST: self._host,
                    CONF_PORT: self._port,
                    CONF_DEVICES: self._devices
                }
            )

        if not self._discovered_devices:
            _LOGGER.warning("No devices found after discovery.")
            return self.async_abort(reason="no_devices_found")

        # Create options for the multi-select form
        device_options = {
            # Use device address (subnet.id) as the key
            device[CONF_DEVICE_ADDRESS]: f"{device[CONF_DEVICE_NAME]} (Type: {device[CONF_DEVICE_TYPE]}, Addr: {device[CONF_DEVICE_ADDRESS]}, Ch: {device[CONF_DEVICE_CHANNEL]})"
            for device in self._discovered_devices
        }

        return self.async_show_form(
            step_id="discovery",
            data_schema=vol.Schema({
                # Ensure selected_devices is required if there are options
                vol.Required("selected_devices") if device_options else vol.Optional("selected_devices")
                : cv.multi_select(device_options)
            }),
            errors=errors,
            description_placeholders={
                "gateway_host": self._host,
                "gateway_port": self._port,
                "discovered_count": len(self._discovered_devices)
            }
        )

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
