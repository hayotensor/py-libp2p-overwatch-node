import asyncio
import math
import threading
import time
from functools import partial
from typing import Dict, Sequence

from subnet.proto import dht_pb2
from subnet.utils.authorizers.auth import SignatureAuthorizer

from libp2p.kad_dht.kad_dht import KadDHT
from libp2p.peer.id import ID as PeerID
import trio
import logging 

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler()],
)
logger = logging.getLogger("/ping/1.0.0")


async def ping(
    peer_id: PeerID,
    _dht: KadDHT,
    node: mesh.dht.DHTNode,
    *,
    wait_timeout: float = 5,
) -> float:
    try:
        ping_request = dht_pb2.PingRequest(peer=node.protocol.node_info)
        start_time = time.perf_counter()
        ping_response = await node.protocol.get_stub(peer_id).rpc_ping(ping_request, timeout=wait_timeout)
        return time.perf_counter() - start_time
    except Exception as e:
        logger.error(f"Ping Error {e}", exc_info=True)
        if str(e) == "protocol not supported":  # Happens on servers with client-mode DHT (e.g., reachable via relays)
            return time.perf_counter() - start_time

        logger.debug(f"Failed to ping {peer_id}:", exc_info=True)
        return math.inf

async def ping_test(
    peer_id: PeerID,
    _dht: mesh.DHT,
    node: mesh.dht.DHTNode,
    *,
    wait_timeout: float = 5,
) -> float:
    try:
        ping_request = dht_pb2.PingRequest(validate=False)
        start_time = time.perf_counter()
        ping_response = await node.protocol.get_stub(peer_id).rpc_ping(ping_request, timeout=wait_timeout)
        return time.perf_counter() - start_time
    except Exception as e:
        logger.error(f"Ping Test Error {e}", exc_info=True)
        if str(e) == "protocol not supported":  # Happens on servers with client-mode DHT (e.g., reachable via relays)
            return time.perf_counter() - start_time

        logger.debug(f"Failed to ping {peer_id}:", exc_info=True)
        return math.inf

async def ping_test_parallel(peer_ids: Sequence[PeerID], *args, **kwargs) -> Dict[PeerID, float]:
    rpc_infos = await asyncio.gather(*[ping_test(peer_id, *args, **kwargs) for peer_id in peer_ids])
    return dict(zip(peer_ids, rpc_infos))

async def ping_test_parallel2(peer_ids: Sequence[PeerID], authorizer: SignatureAuthorizer, *args, **kwargs) -> Dict[PeerID, float]:
    rpc_infos = await asyncio.gather(*[ping_test(peer_id, *args, **kwargs) for peer_id in peer_ids])
    return dict(zip(peer_ids, rpc_infos))

async def ping_parallel(peer_ids: Sequence[PeerID], *args, **kwargs) -> Dict[PeerID, float]:
    rpc_infos = await asyncio.gather(*[ping(peer_id, *args, **kwargs) for peer_id in peer_ids])
    return dict(zip(peer_ids, rpc_infos))


class PingAggregator:
    def __init__(self, dht: KadDHT, *, ema_alpha: float = 0.2, expiration: float = 300):
        self.dht = dht
        self.ema_alpha = ema_alpha
        self.expiration = expiration
        self.ping_emas = mesh.TimedStorage()
        self.lock = trio.Lock()

    def simple_ping(self, peer_ids: Sequence[PeerID], **kwargs) -> Dict[PeerID, float] | MPFuture[Dict[PeerID, float]]:
        current_rtts = self.dht.run_coroutine(partial(ping_test_parallel, peer_ids, **kwargs))
        logger.debug(f"Current RTTs: {current_rtts}")

        return current_rtts

    def ping(self, peer_ids: Sequence[PeerID], **kwargs) -> None:
        current_rtts = self.dht.run_coroutine(partial(ping_test_parallel, peer_ids, **kwargs))
        logger.debug(f"Current RTTs: {current_rtts}")

        with self.lock:
            expiration = mesh.get_dht_time() + self.expiration
            for peer_id, rtt in current_rtts.items():
                prev_rtt = self.ping_emas.get(peer_id)
                if prev_rtt is not None and prev_rtt.value != math.inf:
                    rtt = self.ema_alpha * rtt + (1 - self.ema_alpha) * prev_rtt.value  # Exponential smoothing
                self.ping_emas.store(peer_id, rtt, expiration)

    def to_dict(self) -> Dict[PeerID, float]:
        with self.lock, self.ping_emas.freeze():
            smoothed_rtts = {peer_id: rtt.value for peer_id, rtt in self.ping_emas.items()}
            logger.debug(f"Smothed RTTs: {smoothed_rtts}")
            return smoothed_rtts
