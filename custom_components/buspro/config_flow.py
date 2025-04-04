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
            self._discovered_gateways = await self.hass.async_add_executor_job(self._discover_gateways)

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
                return self.async_show_form(
                    step_id="user",
                    data_schema=vol.Schema({
                        vol.Required(CONF_HOST): cv.string,
                        vol.Required(CONF_PORT, default=6000): vol.In([6000, 6001])
                    }),
                    errors=errors
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

            if await self.hass.async_add_executor_job(self._test_connection):
                return await self.async_step_discovery()
            else:
                errors["base"] = "cannot_connect"
        except Exception:
            _LOGGER.exception("Unexpected exception")
            errors["base"] = "unknown"

        # If we get here, there was an error
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
                errors=errors
            )
        else:
            return self.async_show_form(
                step_id="user",
                data_schema=vol.Schema({
                    vol.Required(CONF_HOST): cv.string,
                    vol.Required(CONF_PORT, default=6000): vol.In([6000, 6001])
                }),
                errors=errors
            )

    def _discover_gateways(self) -> List[Dict]:
        """Discover Buspro gateways on the network."""
        discovered = []
        
        try:
            # Get the local IP address
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))  # Doesn't actually send any data
            local_ip = s.getsockname()[0]
            s.close()

            # Get network address
            ip = ipaddress.ip_interface(f"{local_ip}/24")
            network = ip.network

            # Scan network for Buspro gateways
            for host in network.hosts():
                host_str = str(host)
                for port in BUSPRO_PORTS:
                    try:
                        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                        sock.settimeout(0.1)  # Quick timeout for faster scanning
                        
                        try:
                            sock.sendto(BUSPRO_READ_COMMAND, (host_str, port))
                            # If we can send data without error, consider it a potential gateway
                            discovered.append({
                                "host": host_str,
                                "port": port
                            })
                        except socket.error:
                            continue
                        finally:
                            sock.close()
                    except Exception:
                        continue

        except Exception as ex:
            _LOGGER.error("Gateway discovery failed: %s", ex)

        return discovered

    def _test_connection(self) -> bool:
        """Test connection to the Buspro gateway."""
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.settimeout(2)
            
            try:
                sock.sendto(BUSPRO_READ_COMMAND, (self._host, self._port))
                return True  # If we can send data, consider it a success
            except socket.error:
                return False
            finally:
                sock.close()
                
        except Exception as ex:
            _LOGGER.error("Connection test failed: %s", ex)
            return False

    async def async_step_discovery(self, user_input=None):
        """Handle the device discovery step."""
        errors = {}
        
        if not self._discovered_devices:
            # For now, just add test devices
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
