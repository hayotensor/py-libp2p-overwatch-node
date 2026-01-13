import logging
import sys

from libp2p.abc import (
    IHost,
)
from libp2p.custom_types import (
    TProtocol,
)
from libp2p.kad_dht.kad_dht import KadDHT
from libp2p.network.stream.net_stream import (
    INetStream,
)
from libp2p.peer.id import ID as PeerID
import trio

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler()],
)
logger = logging.getLogger("ping/1.0.0")

PING_PROTOCOL_ID = TProtocol("/ipfs/ping/1.0.0")
PING_LENGTH = 32
RESP_TIMEOUT = 60


async def handle_ping(stream: INetStream) -> None:
    while True:
        try:
            payload = await stream.read(PING_LENGTH)
            peer_id = stream.muxed_conn.peer_id
            if payload is not None:
                logger.info(f"received ping from {peer_id}")

                await stream.write(payload)
                logger.info(f"responded with pong to {peer_id}")

        except Exception:
            await stream.reset()
            break


async def send_ping(stream: INetStream) -> None:
    try:
        payload = b"\x01" * PING_LENGTH
        logger.info(f"sending ping to {stream.muxed_conn.peer_id}")

        await stream.write(payload)

        with trio.fail_after(RESP_TIMEOUT):
            response = await stream.read(PING_LENGTH)

        if response == payload:
            logger.info(f"received pong from {stream.muxed_conn.peer_id}")

    except Exception as e:
        logger.debug(f"error occurred : {e}")


class PingProtocol:
    def __init__(self, host: IHost, dht: KadDHT):
        self.host = host
        self.dht = dht
        self.lock = trio.Lock()

    async def ping(self, peer_id: PeerID) -> bool:
        try:
            peer_info = await self.dht.find_peer(peer_id)
            stream = await self._create_stream_with_retry(peer_id)
            await send_ping(stream)
            return True
        except Exception as e:
            logger.debug(f"Ping error occurred : {e}")
            return False

    async def _create_stream_with_retry(
        self, peer_id: PeerID, max_retries: int = 3, retry_delay: float = 0.5
    ) -> INetStream:
        """Create ping stream with retry mechanism for connection readiness."""
        logger.debug(f"About to create stream for protocol {PING_PROTOCOL_ID}")

        for attempt in range(max_retries):
            try:
                stream = await self.host.new_stream(peer_id, [PING_PROTOCOL_ID])
                logger.debug("Ping stream created successfully")
                return stream
            except Exception as e:
                if attempt < max_retries - 1:
                    logger.debug(f"Stream creation attempt {attempt + 1} failed: {e}, retrying...")
                    await trio.sleep(retry_delay)
                else:
                    logger.debug(f"Stream creation failed after {max_retries} attempts: {e}")
                    raise
        raise RuntimeError("Failed to create ping stream after retries")
