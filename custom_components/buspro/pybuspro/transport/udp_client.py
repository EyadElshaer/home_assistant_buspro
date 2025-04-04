import asyncio
import socket


class UDPClient:

    class UDPClientFactory(asyncio.DatagramProtocol):

        def __init__(self, buspro, data_received_callback=None):
            self.buspro = buspro
            self.transport = None
            self.data_received_callback = data_received_callback
            self._ready = asyncio.Event()

        def connection_made(self, transport):
            self.transport = transport
            self._ready.set()

        def datagram_received(self, data, address):
            if self.data_received_callback is not None:
                self.data_received_callback(data, address)

        def error_received(self, exc):
            self.buspro.logger.warning('Error received: %s', exc)
            pass

        def connection_lost(self, exc):
            self.buspro.logger.info('closing transport %s', exc)
            self._ready.clear()
            pass

    def __init__(self, buspro, gateway_address_send_receive, callback):
        self.buspro = buspro
        self._gateway_address_send, self._gateway_address_receive = gateway_address_send_receive
        self.callback = callback
        self.transport = None
        self._protocol = None
        self._send_lock = asyncio.Lock()

    # def register_callback(self, callback):
    #     self.callback = callback

    def _data_received_callback(self, data, address):
        self.callback(data, address)

    def _create_multicast_sock(self):
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.setblocking(False)
            sock.bind(self._gateway_address_receive)
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, 0)
            # Set a small receive buffer to prevent buffering
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1024)
            return sock
        except Exception as ex:
            self.buspro.logger.warning("Could not connect to {}: {}".format(self._gateway_address_receive, ex))

    async def _connect(self):
        try:
            self._protocol = UDPClient.UDPClientFactory(self.buspro, data_received_callback=self._data_received_callback)

            sock = self._create_multicast_sock()
            if sock is None:
                self.buspro.logger.warning("Socket is None")
                return

            (transport, _) = await self.buspro.loop.create_datagram_endpoint(lambda: self._protocol, sock=sock)

            self.transport = transport
            # Wait for connection to be ready
            await asyncio.wait_for(self._protocol._ready.wait(), timeout=1.0)
        except Exception as ex:
            self.buspro.logger.warning("Could not create endpoint to {}: {}".format(self._gateway_address_receive, ex))

    async def start(self):
        await self._connect()

    async def stop(self):
        if self.transport is not None:
            self.transport.close()

    async def send_message(self, message):
        if self.transport is None:
            self.buspro.logger.info("Could not send message. Transport is None.")
            return False

        try:
            async with self._send_lock:
                async with asyncio.timeout(0.5):  # 500ms timeout for sending
                    self.transport.sendto(message, self._gateway_address_send)
                    return True
        except asyncio.TimeoutError:
            self.buspro.logger.warning("Timeout sending message")
            return False
        except Exception as ex:
            self.buspro.logger.warning("Error sending message: %s", ex)
            return False
