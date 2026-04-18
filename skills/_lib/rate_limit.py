"""File-based token bucket for cross-process HTTP rate limiting.

Public APIs like arXiv and Semantic Scholar publish soft rate limits
(arXiv: ~1 req/3s per client; Semantic Scholar: 1 req/s unauth, higher
with an API key). Multiple skills in this toolkit call the same hosts,
and a user may run several skills back-to-back, so the limiter has to
share state across *processes*, not just within one script.

Design — stdlib-only, no daemon:

  * One state file per host at ``<cache_root>/_rate/<host>.json``.
  * Token bucket: ``{capacity, tokens, rate_per_sec, updated_at}``.
  * ``acquire(host)`` atomically refills, debits one token, and sleeps
    the shortfall with ``fcntl.flock`` (POSIX) or ``msvcrt.locking``
    (Windows) to serialize concurrent callers.
  * Silent passthrough on filesystem / lock errors — the limiter is a
    politeness mechanism, not a correctness one.

Per-host defaults are conservative; callers can override:

    acquire("export.arxiv.org")            # 1 req / 3 s
    acquire("api.semanticscholar.org")     # 1 req / 1 s
    acquire("eutils.ncbi.nlm.nih.gov")     # 3 req / 1 s (NCBI guidance)

Environment overrides:
  ARXIV_TOOLKIT_NO_RATE_LIMIT=1 — disable sleeping entirely (tests).
"""
from __future__ import annotations

import json
import os
import sys
import time
from typing import Dict, Tuple

from . import cache as _cache  # reuse cache_root() for the state dir

# Per-host defaults: (capacity, refill_rate_tokens_per_sec).
# capacity == burst allowance; rate == steady-state budget.
#
# Semantic Scholar's free tier is far stricter than its docs imply —
# we see 429s even at 1 req/s. The conservative default here is one
# request every ~3.5 s (~100 per 5 min), which matches their
# "100 requests per 5 minutes" public guideline. Users with an API key
# can bypass this by setting ARXIV_TOOLKIT_NO_RATE_LIMIT=1.
_HOST_DEFAULTS: Dict[str, Tuple[float, float]] = {
    "export.arxiv.org":         (1.0, 1.0 / 3.0),   # arXiv: 1 per 3s
    "arxiv.org":                (1.0, 1.0 / 3.0),
    "api.semanticscholar.org":  (1.0, 1.0 / 3.5),   # S2 unauth: ~1 per 3.5s
    "eutils.ncbi.nlm.nih.gov":  (3.0, 3.0),         # NCBI: 3/s no key
    "api.crossref.org":         (5.0, 5.0),         # polite pool
    "api.zotero.org":           (5.0, 5.0),         # Zotero Web API
}

_DEFAULT_BUCKET = (1.0, 1.0)  # conservative fallback: 1 req/s


def is_disabled() -> bool:
    """Return True if rate limiting is globally disabled."""
    return os.environ.get("ARXIV_TOOLKIT_NO_RATE_LIMIT") == "1"


def _state_dir() -> str:
    """Directory that holds per-host bucket state files."""
    return os.path.join(_cache.cache_root(), "_rate")


def _state_path(host: str) -> str:
    """Filesystem path for this host's bucket state file."""
    safe = host.replace(":", "_").replace("/", "_")
    return os.path.join(_state_dir(), f"{safe}.json")


def _defaults_for(host: str) -> Tuple[float, float]:
    """Return (capacity, rate_per_sec) for a host; fall back sensibly."""
    return _HOST_DEFAULTS.get(host, _DEFAULT_BUCKET)


# -----------------------------------------------------------------------------
# Cross-platform file locking
# -----------------------------------------------------------------------------

def _lock(fh) -> None:
    """Acquire an exclusive lock on an open file handle (best-effort)."""
    try:
        import fcntl  # POSIX
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
    except ImportError:
        try:
            import msvcrt  # Windows
            msvcrt.locking(fh.fileno(), msvcrt.LK_LOCK, 1)
        except (ImportError, OSError):
            pass
    except OSError:
        pass


def _unlock(fh) -> None:
    """Release a previously acquired lock (best-effort)."""
    try:
        import fcntl
        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    except ImportError:
        try:
            import msvcrt
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
        except (ImportError, OSError):
            pass
    except OSError:
        pass


# -----------------------------------------------------------------------------
# Public API
# -----------------------------------------------------------------------------

def acquire(host: str, *, tokens: float = 1.0) -> None:
    """Block until a token is available for ``host`` (politeness gate).

    On any filesystem or lock failure, returns immediately without sleeping.
    The limiter is a soft politeness layer, not a correctness gate.
    """
    if is_disabled():
        return

    capacity, rate = _defaults_for(host)
    state_path = _state_path(host)

    try:
        os.makedirs(os.path.dirname(state_path), exist_ok=True)
    except OSError:
        return

    # Open for read+write, creating if missing. We hold the lock across
    # read → refill → debit → write so concurrent callers serialize.
    try:
        fh = open(state_path, "a+", encoding="utf-8")
    except OSError:
        return

    try:
        _lock(fh)
        fh.seek(0)
        raw = fh.read()
        now = time.time()
        if raw.strip():
            try:
                state = json.loads(raw)
            except json.JSONDecodeError:
                state = {}
        else:
            state = {}

        bucket_tokens = float(state.get("tokens", capacity))
        updated_at = float(state.get("updated_at", now))

        # Refill based on elapsed time.
        elapsed = max(0.0, now - updated_at)
        bucket_tokens = min(capacity, bucket_tokens + elapsed * rate)

        if bucket_tokens >= tokens:
            bucket_tokens -= tokens
            sleep_for = 0.0
        else:
            # Wait long enough to accumulate the shortfall.
            shortfall = tokens - bucket_tokens
            sleep_for = shortfall / rate if rate > 0 else 0.0
            bucket_tokens = 0.0
            now = now + sleep_for  # advance logical clock

        new_state = {
            "host": host,
            "capacity": capacity,
            "rate_per_sec": rate,
            "tokens": bucket_tokens,
            "updated_at": now,
        }
        fh.seek(0)
        fh.truncate()
        fh.write(json.dumps(new_state, sort_keys=True))
        fh.flush()
    except OSError as err:
        sys.stderr.write(f"rate-limit state write skipped: {err}\n")
        sleep_for = 0.0
    finally:
        try:
            _unlock(fh)
        except Exception:
            pass
        try:
            fh.close()
        except Exception:
            pass

    if sleep_for > 0:
        time.sleep(sleep_for)


def host_of(url: str) -> str:
    """Return the hostname component of ``url`` (lowercased, no port)."""
    from urllib.parse import urlparse
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    return host
