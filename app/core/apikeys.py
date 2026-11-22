"""API key format and hashing (docs/API.md, "Общие правила v2", stage 20).

A key is ``si_`` plus 32 base62 characters (about 190 bits from ``secrets``). The database
stores only the SHA-256 of the key and a display prefix; the key itself is shown once, at
issue. A plain fast hash is enough because the key is random and long: guessing it from the
hash is out of reach, and a slow hash would make every cache miss expensive.
"""

import hashlib
import re
import secrets
import string

KEY_PREFIX = "si_"
KEY_BODY_LENGTH = 32
# Characters of the key shown in lists and searchable by admin: ``si_`` plus 8.
DISPLAY_PREFIX_LENGTH = len(KEY_PREFIX) + 8

_ALPHABET = string.ascii_letters + string.digits

# Fixed-width pattern, anchored: linear on any input.
API_KEY_RE = re.compile(rf"si_[A-Za-z0-9]{{{KEY_BODY_LENGTH}}}")


def generate_api_key() -> str:
    return KEY_PREFIX + "".join(secrets.choice(_ALPHABET) for _ in range(KEY_BODY_LENGTH))


def hash_api_key(key: str) -> bytes:
    """SHA-256 of the key (32 bytes), the value of ``api_keys.key_hash``."""
    return hashlib.sha256(key.encode("utf-8")).digest()


def is_our_key_format(value: str) -> bool:
    """True for a string of our key format; other values (api.nasa.gov keys, ``DEMO_KEY``)
    are not ours. Case matters: ``SI_...`` is not our format."""
    return API_KEY_RE.fullmatch(value) is not None


def key_prefix(key: str) -> str:
    return key[:DISPLAY_PREFIX_LENGTH]
