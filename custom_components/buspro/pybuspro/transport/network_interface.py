from .udp_client import UDPClient
from ..helpers.telegram_helper import TelegramHelper
import asyncio
import logging
# from ..devices.control import Control

_LOGGER = logging.getLogger(__name__)

class NetworkInterface:
    def __init__(self, buspro, gateway_address_send_receive):
        self.buspro = buspro
        self.gateway_address_send_receive = gateway_address_send_receive
        self.udp_client = None
        self.callback = None
        self._init_udp_client()
        self._th = TelegramHelper()
        self._send_lock = asyncio.Lock()

    def _init_udp_client(self):
        self.udp_client = UDPClient(self.buspro, self.gateway_address_send_receive, self._udp_request_received)

    def _udp_request_received(self, data, address):
        if self.callback is not None:
            telegram = self._th.build_telegram_from_udp_data(data, address)
            self.callback(telegram)

    async def _send_message(self, message):
        try:
            async with asyncio.timeout(1.0):  # 1 second timeout
                return await self.udp_client.send_message(message)
        except asyncio.TimeoutError:
            _LOGGER.warning("Network interface timeout sending message")
            return False
        except Exception as e:
            _LOGGER.error("Error sending message: %s", str(e))
            return False

    """
    public methods
    """
    def register_callback(self, callback):
        self.callback = callback

    async def start(self):
        await self.udp_client.start()

    async def stop(self):
        if self.udp_client is not None:
            await self.udp_client.stop()
            self.udp_client = None

    async def send_telegram(self, telegram):
        async with self._send_lock:
            try:
                message = self._th.build_send_buffer(telegram)
                gateway_address_send, _ = self.gateway_address_send_receive
                
                # Log the telegram before sending
                self.buspro.logger.debug(self._th.build_telegram_from_udp_data(message, gateway_address_send))
                
                # Send with timeout
                async with asyncio.timeout(1.5):  # 1.5 second total timeout
                    success = await self._send_message(message)
                    if not success:
                        self.buspro.logger.warning("Failed to send telegram")
                    return success
            except asyncio.TimeoutError:
                self.buspro.logger.warning("Timeout sending telegram")
                return False
            except Exception as e:
                self.buspro.logger.error("Error sending telegram: %s", str(e))
                return False
