from dataclasses import asdict
import json
import logging
from typing import List, Optional

from libp2p.crypto.keys import KeyPair
from libp2p.peer.id import ID as PeerID
import trio

from subnet.config import get_nmap
from subnet.db.database import RocksDB
from subnet.evals.evaluate import SubnetEvaluator
from subnet.hypertensor.chain_data import OverwatchCommit, OverwatchReveals
from subnet.hypertensor.chain_functions import Hypertensor, SubnetNodeClass
from subnet.hypertensor.mock.local_chain_functions import LocalMockHypertensor
from subnet.server.server import Server
from subnet.utils.commit import generate_overwatch_commit
from subnet.utils.protocols.ping import PingProtocol

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler()],
)
logger = logging.getLogger("overwatch-node/1.0.0")


class OverwatchNode:
    def __init__(
        self,
        key_pair: KeyPair,
        db: RocksDB,
        overwatch_node_id: int,
        base_port: int = 31330,
        hypertensor: Optional[Hypertensor | LocalMockHypertensor] = None,
        **kwargs,
    ):
        self.overwatch_node_id = overwatch_node_id
        self.hypertensor = hypertensor
        self.db = db
        self.base_port = base_port
        self.key_pair = key_pair
        self.subnet_evaluators: dict[int, SubnetEvaluator] = {}
        self.servers: dict[int, Server] = {}
        self.subnet_ids = []
        self.slots: dict[int, int | None] = {}  # subnet_id -> slot
        self.stop = trio.Event()

    async def run(self):
        async with trio.open_nursery() as nursery:
            nursery.start_soon(self.run_overwatch_node)

            while True:
                try:
                    self.subnet_ids = [1]  # Mock active subnets, but will query the blockchain for all subnet IDs

                    # TODO: If subnet is no longer active, remove it from servers dictionary
                    for subnet_id in self.subnet_ids:
                        slot = await self._get_subnet_slot(subnet_id)
                        if slot is None:
                            continue
                        if subnet_id not in self.servers:
                            self.servers[subnet_id] = Server(
                                port=self.base_port + subnet_id,
                                bootstrap_addrs=None,
                                key_pair=self.key_pair,
                                db=self.db,
                                subnet_id=subnet_id,
                                subnet_node_id=subnet_id,
                                hypertensor=self.hypertensor,
                                is_bootstrap=False,
                            )
                            nursery.start_soon(self.servers[subnet_id].run)
                except Exception as e:
                    logger.warning(e, exc_info=True)

                # Attempt to join subnets every 30 seconds if not already joined
                await trio.sleep(30)

    async def run_overwatch_node(self):
        self._async_stop_event = trio.Event()
        last_epoch = None
        started = False
        logged_started = False

        while not self.stop.is_set() and not self._async_stop_event.is_set():
            try:
                epoch_data = self.hypertensor.get_overwatch_epoch_data()
                if epoch_data is None:
                    logger.info("Waiting for epoch data")
                    await trio.sleep(6.0)
                    continue

                # Start on fresh epoch
                if started is False:
                    started = True
                    epoch_data = self.hypertensor.get_overwatch_epoch_data()
                    logger.info(
                        f"Current epoch is {epoch_data.overwatch_epoch}.  "
                        f"Starting consensus on next epoch in {epoch_data.seconds_remaining}s"
                    )
                    await trio.sleep(epoch_data.seconds_remaining)
                    continue
                elif not logged_started:
                    logger.info("✅ Starting overwatchconsensus")
                    logged_started = True

                current_epoch = self.hypertensor.get_overwatch_epoch_data().overwatch_epoch

                if current_epoch != last_epoch:
                    """
                    Add validation logic before and/or after `await run_consensus(current_epoch)`

                    The logic here should be for qualifying nodes (proving work), generating scores, etc.
                    """
                    logger.info(f"🆕 Epoch {current_epoch}")
                    last_epoch = current_epoch

                    # Attest/Validate
                    await self.run_commit_reveal(current_epoch)

                try:
                    # Get fresh epoch
                    epoch_data = self.hypertensor.get_overwatch_epoch_data()

                    logger.info(f"Waiting for next epoch {current_epoch + 1} in {epoch_data.seconds_remaining} seconds")

                    with trio.move_on_after(
                        max(
                            1.0,
                            epoch_data.seconds_remaining,
                        )
                    ):
                        await self._async_stop_event.wait()
                        logger.info("Starting next epoch")
                        break

                    if self._async_stop_event.is_set():
                        logger.info("Shutting down overwatch node")
                        break

                    pass  # Timeout reached
                except Exception as e:
                    logger.warning(f"Timeout reached: {e}", exc_info=True)
                    pass
            except Exception as e:
                logger.warning(f"Error waiting for next epoch: {e}", exc_info=True)
                await trio.sleep(6.0)

    async def run_commit_reveal(self, overwatch_epoch: int):
        commits: List[OverwatchCommit] = []
        for subnet_id in self.subnet_ids:
            try:
                logger.info(f"Processing subnet ID {subnet_id} for epoch {overwatch_epoch}")
                # Ensure we haven't already ran this
                # Check database to ensure we haven't already ran this subnet
                # This is helpful between restarts
                if self.db.nmap_get("commits", key=f"{overwatch_epoch}:{subnet_id}") is not None:
                    logger.info(f"Already ran subnet ID {subnet_id} for epoch {overwatch_epoch}")
                    continue

                score = await self.get_subnet_score(subnet_id, overwatch_epoch)
                logger.info(f"Score for subnet ID {subnet_id}: {score}")

                if score is not None:
                    commit_data = generate_overwatch_commit(score)
                    commit_hash = commit_data["commit_hash"]
                    salt = commit_data["salt"]

                    logger.info(
                        f"[Commit]: Weight: {score} Commit Hash: {commit_hash} Salt: {salt} Epoch: {overwatch_epoch}"
                    )

                    commits.append(OverwatchCommit(subnet_id=subnet_id, weight=commit_hash))
                    self.db.nmap_set(
                        "commits",
                        key=f"{overwatch_epoch}:{subnet_id}",
                        value={"weight": score, "commit_hash": commit_hash, "salt": salt},
                    )
            except Exception as e:
                logger.warning(f"Failed to commit subnet ID {subnet_id} for epoch {overwatch_epoch}: {e}")

        # Commit
        try:
            logger.info(f"[Commit]: Committing weights for epoch {overwatch_epoch}")
            commits = [asdict(c) for c in commits]
            self.hypertensor.commit_overwatch_subnet_weights(self.overwatch_node_id, commits)
        except Exception as e:
            logger.warning(f"Failed to commit weights for epoch {overwatch_epoch}: {e}")

        # Wait for reveal period
        reveal_async_stop_event = trio.Event()
        while not self.stop.is_set() and not reveal_async_stop_event.is_set():
            try:
                # We iterate here to ensure client clock matches blockchain
                epoch_data = self.hypertensor.get_overwatch_epoch_data()
                if epoch_data.overwatch_epoch > overwatch_epoch:
                    logger.info(
                        f"Epoch {overwatch_epoch} has completed, current epoch is {epoch_data.overwatch_epoch}, moving on"
                    )
                    break

                seconds_remaining_until_reveal = epoch_data.seconds_remaining_until_reveal

                if seconds_remaining_until_reveal == 0:
                    break

                logger.info(
                    f"[Reveal]: Current block is {epoch_data.block}, epoch cutoff block is {epoch_data.epoch_cutoff_block}"
                    f"Sleeping for {seconds_remaining_until_reveal} seconds"
                )
                with trio.move_on_after(
                    max(
                        1.0,
                        seconds_remaining_until_reveal,
                    )
                ):
                    await reveal_async_stop_event.wait()
                    break

                if reveal_async_stop_event.is_set():
                    break
            except Exception as e:
                logger.warning(f"Failed to wait for reveal phase: {e}", exc_info=True)
                pass

        epoch_data = self.hypertensor.get_overwatch_epoch_data()
        if epoch_data.overwatch_epoch > overwatch_epoch:
            logger.info(
                f"Epoch {overwatch_epoch} has completed, current epoch is {epoch_data.overwatch_epoch}, moving on"
            )
            return

        reveals: List[OverwatchReveals] = []
        for subnet_id in self.subnet_ids:
            try:
                commit = self.db.nmap_get("commits", key=f"{overwatch_epoch}:{subnet_id}")
                if commit is None:
                    logger.info(f"Commit for subnet ID {subnet_id} not found")
                    continue

                weight = commit.get("weight")
                salt = commit.get("salt")

                if weight is None or salt is None:
                    logger.info(f"Commit for subnet ID {subnet_id} is missing weight or salt: {commit}")
                    continue

                logger.info(f"[Reveal]: Weight: {weight} Salt: {salt}")

                reveals.append(OverwatchReveals(subnet_id=subnet_id, weight=weight, salt=salt))
            except Exception as e:
                logger.warning(f"Failed to reveal subnet ID {subnet_id} for epoch {overwatch_epoch}: {e}")

        try:
            logger.info(f"[Reveal]: Revealing weights for epoch {overwatch_epoch}")
            reveals = [asdict(r) for r in reveals]
            self.hypertensor.reveal_overwatch_subnet_weights(self.overwatch_node_id, reveals)
        except Exception as e:
            logger.warning(f"Failed to reveal weights for epoch {overwatch_epoch}: {e}")

    async def _get_subnet_slot(self, subnet_id: int) -> int | None:
        if self.slots.get(subnet_id) is None or self.slots.get(subnet_id) == "None":  # noqa: E711
            try:
                slot = self.hypertensor.get_subnet_slot(subnet_id)
                if slot == None or slot == "None":  # noqa: E711
                    return None
                self.slots[subnet_id] = int(str(slot))
                logger.debug(f"Subnet running in slot {self.slots.get(subnet_id)}")
            except Exception as e:
                logger.warning(f"Consensus get_subnet_slot={e}", exc_info=True)
        return self.slots.get(subnet_id)

    async def get_subnet_score(self, subnet_id: int, overwatch_epoch: int) -> int | None:
        try:
            nmap = get_nmap(subnet_id, overwatch_epoch - 1)
            logger.info(f"Getting heartbeats under nmap key: {nmap}")
            entries = self.db.nmap_get_all(nmap)
            logger.info(f"Entries for subnet {subnet_id}: {entries}")
            if entries is None or entries == {}:
                return None

            slot = await self._get_subnet_slot(subnet_id)
            if slot is None:
                return None
            subnet_epoch = self.hypertensor.get_subnet_epoch_data(slot).epoch
            logger.info(f"Subnet epoch for subnet {subnet_id}: {subnet_epoch}")

            validator_nodes = self.hypertensor.get_min_class_subnet_nodes_formatted(
                subnet_id, subnet_epoch, SubnetNodeClass.Included
            )

            total_nodes = len(validator_nodes)

            if total_nodes == 0:
                return None

            successful_validations = 0
            for validator in validator_nodes:
                subnet_node_id = validator.subnet_node_id
                peer_id = PeerID.from_base58(validator.peer_id)
                logger.info(f"Processing peer ID {peer_id} for subnet node ID {subnet_node_id}")
                entry_key, entry_value = self.find_entry(entries, subnet_id, subnet_node_id)
                logger.info(f"entry_key: {entry_key} for subnet_node_id: {subnet_node_id}")
                logger.info(f"entry_value: {entry_value} for subnet_node_id: {subnet_node_id}")
                if entry_key is None or entry_value is None:
                    logger.debug(f"Entry for subnet node ID {subnet_node_id} not found")
                    if peer_id is None:
                        continue

                    subnet_server = self.servers.get(subnet_id)
                    if subnet_server is None or subnet_server.host is None or subnet_server.dht is None:
                        continue

                    logger.debug(f"Pinging peer ID {peer_id}")
                    ping_protocol = PingProtocol(subnet_server.host, subnet_server.dht)

                    success = await ping_protocol.ping(peer_id)
                    logger.debug(f"Ping result: {success}")
                    if success:
                        successful_validations += 1
                else:
                    successful_validations += 1

            success_rate = successful_validations / total_nodes
            logger.info(f"Heartbeat success rate is {success_rate}")

            return int(success_rate * 1e18)
        except Exception as e:
            logger.warning(e, exc_info=True)
            return int(1e18)

    def find_entry(self, data, subnet_id: int, subnet_node_id: int):
        for key, value in data.items():
            payload = json.loads(value)

            if payload["subnet_id"] == subnet_id and payload["subnet_node_id"] == subnet_node_id:
                return key, value

        return None, None

    def find_peer_id_from_heartbeat(self, data, subnet_id: int, subnet_node_id: int) -> Optional[PeerID]:
        try:
            payload = json.loads(data)

            if payload["subnet_id"] == subnet_id and payload["subnet_node_id"] == subnet_node_id:
                peer_id_base58 = payload["peer_id"]
                return PeerID.from_base58(peer_id_base58)
        except Exception as e:
            logger.warning(e, exc_info=True)
            return None

    def find_peer_id_from_heartbeats(self, data, subnet_id: int, subnet_node_id: int) -> Optional[PeerID]:
        try:
            for key, value in data.items():
                payload = json.loads(value)

                if payload["subnet_id"] == subnet_id and payload["subnet_node_id"] == subnet_node_id:
                    peer_id_base58 = key.split(":", 1)[1]
                    return PeerID.from_base58(peer_id_base58)
        except Exception as e:
            logger.warning(e, exc_info=True)
            return None
