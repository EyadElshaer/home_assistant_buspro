from .control import _SingleChannelControl
from .device import Device
from ..helpers.enums import *
from ..helpers.generics import Generics
import asyncio


class Light(Device):
    def __init__(self, buspro, device_address, channel_number, name="", delay_read_current_state_seconds=0):
        super().__init__(buspro, device_address, name)
        # device_address = (subnet_id, device_id, channel_number)

        self._buspro = buspro
        self._device_address = device_address
        self._channel = channel_number
        self._brightness = 0
        self._previous_brightness = None
        self._command_lock = asyncio.Lock()
        self.register_telegram_received_cb(self._telegram_received_cb)
        self._call_read_current_status_of_channels(run_from_init=True)

    def _telegram_received_cb(self, telegram):

        # if telegram.target_address[1] == 72:
        #    print("==== {}".format(str(telegram)))

        if telegram.operate_code == OperateCode.SingleChannelControlResponse:
            channel = telegram.payload[0]
            # success = telegram.payload[1]
            brightness = telegram.payload[2]
            if channel == self._channel:
                self._brightness = brightness
                self._set_previous_brightness(self._brightness)
                self._call_device_updated()
        elif telegram.operate_code == OperateCode.ReadStatusOfChannelsResponse:
            if self._channel <= telegram.payload[0]:
                self._brightness = telegram.payload[self._channel]
                self._set_previous_brightness(self._brightness)
                self._call_device_updated()
        elif telegram.operate_code == OperateCode.SceneControlResponse:
            self._call_read_current_status_of_channels()

    async def set_on(self, running_time_seconds=0):
        intensity = 100
        return await self._set(intensity, running_time_seconds)

    async def set_off(self, running_time_seconds=0):
        intensity = 0
        return await self._set(intensity, running_time_seconds)

    async def set_brightness(self, intensity, running_time_seconds=0):
        return await self._set(intensity, running_time_seconds)

    async def read_status(self):
        await self._call_read_current_status_of_channels()

    @property
    def device_identifier(self):
        return f"{self._device_address}-{self._channel}"

    @property
    def supports_brightness(self):
        return True

    @property
    def previous_brightness(self):
        return self._previous_brightness

    @property
    def current_brightness(self):
        return self._brightness

    @property
    def is_on(self):
        return self._brightness > 0

    async def _set(self, intensity, running_time_seconds):
        async with self._command_lock:
            try:
                # Update state immediately
                self._brightness = intensity
                self._set_previous_brightness(self._brightness)

                # Prepare command
                generics = Generics()
                (minutes, seconds) = generics.calculate_minutes_seconds(running_time_seconds)

                scc = _SingleChannelControl(self._buspro)
                scc.subnet_id, scc.device_id = self._device_address
                scc.channel_number = self._channel
                scc.channel_level = intensity
                scc.running_time_minutes = minutes
                scc.running_time_seconds = seconds

                # Send command with timeout
                async with asyncio.timeout(0.5):  # 500ms timeout
                    await scc.send()
                    return True
            except asyncio.TimeoutError:
                return False
            except Exception:
                return False

    def _set_previous_brightness(self, brightness):
        if self.supports_brightness and brightness > 0:
            self._previous_brightness = brightness
