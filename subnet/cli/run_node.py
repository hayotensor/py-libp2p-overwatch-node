"""CLI command to run a libp2p subnet server node."""

import argparse
import logging
import os
from pathlib import Path
import random
import secrets
import sys

from dotenv import load_dotenv
from libp2p.crypto.ed25519 import create_new_key_pair
from libp2p.peer.id import ID as PeerID
from substrateinterface import (
    Keypair as SubstrateKeypair,
    KeypairType,
)
import trio

from subnet.db.database import RocksDB
from subnet.hypertensor.chain_functions import Hypertensor, KeypairFrom
from subnet.hypertensor.mock.local_chain_functions import LocalMockHypertensor
from subnet.server.node import OverwatchNode
from subnet.utils.crypto.store_key import get_key_pair

load_dotenv(os.path.join(Path.cwd(), ".env"))

PHRASE = os.getenv("PHRASE")

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler()],
)
logger = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Run a libp2p subnet node",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
# Run locally with no RPC connection

# Start bootnode (or start bootnode through `run_bootnode`)

python -m subnet.cli.run_node \
--private_key_path overwatch.key \
--overwatch_node_id 1 \
--no_blockchain_rpc

        """,
    )

    parser.add_argument("--base_path", type=str, default=None, help="Specify custom base path")

    parser.add_argument(
        "--private_key_path",
        type=str,
        default=None,
        help="Path to the private key file. ",
    )

    parser.add_argument("--subnet_id", type=int, default=1, help="Subnet ID this node belongs to. ")

    parser.add_argument(
        "--overwatch_node_id",
        type=int,
        default=0,
        help="Overwatch node ID this node belongs to. ",
    )

    parser.add_argument("--no_blockchain_rpc", action="store_true", help="[Testing] Run with no RPC")

    parser.add_argument(
        "--local_rpc",
        action="store_true",
        help="[Testing] Run in local RPC mode, uses LOCAL_RPC",
    )

    parser.add_argument(
        "--tensor_private_key",
        type=str,
        required=False,
        help="[Testing] Hypertensor blockchain private key",
    )

    parser.add_argument(
        "--phrase",
        type=str,
        required=False,
        help="[Testing] Coldkey phrase that controls actions which include funds, such as registering, and staking",
    )

    # add option to use verbose logging
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable verbose logging",
    )

    return parser.parse_args()


def main() -> None:
    """Main entry point for the CLI."""
    args = parse_args()

    # Set logging level
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
    else:
        logging.getLogger().setLevel(logging.INFO)

    # Log startup information
    logger.info("Starting libp2p subnet server node...")

    # port = args.port
    # if port <= 0:
    #     port = random.randint(10000, 60000)
    # logger.debug(f"Using port: {port}")

    # if args.bootstrap:
    #     logger.info(f"Bootstrap peers: {args.bootstrap}")
    # else:
    #     logger.info("Running as standalone node (no bootstrap peers)")

    if args.private_key_path is None:
        key_pair = create_new_key_pair(secrets.token_bytes(32))
    else:
        key_pair = get_key_pair(args.private_key_path)

    if not args.base_path:
        base_path = f"/tmp/{random.randint(100, 1000000)}"
    else:
        base_path = args.base_path

    db = RocksDB(base_path, args.subnet_id)

    hotkey = None
    if not args.no_blockchain_rpc:
        if args.local_rpc:
            rpc = os.getenv("LOCAL_RPC")
        else:
            rpc = os.getenv("DEV_RPC")

        if args.phrase is not None:
            hypertensor = Hypertensor(rpc, args.phrase)
            substrate_keypair = SubstrateKeypair.create_from_mnemonic(args.phrase, crypto_type=KeypairType.ECDSA)
            hotkey = substrate_keypair.ss58_address
            logger.info(f"hotkey: {hotkey}")
        elif args.tensor_private_key is not None:
            hypertensor = Hypertensor(rpc, args.tensor_private_key, KeypairFrom.PRIVATE_KEY)
            substrate_keypair = SubstrateKeypair.create_from_private_key(
                args.tensor_private_key, crypto_type=KeypairType.ECDSA
            )
            hotkey = substrate_keypair.ss58_address
            logger.info(f"hotkey: {hotkey}")
        else:
            # Default to using PHRASE if no other options are provided
            hypertensor = Hypertensor(rpc, PHRASE)
    else:
        # Run mock hypertensor blockchain for testing
        # This is a shared database between all local nodes
        hypertensor = LocalMockHypertensor(
            subnet_id=args.subnet_id,
            peer_id=PeerID.from_pubkey(key_pair.public_key),
            subnet_node_id=0,
            coldkey="",
            hotkey="",
            bootnode_peer_id="",
            client_peer_id="",
            reset_db=True,
            insert_mock_overwatch_node=True,
            insert_mock_subnet_nodes=(True, 9),
        )

    try:
        # By default, each subnet we enter uses the same keypair/peer ID and is differentiated by port
        node = OverwatchNode(
            base_port=41330,
            key_pair=key_pair,
            db=db,
            overwatch_node_id=0,
            hypertensor=hypertensor,
        )
        trio.run(node.run)
    except KeyboardInterrupt:
        logger.info("Received interrupt signal, shutting down...")
        sys.exit(0)
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)
    finally:
        ...


if __name__ == "__main__":
    main()
