import logging
from typing import Optional, Tuple

from libp2p.kad_dht.kad_dht import KadDHT
from libp2p.peer.id import ID as PeerID

from subnet.hypertensor.chain_functions import Hypertensor, SubnetNodeClass
from subnet.utils.connections.bootstrap import connect_to_bootstrap_nodes

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler()],
)
logger = logging.getLogger("eval/consensus/1.0.0")

async def score_consensus_accuracy(
    subnet_id: int,
    epoch: int,
    dht: KadDHT,
    hypertensor: Hypertensor,
) -> Tuple[Optional[KadDHT], int]:
    """
    Score subnet based on on-chain consensus accuracy

    - Get each node in-consensus on-chain
    - Ping each node in the subnet
    """
    logger.info(f"Scoring subnet {subnet_id} on consensus accuracy")

    expected_peer_ids = None
    # Get each peer_id onchain
    consensus_data = hypertensor.get_consensus_data_formatted(subnet_id, epoch)
    print("consensus_data", consensus_data)

    # print("consensus_data", consensus_data)

    # # Extract just the node IDs from the consensus data subnet_nodes
    # consensus_node_ids = {node.subnet_node_id for node in consensus_data.data}

    # print("consensus_node_ids", consensus_node_ids)

    # # Get all SubnetNode's to get the PeerID's from the `consensus_node_ids`
    # included_nodes = hypertensor.get_subnet_included_nodes(subnet_id)  # List[SubnetNode]

    # print("included_nodes", included_nodes)

    # Match nodes that appear in consensus
    # expected_peer_ids = [
    #     node.peer_id
    #     for node in validator_nodes
    #     if node.id in consensus_node_ids
    # ]

    validator_nodes = hypertensor.get_min_class_subnet_nodes_formatted(subnet_id, 0, SubnetNodeClass.Included)
    print("validator_nodes", validator_nodes)

    expected_peer_ids = [
        node.peer_id
        for node in validator_nodes
    ]

    total_nodes = len(expected_peer_ids)

    if total_nodes == 0:
        return dht, 0.0

    logger.info(f"Pinging each node in subnet {subnet_id}")

    expected_peer_ids = [
        PeerID.from_base58(peer_id)
        for peer_id in expected_peer_ids
    ]
    print("expected_peer_ids IDs", expected_peer_ids)

    successful_pings = 0

    for peer_id in expected_peer_ids:
        peer_info = await dht.peer_routing.find_peer(peer_id)
        if not peer_info:
            logger.info(f"Peer {peer_id} not found in DHT")
            continue

        for addr in peer_info.addrs:
            logger.info(f"Peer {peer_id} address: {addr}")
            try:
                await connect_to_bootstrap_nodes(dht.host, [addr.__str__])
                successful_pings += 1
            except Exception as e:
                logger.debug(f"Failed to connect to peer {peer_id} at address {addr}: {e}")

    success_rate = successful_pings / len(expected_peer_ids)
    logger.info(f"Ping response success rate is {success_rate}")

    return dht, int(success_rate * 1e18)
