"""File-based cache for skill HTTP responses.

Minimal, stdlib-only. Keyed by sha256 of `(source, normalized_key,
sorted_filters)`. Values are raw response bodies (bytes) plus a JSON
sidecar with metadata (created_at, ttl_seconds, source, key).

Layout:

    ~/.cache/arxiv-research-toolkit/<source>/<sha256>.bin
    ~/.cache/arxiv-research-toolkit/<source>/<sha256>.json

TTL is per-source:
  - search results: 24 h (search_ttl)
  - paper metadata: 7 d (metadata_ttl)
  - reference lists of a fixed paper: no expiry (ttl=0)

The cache silently passes through (miss + fetch + store) on any
filesystem error — it is an optimization, not a source of truth.

Environment overrides:
  ARXIV_TOOLKIT_CACHE_DIR — override the cache root path.
  ARXIV_TOOLKIT_NO_CACHE=1 — disable reads AND writes globally.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from typing import Any, Callable, Dict, Optional, Tuple

CACHE_VERSION = 1  # bump to invalidate on schema changes
DEFAULT_CACHE_ROOT = os.path.join(
    os.path.expanduser("~"), ".cache", "arxiv-research-toolkit"
)


def is_disabled() -> bool:
    """Return True if the global kill-switch env var is set."""
    return os.environ.get("ARXIV_TOOLKIT_NO_CACHE") == "1"


def cache_root() -> str:
    """Return the configured cache root directory (env var wins)."""
    return os.environ.get("ARXIV_TOOLKIT_CACHE_DIR") or DEFAULT_CACHE_ROOT


def _hash_key(source: str, key: str, filters: Optional[Dict[str, Any]]) -> str:
    """Deterministic sha256 over (source, key, sorted filters)."""
    payload = {
        "v": CACHE_VERSION,
        "source": source,
        "key": key,
        "filters": {k: filters[k] for k in sorted(filters or {})} if filters else {},
    }
    blob = json.dumps(payload, ensure_ascii=True, sort_keys=True).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def _paths(source: str, digest: str) -> Tuple[str, str]:
    """Return the (body_path, meta_path) for a given (source, digest)."""
    folder = os.path.join(cache_root(), source)
    return (
        os.path.join(folder, f"{digest}.bin"),
        os.path.join(folder, f"{digest}.json"),
    )


def _is_fresh(meta: Dict[str, Any]) -> bool:
    """Return True if the cached record is still within its TTL."""
    ttl = float(meta.get("ttl_seconds", 0) or 0)
    if ttl == 0:
        return True  # 0 = no expiry (immutable reference lists).
    created = float(meta.get("created_at", 0))
    return (time.time() - created) < ttl


def get(
    source: str, key: str, filters: Optional[Dict[str, Any]] = None
) -> Optional[bytes]:
    """Return cached bytes for (source, key, filters) or None on miss/expiry."""
    if is_disabled():
        return None
    digest = _hash_key(source, key, filters)
    body_path, meta_path = _paths(source, digest)
    try:
        with open(meta_path, "r", encoding="utf-8") as fh:
            meta = json.load(fh)
        if not _is_fresh(meta):
            return None
        with open(body_path, "rb") as fh:
            return fh.read()
    except (OSError, json.JSONDecodeError):
        return None


def put(
    source: str,
    key: str,
    body: bytes,
    *,
    ttl_seconds: float = 86400.0,
    filters: Optional[Dict[str, Any]] = None,
) -> None:
    """Store `body` under (source, key, filters). Silent on filesystem errors."""
    if is_disabled():
        return
    digest = _hash_key(source, key, filters)
    body_path, meta_path = _paths(source, digest)
    folder = os.path.dirname(body_path)
    try:
        os.makedirs(folder, exist_ok=True)
        with open(body_path, "wb") as fh:
            fh.write(body)
        meta = {
            "version": CACHE_VERSION,
            "source": source,
            "key": key,
            "filters": filters or {},
            "ttl_seconds": ttl_seconds,
            "created_at": time.time(),
            "size_bytes": len(body),
        }
        with open(meta_path, "w", encoding="utf-8") as fh:
            json.dump(meta, fh, ensure_ascii=True, sort_keys=True)
    except OSError as err:
        sys.stderr.write(f"cache write skipped: {err}\n")


def memoized(
    source: str,
    key: str,
    fetcher: Callable[[], bytes],
    *,
    ttl_seconds: float = 86400.0,
    filters: Optional[Dict[str, Any]] = None,
) -> bytes:
    """Return cached bytes if fresh; otherwise call fetcher() and cache the result."""
    hit = get(source, key, filters)
    if hit is not None:
        return hit
    body = fetcher()
    put(source, key, body, ttl_seconds=ttl_seconds, filters=filters)
    return body
