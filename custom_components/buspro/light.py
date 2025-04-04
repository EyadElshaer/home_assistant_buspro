"""
This component provides light support for Buspro.

For more details about this platform, please refer to the documentation at
https://home-assistant.io/components/...
"""

import logging
import asyncio

import homeassistant.helpers.config_validation as cv
import voluptuous as vol
from homeassistant.components.light import (
    LightEntity, 
    ColorMode, 
    PLATFORM_SCHEMA, 
    ATTR_BRIGHTNESS
)
from homeassistant.const import (CONF_NAME, CONF_DEVICES)
from homeassistant.core import callback

from ..buspro import DATA_BUSPRO
from datetime import timedelta
import homeassistant.helpers.event as event


_LOGGER = logging.getLogger(__name__)

DEFAULT_DEVICE_RUNNING_TIME = 0
DEFAULT_PLATFORM_RUNNING_TIME = 0
DEFAULT_DIMMABLE = True

DEVICE_SCHEMA = vol.Schema({
    vol.Optional("running_time", default=DEFAULT_DEVICE_RUNNING_TIME): cv.positive_int,
    vol.Optional("dimmable", default=DEFAULT_DIMMABLE): cv.boolean,
    vol.Required(CONF_NAME): cv.string,
})

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend({
    vol.Optional("running_time", default=DEFAULT_PLATFORM_RUNNING_TIME): cv.positive_int,
    vol.Required(CONF_DEVICES): {cv.string: DEVICE_SCHEMA},
})


# noinspection PyUnusedLocal
async def async_setup_platform(hass, config, async_add_entites, discovery_info=None):
    """Set up Buspro light devices."""
    # noinspection PyUnresolvedReferences
    from .pybuspro.devices import Light

    hdl = hass.data[DATA_BUSPRO].hdl
    devices = []
    platform_running_time = int(config["running_time"])

    for address, device_config in config[CONF_DEVICES].items():
        name = device_config[CONF_NAME]
        device_running_time = int(device_config["running_time"])
        dimmable = bool(device_config["dimmable"])

        if device_running_time == 0:
            device_running_time = platform_running_time
        if dimmable:
            device_running_time = 0

        address2 = address.split('.')
        device_address = (int(address2[0]), int(address2[1]))
        channel_number = int(address2[2])
        _LOGGER.debug("Adding light '{}' with address {} and channel number {}".format(name, device_address, channel_number))

        light = Light(hdl, device_address, channel_number, name)
        devices.append(BusproLight(hass, light, device_running_time, dimmable))

    async_add_entites(devices)
    for device in devices:
        await device.async_read_status()


# noinspection PyAbstractClass
class BusproLight(LightEntity):
    """Representation of a Buspro light."""

    def __init__(self, hass, device, running_time, dimmable):
        self._hass = hass
        self._device = device
        self._running_time = running_time
        self._dimmable = dimmable
        self._command_lock = asyncio.Lock()
        if self._dimmable:
            self._attr_color_mode = ColorMode.BRIGHTNESS
            self._attr_supported_color_modes = {ColorMode.BRIGHTNESS}
        else:
            self._attr_color_mode = ColorMode.ONOFF
            self._attr_supported_color_modes = {ColorMode.ONOFF}
        self.async_register_callbacks()
        # Set the polling interval to 1 minute for regular state updates
        self._polling_interval = timedelta(minutes=1)
        event.async_track_time_interval(hass, self.async_update, self._polling_interval)


    @callback
    def async_register_callbacks(self):
        """Register callbacks to update hass after device was changed."""

        # noinspection PyUnusedLocal
        async def after_update_callback(device):
            """Call after device was updated."""
            self.async_write_ha_state()

        self._device.register_device_updated_cb(after_update_callback)

    @property
    def should_poll(self):
        """No polling needed within Buspro."""
        return True

    async def async_update(self, *args):
        """Fetch new state data for this light."""
        await self.async_read_status()

    #async def async_update(self):
     #   """Fetch new state data for this light."""
      #  await self.async_read_status()

    @property
    def name(self):
        """Return the display name of this light."""
        return self._device.name

    @property
    def available(self):
        """Return True if entity is available."""
        return self._hass.data[DATA_BUSPRO].connected

    @property
    def brightness(self):
        """Return the brightness of the light."""
        brightness = self._device.current_brightness / 100 * 255
        return brightness

    @property
    def is_on(self):
        """Return true if light is on."""
        return self._device.is_on

    async def async_turn_on(self, **kwargs):
        """Instruct the light to turn on."""
        if ATTR_BRIGHTNESS in kwargs:
            target_brightness = int(kwargs[ATTR_BRIGHTNESS] / 255 * 100)
        else:
            target_brightness = self._device.previous_brightness if self._device.previous_brightness is not None and not self.is_on else 100

        # Update state optimistically
        self._device._brightness = target_brightness
        self.async_write_ha_state()

        # Send command with timeout
        async def send_command():
            try:
                async with asyncio.timeout(0.5):  # 500ms timeout for the command
                    if ATTR_BRIGHTNESS in kwargs:
                        success = await self._device.set_brightness(target_brightness, 0)  # Set running time to 0 for immediate response
                    else:
                        if self._device.previous_brightness is not None and not self.is_on:
                            success = await self._device.set_brightness(target_brightness, 0)
                        else:
                            success = await self._device.set_on(0)
                    
                    if not success:
                        _LOGGER.warning(f"Failed to send command to light {self.name}")
                        # Schedule a state refresh
                        self._hass.async_create_task(self.async_read_status())
            except asyncio.TimeoutError:
                _LOGGER.warning(f"Command timeout for light {self.name}")
                # Schedule a state refresh
                self._hass.async_create_task(self.async_read_status())
            except Exception as e:
                _LOGGER.error(f"Error sending command to light {self.name}: {str(e)}")
                # Schedule a state refresh
                self._hass.async_create_task(self.async_read_status())

        # Send command in background
        self._hass.async_create_task(send_command())

    async def async_turn_off(self, **kwargs):
        """Instruct the light to turn off."""
        # Update state optimistically
        self._device._brightness = 0
        self.async_write_ha_state()

        # Send command with timeout
        async def send_command():
            try:
                async with asyncio.timeout(0.5):  # 500ms timeout for the command
                    success = await self._device.set_off(0)  # Set running time to 0 for immediate response
                    if not success:
                        _LOGGER.warning(f"Failed to send command to light {self.name}")
                        # Schedule a state refresh
                        self._hass.async_create_task(self.async_read_status())
            except asyncio.TimeoutError:
                _LOGGER.warning(f"Command timeout for light {self.name}")
                # Schedule a state refresh
                self._hass.async_create_task(self.async_read_status())
            except Exception as e:
                _LOGGER.error(f"Error sending command to light {self.name}: {str(e)}")
                # Schedule a state refresh
                self._hass.async_create_task(self.async_read_status())

        # Send command in background
        self._hass.async_create_task(send_command())

    @property
    def unique_id(self):
        """Return the unique id."""
        return self._device.device_identifier

    async def async_read_status(self):
        """Read the status of the device."""
        await self._device.read_status()
        self.async_write_ha_state()