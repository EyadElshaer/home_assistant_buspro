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

class BusproConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1
    CONNECTION_CLASS = config_entries.CONN_CLASS_CLOUD_POLL
    
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
            self._host = user_input[CONF_HOST]
            self._port = user_input[CONF_PORT]
            return await self.async_step_discovery()

        # Try to discover gateways automatically
        discovered_gateways = await self._async_discover_gateways()
        
        if discovered_gateways:
            return self.async_show_form(
                step_id="user",
                data_schema=vol.Schema({
                    vol.Required(CONF_HOST): vol.In({
                        gateway[CONF_HOST]: f"{gateway[CONF_HOST]}:{gateway[CONF_PORT]}"
                        for gateway in discovered_gateways
                    }),
                    vol.Required(CONF_PORT): cv.port
                }),
                errors=errors
            )

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({
                vol.Required(CONF_HOST): cv.string,
                vol.Required(CONF_PORT): cv.port
            }),
            errors=errors
        )

    async def _async_discover_gateways(self) -> List[Dict]:
        """Discover Buspro gateways using SSDP and Zeroconf."""
        discovered_gateways = []

        # SSDP Discovery
        ssdp_entries = await ssdp.async_get_discovery_info_by_st(
            self.hass, "urn:schemas-upnp-org:device:Basic:1"
        )
        
        for entry in ssdp_entries:
            if "HDL" in entry.get(ssdp.ATTR_UPNP_MANUFACTURER, ""):
                discovered_gateways.append({
                    CONF_HOST: entry.get(ssdp.ATTR_SSDP_LOCATION, "").split("//")[1].split(":")[0],
                    CONF_PORT: 6000  # Default Buspro port
                })

        # Zeroconf Discovery
        zeroconf = await self.hass.async_add_executor_job(Zeroconf)
        try:
            browser = ServiceBrowser(zeroconf, "_hdl-buspro._tcp.local.", self)
            await asyncio.sleep(5)  # Wait for discovery
            for gateway in self._discovered_gateways.values():
                discovered_gateways.append(gateway)
        finally:
            await self.hass.async_add_executor_job(zeroconf.close)

        return discovered_gateways

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

    async def async_step_discovery(self, user_input=None):
        """Handle the device discovery step."""
        errors = {}
        
        if not self._discovered_devices:
            try:
                # Connect to the gateway and discover devices
                session = aiohttp_client.async_get_clientsession(self.hass)
                async with session.get(f"http://{self._host}:{self._port}/devices") as response:
                    if response.status == 200:
                        devices_data = await response.json()
                        self._discovered_devices = []
                        
                        for device in devices_data:
                            # Determine device type based on device data
                            device_type = self._determine_device_type(device)
                            
                            if device_type:
                                self._discovered_devices.append({
                                    CONF_DEVICE_TYPE: device_type,
                                    CONF_DEVICE_NAME: device.get("name", f"Buspro Device {device.get('address')}"),
                                    CONF_DEVICE_ADDRESS: device.get("address"),
                                    CONF_DEVICE_CHANNEL: device.get("channel", 1)
                                })
                    else:
                        errors["base"] = "discovery_failed"
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
                title="Buspro",
                data={
                    CONF_HOST: self._host,
                    CONF_PORT: self._port,
                    CONF_DEVICES: self._devices
                }
            )

        if not self._discovered_devices:
            return self.async_abort(reason="no_devices_found")

        # Create a list of device options for the multi-select
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

    def _determine_device_type(self, device_data: Dict) -> Optional[str]:
        """Determine the device type based on device data."""
        # This is where you implement the logic to determine device type
        # based on the device data received from the gateway
        device_type = device_data.get("type")
        
        if device_type in ["light", "switch", "cover", "climate"]:
            return device_type
        
        # Add more device type detection logic here
        return None
