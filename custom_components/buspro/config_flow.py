import aiohttp
import asyncio
import json

from collections import namedtuple
from typing import Dict, List, Tuple
import requests
import re

import logging
import voluptuous as vol

from homeassistant import config_entries, exceptions
from homeassistant.core import HomeAssistant
from homeassistant.core import callback
import homeassistant.helpers.config_validation as cv

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
    VERSION = 1
    CONNECTION_CLASS = config_entries.CONN_CLASS_CLOUD_POLL
    
    def __init__(self):
        """Initialize the config flow."""
        self._devices = []
        self._host = None
        self._port = None
        self._discovered_devices = []

    async def async_step_user(self, user_input=None):
        """Handle the initial step."""
        errors = {}
        
        if user_input is not None:
            self._host = user_input[CONF_HOST]
            self._port = user_input[CONF_PORT]
            return await self.async_step_discovery()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({
                vol.Required(CONF_HOST): cv.string,
                vol.Required(CONF_PORT): cv.port
            }),
            errors=errors
        )

    async def async_step_discovery(self, user_input=None):
        """Handle the device discovery step."""
        errors = {}
        
        if not self._discovered_devices:
            # Try to discover devices from the gateway
            try:
                # This is where you would implement the actual device discovery logic
                # For now, we'll simulate some discovered devices
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
                title="Buspro",
                data={
                    CONF_HOST: self._host,
                    CONF_PORT: self._port,
                    CONF_DEVICES: self._devices
                }
            )

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
