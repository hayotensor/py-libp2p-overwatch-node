from libp2p.custom_types import TProtocol

from subnet.utils.pubsub.heartbeat import HEARTBEAT_TOPIC

GOSSIPSUB_PROTOCOL_ID = TProtocol("/meshsub/1.0.0")
PK_DIR = "keys"


def get_nmap(subnet_id: int, overwatch_epoch: int) -> str:
    return f"{HEARTBEAT_TOPIC}:{str(subnet_id)}:{str(overwatch_epoch)}"
