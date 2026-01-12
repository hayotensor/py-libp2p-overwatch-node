from hashlib import blake2b
import os

from scalecodec.base import RuntimeConfiguration

# Preload runtime config so we can create SCALE objects
runtime_config = RuntimeConfiguration()

def generate_overwatch_commit(weight: int, salt_len: int = 16):
    """
    Creates a commit hash for overwatch commit-reveal scheme.

    Returns:
        dict: {
            'commit_hash': str,     # Hex string for extrinsic (H256)
            'weight': int,          # Original weight (for reveal phase)
            'salt': bytes           # Salt (for reveal phase)
        }

    """
    # 1) Generate random salt
    salt = os.urandom(salt_len)

    # 2) Convert salt to list of integers for SCALE encoding
    salt_as_list = list(salt)

    # 3) SCALE encode the tuple (weight, salt)
    tuple_obj = runtime_config.create_scale_object('(u128, Vec<u8>)')
    encoded_tuple = tuple_obj.encode((weight, salt_as_list)).data

    # 4) Blake2-256 hash (32 bytes = H256)
    commit_hash_bytes = blake2b(encoded_tuple, digest_size=32).digest()

    # 5) Convert to hex string for substrate (with 0x prefix)
    commit_hash_hex = '0x' + commit_hash_bytes.hex()

    return {
        'commit_hash': commit_hash_hex,  # This goes in the extrinsic
        'weight': weight,                # Store for reveal phase
        'salt': salt                     # Store for reveal phase
    }
