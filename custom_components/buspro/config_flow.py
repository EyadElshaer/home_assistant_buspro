import aiohttp
import asyncio
import json
import yaml
import os

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
    CONF_CHANNEL_ID,
    CONF_DEVICE_ID,
    CONF_SUBNET_ID,
    CONF_DEVICE_TYPE,
    CONF_RUNNING_TIME,
    CONF_NAME,
    CONF_DIMMABLE,
    CONF_DEVICES,
    CONF_PLATFORM,
    DEVICE_TYPES
)

_LOGGER = logging.getLogger(__name__)

class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):

    VERSION = 1
    CONNECTION_CLASS = config_entries.CONN_CLASS_CLOUD_POLL
    
    def __init__(self):
        """Initialize the config flow."""
        self._host = None
        self._port = None
        self._device_type = None
        self._channel_id = None
        self._device_id = None
        self._subnet_id = None
        self._name = None
        self._running_time = None
        self._dimmable = None

    async def async_step_user(self, user_input=None):
        """Handle the initial step."""
        errors = {}
        
        if user_input is not None:
            self._host = user_input[CONF_HOST]
            self._port = user_input[CONF_PORT]
            return await self.async_step_device()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({
                vol.Required(CONF_HOST): cv.string,
                vol.Required(CONF_PORT): cv.port
            }),
            errors=errors
        )

    async def async_step_device(self, user_input=None):
        """Handle the device configuration step."""
        errors = {}
        
        if user_input is not None:
            self._device_type = user_input[CONF_DEVICE_TYPE]
            self._channel_id = user_input[CONF_CHANNEL_ID]
            self._device_id = user_input[CONF_DEVICE_ID]
            self._subnet_id = user_input[CONF_SUBNET_ID]
            self._name = user_input[CONF_NAME]
            self._running_time = user_input.get(CONF_RUNNING_TIME)
            self._dimmable = user_input.get(CONF_DIMMABLE)

            # Add to configuration.yaml
            config_path = self.hass.config.path("configuration.yaml")
            try:
                with open(config_path, 'r') as file:
                    config = yaml.safe_load(file) or {}
            except FileNotFoundError:
                config = {}

            # Initialize the device type section if it doesn't exist
            if self._device_type not in config:
                config[self._device_type] = []

            # Create device configuration
            device_config = {
                CONF_PLATFORM: DOMAIN,
                CONF_DEVICES: {}
            }

            # Add running_time if specified
            if self._running_time is not None:
                device_config[CONF_RUNNING_TIME] = self._running_time

            # Create device identifier in format "subnet_id.channel_id.device_id"
            device_identifier = f"{self._subnet_id}.{self._channel_id}.{self._device_id}"
            
            # Create device details
            device_details = {}
            if self._name:
                device_details[CONF_NAME] = self._name
            if self._dimmable is not None:
                device_details[CONF_DIMMABLE] = self._dimmable
            if self._running_time is not None:
                device_details[CONF_RUNNING_TIME] = self._running_time

            device_config[CONF_DEVICES][device_identifier] = device_details

            # Add to configuration
            config[self._device_type].append(device_config)

            with open(config_path, 'w') as file:
                yaml.dump(config, file, default_flow_style=False)

            return self.async_create_entry(
                title=f"Buspro {self._device_type}",
                data={
                    CONF_HOST: self._host,
                    CONF_PORT: self._port,
                    CONF_DEVICE_TYPE: self._device_type,
                    CONF_CHANNEL_ID: self._channel_id,
                    CONF_DEVICE_ID: self._device_id,
                    CONF_SUBNET_ID: self._subnet_id,
                    CONF_NAME: self._name,
                    CONF_RUNNING_TIME: self._running_time,
                    CONF_DIMMABLE: self._dimmable
                }
            )

        return self.async_show_form(
            step_id="device",
            data_schema=vol.Schema({
                vol.Required(CONF_DEVICE_TYPE): vol.In(DEVICE_TYPES),
                vol.Required(CONF_CHANNEL_ID): cv.positive_int,
                vol.Required(CONF_DEVICE_ID): cv.positive_int,
                vol.Required(CONF_SUBNET_ID): cv.positive_int,
                vol.Optional(CONF_NAME): cv.string,
                vol.Optional(CONF_RUNNING_TIME): cv.positive_int,
                vol.Optional(CONF_DIMMABLE): cv.boolean
            }),
            errors=errors
        )
