from __future__ import annotations

import os
import secrets
from collections.abc import Iterable


def load_api_keys() -> frozenset[str]:
    """Valid agent API keys from $PULSE_API_KEYS (comma-separated)."""
    raw = os.environ.get("PULSE_API_KEYS", "")
    return frozenset(key.strip() for key in raw.split(",") if key.strip())


def extract_bearer_token(header: str | None) -> str | None:
    if not header:
        return None
    scheme, _, token = header.partition(" ")
    token = token.strip()
    if scheme.lower() != "bearer" or not token:
        return None
    return token


def is_valid_key(token: str, valid_keys: Iterable[str]) -> bool:
    """Constant-time comparison against every configured key."""
    candidate = token.encode()
    matched = False
    for key in valid_keys:
        if secrets.compare_digest(candidate, key.encode()):
            matched = True
    return matched
