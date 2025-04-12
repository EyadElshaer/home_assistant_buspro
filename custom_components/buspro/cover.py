"""Support for Buspro covers."""
import logging

import homeassistant.helpers.config_validation as cv
import voluptuous as vol
from homeassistant.components.cover import (
    CoverEntity, 
    CoverEntityFeature, 
    CoverDeviceClass, 
    PLATFORM_SCHEMA,
    ATTR_POSITION
)
from homeassistant.const import CONF_NAME, CONF_DEVICES
from homeassistant.core import callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from datetime import timedelta
import homeassistant.helpers.event as event

from .const import (
    DOMAIN, 
    CONF_SUBNET_ID, 
    CONF_DEVICE_ID, 
    CONF_CHANNEL, 
    CONF_DEVICE_TYPE,
    CONF_OPENING_TIME,
    DEFAULT_OPENING_TIME,
    CONF_ADJUSTABLE,
    DEFAULT_ADJUSTABLE
)

_LOGGER = logging.getLogger(__name__)

DEVICE_SCHEMA = vol.Schema({
    vol.Required(CONF_NAME): cv.string,
    vol.Optional(CONF_OPENING_TIME, default=DEFAULT_OPENING_TIME): cv.positive_int,
    vol.Optional(CONF_ADJUSTABLE, default=DEFAULT_ADJUSTABLE): cv.boolean,    
})

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend({
    vol.Required(CONF_DEVICES): {cv.string: DEVICE_SCHEMA},
})

async def async_setup_platform(hass, config, async_add_entities, discovery_info=None):
    """Set up Buspro cover devices through configuration.yaml."""
    # Legacy support - this is deprecated and will be removed in a future version
    from .pybuspro.devices import Cover

    if DOMAIN not in hass.data:
        _LOGGER.error("Buspro integration not set up, please set it up first")
        return

    # Use the first module available
    module_data = next(iter(hass.data[DOMAIN].values()))
    hdl = module_data["module"].hdl
    devices = []

    for address, device_config in config[CONF_DEVICES].items():
        name = device_config[CONF_NAME]
        opening_time = int(device_config[CONF_OPENING_TIME])
        adjustable = bool(device_config[CONF_ADJUSTABLE])

        address_parts = address.split('.')
        device_address = (int(address_parts[0]), int(address_parts[1]))
        channel_number = int(address_parts[2])
        _LOGGER.debug(f"Adding cover '{name}' with address {device_address}, channel {channel_number}, adjustable: {adjustable}")

        cover = Cover(hdl, device_address, channel_number, name)
        devices.append(BusproCover(hass, cover, opening_time, adjustable))

    async_add_entities(devices)
    # Read status of all devices on startup
    for device in devices:
        await device.async_update()


async def async_setup_entry(
    hass: HomeAssistant, 
    config_entry: ConfigEntry, 
    async_add_entities: AddEntitiesCallback
):
    """Set up Buspro cover devices from a config entry."""
    from .pybuspro.devices import Cover
    
    # Get the Buspro module and devices from the config entry
    module_data = hass.data[DOMAIN][config_entry.entry_id]
    hdl = module_data["module"].hdl
    devices_config = module_data["devices"]
    
    devices = []
    
    # Go through all configured devices and set up covers
    for device_id, device_config in devices_config.items():
        if device_config[CONF_DEVICE_TYPE] != "cover":
            continue
            
        # Extract device details
        subnet_id = device_config[CONF_SUBNET_ID]
        device_id_num = device_config[CONF_DEVICE_ID]
        channel = device_config.get(CONF_CHANNEL, 1)
        
        # Get cover-specific configuration
        opening_time = device_config.get(CONF_OPENING_TIME, DEFAULT_OPENING_TIME)
        adjustable = device_config.get(CONF_ADJUSTABLE, DEFAULT_ADJUSTABLE)
        
        # Create a device name
        name = f"Cover {subnet_id}.{device_id_num}.{channel}"
        
        device_address = (subnet_id, device_id_num)
        
        _LOGGER.debug(f"Adding cover '{name}' with address {device_address}, channel {channel}, adjustable: {adjustable}")
        
        cover = Cover(hdl, device_address, channel, name)
        devices.append(BusproCover(hass, cover, opening_time, adjustable))
    
    async_add_entities(devices)
    
    # Read status of all devices on startup
    for device in devices:
        await device.async_update()


class BusproCover(CoverEntity):
    """Representation of a Buspro cover."""

    def __init__(self, hass, device, opening_time=DEFAULT_OPENING_TIME, adjustable=DEFAULT_ADJUSTABLE):
        self._hass = hass
        self._device = device
        self._attr_device_class = CoverDeviceClass.CURTAIN
        self._opening_time = opening_time
        self._adjustable = adjustable
        self.async_register_callbacks()
        # Set the polling interval (e.g., every 60 minutes)
        self._polling_interval = timedelta(minutes=60)
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

    @property
    def name(self):
        """Return the display name of this cover."""
        return self._device.name

    @property
    def available(self):
        """Return True if entity is available."""
        # Check if the Buspro module is connected
        # We need to find the module in the hass data
        for module_data in self._hass.data[DOMAIN].values():
            if hasattr(module_data, "module") and hasattr(module_data["module"], "connected"):
                return module_data["module"].connected
        return False

    @property
    def is_closed(self):
        """Return true if cover is closed."""
        return self._device.is_closed

    @property
    def is_closing(self):
        """Return true if cover is closing for 30 seconds after command."""
        return self._device.is_closing

    @property
    def is_opening(self):
        """Return true if cover is opening for 30 seconds after command."""
        return self._device.is_opening
        
    @property
    def current_cover_position(self):
        """Return true if cover is opening for 30 seconds after command."""
        return self._device.current_cover_position

    @property
    def supported_features(self) -> CoverEntityFeature:
        """Flag supported features."""
        if self._adjustable:
            features = (
                CoverEntityFeature.OPEN
                | CoverEntityFeature.CLOSE
                | CoverEntityFeature.STOP
                | CoverEntityFeature.SET_POSITION
            )
        else:
            features = (
                CoverEntityFeature.OPEN
                | CoverEntityFeature.CLOSE
                | CoverEntityFeature.STOP
            )
        return features

    async def async_open_cover(self, **kwargs):
        """Instruct the cover to open."""
        await self._device.set_open()

    async def async_close_cover(self, **kwargs):
        """Instruct the cover to close."""
        await self._device.set_close()

    async def async_stop_cover(self, **kwargs):
        """Instruct the cover to stop."""
        await self._device.set_stop()

    async def async_read_status(self):
        """Read the status of the device."""
        status = self._device._status
        if status is None:
            """Fetch new state data for this light."""
            await self._device.read_status()
        self.async_write_ha_state()
    
    async def async_update(self, *args):
        """Fetch new state data for this light."""
        await self.async_read_status()
    
    async def async_set_cover_position(self, **kwargs):
        """Set the cover position."""
        position = int(kwargs.get(ATTR_POSITION))
        await self._device.set_position(position)

    @property
    def unique_id(self):
        """Return the unique id."""
        return self._device.device_identifier 