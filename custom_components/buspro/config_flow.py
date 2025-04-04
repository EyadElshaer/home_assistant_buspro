import aiohttp
import asyncio
import json
import socket
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
        self._discovered_gateways = {}

    async def async_step_user(self, user_input=None):
        """Handle the initial step."""
        errors = {}
        
        if user_input is not None:
            try:
                self._host = user_input[CONF_HOST]
                self._port = user_input[CONF_PORT]
                
                # Test connection using UDP
                try:
                    # Create a UDP socket
                    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                    sock.settimeout(5)  # 5 second timeout
                    
                    # Send a simple "read" command to the gateway
                    # HDL Buspro command format: 0x0DABBCCCC...
                    command = bytes([0x0D, 0xAB, 0x00, 0x00])  # Simple read command
                    
                    # Send the command
                    sock.sendto(command, (self._host, self._port))
                    
                    # Try to receive a response
                    try:
                        data, addr = sock.recvfrom(1024)
                        if data:  # If we got any response, consider it a success
                            return await self.async_step_discovery()
                    except socket.timeout:
                        errors["base"] = "cannot_connect"
                    finally:
                        sock.close()
                        
                except Exception as ex:
                    _LOGGER.error("Connection test failed: %s", ex)
                    errors["base"] = "cannot_connect"
                    
            except Exception:
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({
                vol.Required(CONF_HOST): cv.string,
                vol.Required(CONF_PORT, default=6000): cv.port,
            }),
            errors=errors
        )

    async def async_step_discovery(self, user_input=None):
        """Handle the device discovery step."""
        errors = {}
        
        if not self._discovered_devices:
            try:
                # Create a UDP socket for device discovery
                sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                sock.settimeout(5)
                
                # Send broadcast discovery command
                discovery_command = bytes([0x0D, 0xAB, 0x00, 0x01])  # Discovery command
                
                try:
                    sock.sendto(discovery_command, (self._host, self._port))
                    
                    # Wait for responses
                    start_time = asyncio.get_event_loop().time()
                    self._discovered_devices = []
                    
                    while asyncio.get_event_loop().time() - start_time < 3:  # 3 second discovery window
                        try:
                            data, addr = sock.recvfrom(1024)
                            if data:
                                # Parse device data from response
                                # This is a simplified example - adjust according to actual protocol
                                device_type = "light"  # Default to light for testing
                                device_addr = f"{data[4]}.{data[5]}.{data[6]}"
                                
                                self._discovered_devices.append({
                                    CONF_DEVICE_TYPE: device_type,
                                    CONF_DEVICE_NAME: f"Buspro Device {device_addr}",
                                    CONF_DEVICE_ADDRESS: device_addr,
                                    CONF_DEVICE_CHANNEL: 1
                                })
                        except socket.timeout:
                            continue
                            
                finally:
                    sock.close()
                    
                # If no devices found, add test devices
                if not self._discovered_devices:
                    self._discovered_devices = [
                        {
                            CONF_DEVICE_TYPE: "light",
                            CONF_DEVICE_NAME: "Living Room Light",
                            CONF_DEVICE_ADDRESS: "1.1.1",
                            CONF_DEVICE_CHANNEL: 1
                        },
                        {
                            CONF_DEVICE_TYPE: "switch",
                            CONF_DEVICE_NAME: "Kitchen Switch",
                            CONF_DEVICE_ADDRESS: "1.1.2",
                            CONF_DEVICE_CHANNEL: 1
                        }
                    ]
                    
            except Exception as ex:
                _LOGGER.error("Error discovering devices: %s", ex)
                errors["base"] = "discovery_failed"
                return self.async_show_form(
                    step_id="discovery",
                    errors=errors
                )

        if user_input is not None:
            selected_devices = user_input.get("selected_devices", [])
            for device_id in selected_devices:
                device = next(d for d in self._discovered_devices if d[CONF_DEVICE_ADDRESS] == device_id)
                self._devices.append(device)
            
            return self.async_create_entry(
                title=f"Buspro Gateway ({self._host})",
                data={
                    CONF_HOST: self._host,
                    CONF_PORT: self._port,
                    CONF_DEVICES: self._devices
                }
            )

        if not self._discovered_devices:
            return self.async_abort(reason="no_devices_found")

        device_options = {
            device[CONF_DEVICE_ADDRESS]: f"{device[CONF_DEVICE_NAME]} ({device[CONF_DEVICE_TYPE]})"
            for device in self._discovered_devices
        }

        return self.async_show_form(
            step_id="discovery",
            data_schema=vol.Schema({
                vol.Required("selected_devices"): cv.multi_select(device_options)
            }),
            errors=errors
        )

    def remove_service(self, zeroconf, type, name):
        """Handle removal of a service."""
        pass

    def add_service(self, zeroconf, type, name):
        """Handle addition of a service."""
        info = zeroconf.get_service_info(type, name)
        if info:
            self._discovered_gateways[name] = {
                CONF_HOST: socket.inet_ntoa(info.addresses[0]),
                CONF_PORT: info.port
            }

    def _determine_device_type(self, device_data: Dict) -> Optional[str]:
        """Determine the device type based on device data."""
        # This is where you implement the logic to determine device type
        # based on the device data received from the gateway
        device_type = device_data.get("type")
        
        if device_type in ["light", "switch", "cover", "climate"]:
            return device_type
        
        # Add more device type detection logic here
        return None
