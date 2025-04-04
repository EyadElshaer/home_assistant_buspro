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
        # --- !!! IMPORTANT: IMPLEMENT ACTUAL VALIDATION LOGIC HERE !!! ---
        # This placeholder is too basic and will likely accept non-Buspro devices.
        # Check specific bytes, opcodes, lengths based on HDL protocol docs.
        # Example: Check if data starts with expected header and has minimum length
        if data and len(data) >= 10 and data.startswith(b'\xAA\x55'): # Fictional example header
            _LOGGER.debug(f"Received VALIDATED Buspro response from {addr}: {data.hex()}")
            return True 
        # -----------------------------------------------------------------
        _LOGGER.debug(f"Received INVALID or non-Buspro response from {addr}: {data.hex()}")
        return False

    def _discover_gateways(self) -> List[Dict]:
        """Discover Buspro gateways on the network using broadcast."""
        discovered = []
        found_addrs = set()
        broadcast_address = '255.255.255.255'
        timeout = 3.0 # Increased timeout slightly

        # Using a context manager ensures the socket is closed
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.settimeout(0.5) # Shorter timeout for individual recv attempts
            
            _LOGGER.debug("Sending Buspro broadcast discovery packets...")
            # Send broadcast command to both ports
            for port in BUSPRO_PORTS:
                try:
                    sock.sendto(BUSPRO_BROADCAST_DISCOVERY_COMMAND, (broadcast_address, port))
                except socket.error as e:
                    _LOGGER.warning(f"Error sending broadcast to port {port}: {e}")
            
            _LOGGER.debug(f"Listening for Buspro gateway responses for {timeout} seconds...")
            # Listen for responses for the total timeout duration
            end_time = asyncio.get_event_loop().time() + timeout
            while asyncio.get_event_loop().time() < end_time:
                try:
                    data, addr = sock.recvfrom(1024)
                    # Check if it's a *validated* response and not already found
                    if addr[0] not in found_addrs and self._is_valid_buspro_response(data, addr):
                        host = addr[0]
                        port = addr[1] # Use the port the gateway responded on
                        discovered.append({"host": host, "port": port})
                        found_addrs.add(host) # Avoid adding the same gateway IP multiple times
                        _LOGGER.info(f"Discovered and validated Buspro gateway at {host}:{port}")
                except socket.timeout:
                    # Expected timeout if no data received in the short interval, continue listening
                    await asyncio.sleep(0.1) # Small sleep to prevent busy-waiting
                except socket.error as e:
                    # Log other socket errors but try to continue listening
                    _LOGGER.debug(f"Socket error during discovery receive: {e}")
                    await asyncio.sleep(0.1)
                except Exception as e:
                    _LOGGER.error(f"Unexpected error during discovery processing: {e}")
                    break # Stop discovery on unexpected errors
                    
        _LOGGER.debug(f"Finished listening. Final discovered gateways: {discovered}")
        return discovered

    async def async_step_discovery(self, user_input=None):
        """Discover devices on the selected gateway."""
        errors = {}
        
        # This step runs *after* a gateway host/port is selected/entered.
        # We try to discover devices, implicitly testing the connection.
        
        if not self._discovered_devices:
            _LOGGER.info(f"Starting device discovery from gateway {self._host}:{self._port}")
            try:
                # --- !!! IMPLEMENT ACTUAL DEVICE DISCOVERY LOGIC HERE !!! ---
                # 1. Create UDP socket
                # 2. Send BUSPRO_DEVICE_DISCOVERY_COMMAND to self._host:self._port
                # 3. Listen for responses (multiple packets expected)
                # 4. Parse each response to get subnet, device ID, channel, device type
                # 5. Populate self._discovered_devices list with dicts containing:
                #    { CONF_DEVICE_TYPE: "...", CONF_DEVICE_NAME: "...", 
                #      CONF_DEVICE_ADDRESS: "subnet.id", CONF_DEVICE_CHANNEL: ... }
                # 6. If communication fails (timeout, socket error), raise exceptions.ConfigEntryNotReady
                
                # -------- Placeholder --------
                # Simulate discovery: Replace this block
                _LOGGER.warning("--- Using placeholder test devices --- Implement actual device discovery! ---")
                await asyncio.sleep(1) # Simulate network time
                # Simulate connection failure for testing:
                # if self._host == "192.168.1.99": 
                #     raise exceptions.ConfigEntryNotReady("Simulated connection failed")
                self._discovered_devices = [
                    {
                        CONF_DEVICE_TYPE: "light",
                        CONF_DEVICE_NAME: f"Light {self._host}-1.1.1", # Example name
                        CONF_DEVICE_ADDRESS: "1.1", # Subnet.ID format
                        CONF_DEVICE_CHANNEL: 1
                    },
                    {
                        CONF_DEVICE_TYPE: "switch",
                        CONF_DEVICE_NAME: f"Switch {self._host}-1.1.2",
                        CONF_DEVICE_ADDRESS: "1.2", # Subnet.ID format
                        CONF_DEVICE_CHANNEL: 1
                    }
                ]
                _LOGGER.info(f"Finished placeholder device discovery. Found {len(self._discovered_devices)} devices.")
                # --- End Placeholder ---
                
            except exceptions.ConfigEntryNotReady as e:
                 _LOGGER.error(f"Failed to connect or discover devices from gateway {self._host}:{self._port}: {e}")
                 # Go back to the user step to allow re-entry or re-selection
                 errors["base"] = "cannot_connect" 
                 # We need to show the form again, depending on whether gateways were originally found
                 if self._discovered_gateways: # If we started with discovered gateways, show selection again
                     gateway_options = { f"{gw['host']}:{gw['port']}": f"{gw['host']} (Port {gw['port']})" for gw in self._discovered_gateways }
                     return self.async_show_form( step_id="user", data_schema=vol.Schema({vol.Required("gateway"): vol.In(gateway_options)}), errors=errors, description_placeholders={"found_gateways": len(self._discovered_gateways)} )
                 else: # Otherwise, show manual entry again
                      return self.async_show_form( step_id="user", data_schema=vol.Schema({ vol.Required(CONF_HOST): cv.string, vol.Required(CONF_PORT, default=6000): vol.In(BUSPRO_PORTS) }), errors=errors, description_placeholders={"found_gateways": 0} )
            except Exception as ex:
                _LOGGER.exception(f"Unexpected error discovering devices from gateway {self._host}:{self._port}")
                errors["base"] = "unknown" # Generic error for unexpected issues
                # Abort on unknown discovery errors for now
                return self.async_abort(reason="discovery_failed_unknown")

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
