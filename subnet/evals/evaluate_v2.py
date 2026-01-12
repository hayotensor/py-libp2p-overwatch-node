import logging
import os
from pathlib import Path
import secrets
from typing import TYPE_CHECKING, Optional, Tuple, cast

from dotenv import load_dotenv
from libp2p import new_host
from libp2p.crypto.ed25519 import (
    Ed25519PrivateKey,
    create_new_key_pair,
)
from libp2p.crypto.keys import KeyPair
from libp2p.crypto.secp256k1 import Secp256k1PrivateKey
from libp2p.kad_dht.kad_dht import (
    DHTMode,
    KadDHT,
)
from libp2p.peer.id import ID as PeerID
from libp2p.peer.pb import crypto_pb2
from libp2p.records.pubkey import PublicKeyValidator
from libp2p.records.validator import NamespacedValidator
from libp2p.tools.async_service import background_trio_service
import trio

from subnet.db.database import RocksDB
from subnet.hypertensor.chain_functions import Hypertensor, SubnetNodeClass
from subnet.utils.connections.bootstrap import connect_to_bootstrap_node_with_retry, connect_to_bootstrap_nodes
from subnet.utils.crypto.store_key import store_key
from subnet.utils.patches import apply_all_patches
from subnet.utils.pos.pos_transport import (
    PROTOCOL_ID as POS_PROTOCOL_ID,
    POSTransport,
)
from subnet.utils.pos.proof_of_stake import ProofOfStake

if TYPE_CHECKING:
    from libp2p.network.swarm import Swarm

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler()],
)
logger = logging.getLogger("server/1.0.0")

load_dotenv(os.path.join(Path.cwd(), '.env'))

DEFAULT_BOOTNODES = os.getenv('DEFAULT_BOOTNODES')

class SubnetEvaluator:
    def __init__(
        self,
        port: int,
        subnet_id: int,
        hypertensor: Hypertensor,
        db: RocksDB,
        private_key_path: Optional[str] = None,
    ):
        self.subnet_id = subnet_id
        self.hypertensor = hypertensor
        self.port = port
        self.db = db
        self.private_key_path = private_key_path
        self.dht = None

    async def run(self):
        from libp2p.utils.address_validation import (
            get_available_interfaces,
            get_optimal_binding_address,
        )

        listen_addrs = get_available_interfaces(self.port)

        key_pair = get_or_create_key_pair(
            self.private_key_path,
            hypertensor=self.hypertensor,
            subnet_id=self.subnet_id,
            overwatch_node_id=0,
        )

        proof_of_stake = ProofOfStake(
            subnet_id=self.subnet_id,
            hypertensor=self.hypertensor,
            min_class=0,
        )

        host = new_host(key_pair=key_pair)

        cast("Swarm", host.get_network()).connection_config.max_connections_per_peer = 10

        async with host.run(listen_addrs=listen_addrs), trio.open_nursery() as nursery:
            nursery.start_soon(host.get_peerstore().start_cleanup_task, 60)

            if self.hypertensor is not None:
                # Get active subnet bootstrap nodes
                all_bootnodes = self.hypertensor.get_bootnodes_formatted(self.subnet_id)
                bootnodes = all_bootnodes.subnet_bootnodes
                # Get backup bootnodes from peers in subnet
                backup_bootnodes = all_bootnodes.node_bootnodes
            else:
                bootnodes = DEFAULT_BOOTNODES
                backup_bootnodes = []

            if not bootnodes and not backup_bootnodes:
                return None, int(0.0 * 1e18)

            try:
                await connect_to_bootstrap_nodes(host, backup_bootnodes)
            except Exception:
                return int(0)

            self.dht = KadDHT(
                host,
                DHTMode.SERVER,
                enable_random_walk=True,
                validator=NamespacedValidator({"pk": PublicKeyValidator()}),
            )

            epoch = self.hypertensor.get_epoch_data().epoch

            async with background_trio_service(self.dht):
                return await self.score_consensus_accuracy(
                    epoch=epoch,
                )

    async def score_consensus_accuracy(
        self,
        epoch: int,
    ) -> int:
        """
        Score subnet based on on-chain consensus accuracy

        - Get each node in-consensus on-chain
        - Ping each node in the subnet
        """
        logger.info(f"Scoring subnet {self.subnet_id} on consensus accuracy")

        expected_peer_ids = None
        # Get each peer_id onchain
        """
        consensus_data = self.hypertensor.get_consensus_data_formatted(self.subnet_id, epoch)
        print("consensus_data", consensus_data)

        # Extract just the node IDs from the consensus data subnet_nodes
        consensus_node_ids = {node.subnet_node_id for node in consensus_data.data}

        print("consensus_node_ids", consensus_node_ids)

        # Get all SubnetNode's to get the PeerID's from the `consensus_node_ids`
        included_nodes = hypertensor.get_subnet_included_nodes(subnet_id)  # List[SubnetNode]

        print("included_nodes", included_nodes)

        Match nodes that appear in consensus
        expected_peer_ids = [
            node.peer_id
            for node in validator_nodes
            if node.id in consensus_node_ids
        ]
        """

        validator_nodes = self.hypertensor.get_min_class_subnet_nodes_formatted(self.subnet_id, 0, SubnetNodeClass.Included)
        print("validator_nodes", validator_nodes)

        expected_peer_ids = [
            node.peer_id
            for node in validator_nodes
        ]

        total_nodes = len(expected_peer_ids)

        if total_nodes == 0:
            return self.dht, 0.0

        logger.info(f"Pinging each node in subnet {self.subnet_id}")

        expected_peer_ids = [
            PeerID.from_base58(peer_id)
            for peer_id in expected_peer_ids
        ]
        print("expected_peer_ids IDs", expected_peer_ids)

        successful_connections = 0
        for peer_id in expected_peer_ids:
            addr = self.db.get_nested(self.subnet_id, peer_id.to_base58())

            if addr is not None:
                try:
                    await connect_to_bootstrap_node_with_retry(
                        host=self.dht.host,
                        bootstrap_addrs=addr,
                        max_retries=3,
                        retry_delay=0.5
                    )
                    successful_connections += 1
                    continue  # proceed to next peer_id on success
                except Exception as e:
                    logger.debug(f"Failed to connect to peer {peer_id} at cached address {addr}: {e}")

            # If we have no multiaddr or connect failed for peer, ask for it from the DHT
            peer_info = await self.dht.peer_routing.find_peer(peer_id)
            if not peer_info:
                logger.info(f"Peer {peer_id} not found in DHT")
                continue

            for addr in peer_info.addrs:
                logger.info(f"Peer {peer_id} address: {addr}")
                try:
                    await connect_to_bootstrap_node_with_retry(
                        host=self.dht.host,
                        bootstrap_addrs=str(addr),
                        max_retries=3,
                        retry_delay=0.5
                    )
                    successful_connections += 1

                    # Store successful addr in DB. ``subnet_id -> peer_id -> addr``
                    self.db.set_nested(self.subnet_id, peer_id.to_base58(), str(addr))

                    # break on the first successful connection for peer
                    break
                except Exception as e:
                    logger.debug(f"Failed to connect to peer {peer_id} at address {addr}: {e}")

        success_rate = successful_connections / len(expected_peer_ids)
        logger.info(f"Ping response success rate is {success_rate}")

        return int(success_rate * 1e18)




def get_or_create_key_pair(
    private_key_path: str,
    hypertensor: Hypertensor,
    subnet_id: int,
    overwatch_node_id: int
) -> KeyPair:
    """
    Get a keypair if it exists, otherwise create a new one and store it locally.

    This function assumes if the keypair exists, it's already registered on-chain.

    :param private_key_path: Expected path of private key
    :type private_key_path: str
    :param hypertensor: Blockchain RPC connection
    :type hypertensor: Hypertensor
    :param subnet_id: Subnet ID
    :type subnet_id: int
    :param overwatch_node_id: Overwatch node ID
    :type overwatch_node_id: int
    :rtype: KeyPair
    """
    try:
        with open(f"{private_key_path}", "rb") as f:
            data = f.read()
        private_key = crypto_pb2.PrivateKey.FromString(data)
        if private_key.Type == crypto_pb2.KeyType.Ed25519:
            private_key = Ed25519PrivateKey.from_bytes(private_key.Data)
        elif private_key.Type == crypto_pb2.KeyType.Secp256k1:
            private_key = Secp256k1PrivateKey.from_bytes(private_key.Data)
        else:
            raise ValueError("Unsupported key type")

        return KeyPair(private_key, private_key.get_public_key())
    except Exception:
        # Key does not exist, create new one and store locally
        key_pair = store_key(private_key_path)
        peer_id = PeerID.from_pubkey(key_pair.public_key)
        hypertensor.set_overwatch_node_peer_id(
            subnet_id=subnet_id,
            overwatch_node_id=overwatch_node_id,
            peer_id=peer_id.to_base58()
        )

        return key_pair
