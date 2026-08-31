"""
Simple in-memory TTL cache for parsed profile responses, keyed by LinkedIn
public identifier. Avoids re-hitting LinkedIn (and burning rate-limit
budget / raising ban risk) for repeat lookups within the TTL window.

Process-local only — fine for a single-instance POC. A multi-instance
deployment would want this backed by Redis instead.
"""

from typing import Optional

from cachetools import TTLCache

from . import config

_cache: TTLCache = TTLCache(maxsize=config.CACHE_MAX_SIZE, ttl=config.CACHE_TTL_SECONDS)


def get(public_identifier: str) -> Optional[dict]:
    return _cache.get(public_identifier)


def set(public_identifier: str, parsed_profile: dict) -> None:
    _cache[public_identifier] = parsed_profile


def stats() -> dict:
    return {"size": len(_cache), "max_size": _cache.maxsize, "ttl_seconds": _cache.ttl}


def clear() -> None:
    """Clear cached profiles. Primarily useful for tests and operations."""
    _cache.clear()
