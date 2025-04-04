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

    async def async_step_user(self, user_input=None):
        """Handle the initial step."""
        errors = {}
        
        if user_input is not None:
            self._host = user_input[CONF_HOST]
            self._port = user_input[CONF_PORT]
            return await self.async_step_devices()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({
                vol.Required(CONF_HOST): cv.string,
                vol.Required(CONF_PORT): cv.port
            }),
            errors=errors
        )

    async def async_step_devices(self, user_input=None):
        """Handle the devices step."""
        errors = {}
        
        if user_input is not None:
            device = {
                CONF_DEVICE_TYPE: user_input[CONF_DEVICE_TYPE],
                CONF_DEVICE_NAME: user_input[CONF_DEVICE_NAME],
                CONF_DEVICE_ADDRESS: user_input[CONF_DEVICE_ADDRESS],
                CONF_DEVICE_CHANNEL: user_input[CONF_DEVICE_CHANNEL]
            }
            
            # Check for duplicate devices
            for existing_device in self._devices:
                if (existing_device[CONF_DEVICE_ADDRESS] == device[CONF_DEVICE_ADDRESS] and
                    existing_device[CONF_DEVICE_CHANNEL] == device[CONF_DEVICE_CHANNEL]):
                    errors["base"] = "duplicate_device"
                    break
            
            if not errors:
                self._devices.append(device)
                # If user wants to add more devices, show the form again
                if user_input.get("add_another", False):
                    return await self.async_step_devices()
                
                # Otherwise, create the entry with all devices
                return self.async_create_entry(
                    title="Buspro",
                    data={
                        CONF_HOST: self._host,
                        CONF_PORT: self._port,
                        CONF_DEVICES: self._devices
                    }
                )

        return self.async_show_form(
            step_id="devices",
            data_schema=vol.Schema({
                vol.Required(CONF_DEVICE_TYPE): vol.In(["light", "switch", "cover", "climate"]),
                vol.Required(CONF_DEVICE_NAME): cv.string,
                vol.Required(CONF_DEVICE_ADDRESS): cv.string,
                vol.Required(CONF_DEVICE_CHANNEL): cv.positive_int,
                vol.Optional("add_another"): cv.boolean
            }),
            errors=errors
        )
