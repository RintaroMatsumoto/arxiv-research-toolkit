"""Shared HTTP / SSL helpers for the arxiv-research-toolkit CLIs.

Handles three cross-cutting concerns that every skill's network code
would otherwise re-invent:

  1. **SSL trust store discovery.** On Windows, the default Python
     install ships without a trust root bundle that `urllib` can find.
     HTTPS calls to `arxiv.org`, `api.semanticscholar.org`, etc. fail
     with `CERTIFICATE_VERIFY_FAILED`. This module prefers `certifi`
     when installed, then tries a small list of common system bundles,
     and lets the user opt out with `ARXIV_TOOLKIT_INSECURE=1` as a
     last-resort escape hatch (printed with a warning).

  2. **stdout encoding.** Windows defaults to `cp932` in `ja-JP` locale,
     which chokes on Unicode JSON when scripts use `ensure_ascii=False`.
     `ensure_utf8_stdout()` flips stdout to UTF-8 in a way that works on
     Python 3.7+ and is a no-op when stdout is already UTF-8.

  3. **A common `urlopen` wrapper** with User-Agent, SSL context, and
     one-shot retry semantics that every script otherwise copy-pastes.

Stdlib-only. `certifi` is an optional enhancement, not a hard dep.
"""
from __future__ import annotations

import os
import ssl
import sys
import time
import urllib.error
import urllib.request
from typing import Optional

USER_AGENT = (
    "arxiv-research-toolkit/0.2 "
    "(+https://github.com/RintaroMatsumoto/arxiv-research-toolkit)"
)
RETRY_BACKOFF_SECONDS = 3.0

_SYSTEM_BUNDLE_CANDIDATES = (
    "/etc/ssl/certs/ca-certificates.crt",      # Debian / Ubuntu
    "/etc/pki/tls/certs/ca-bundle.crt",        # RHEL / CentOS
    "/etc/ssl/cert.pem",                       # Alpine / macOS Homebrew
    "/usr/local/etc/openssl/cert.pem",         # macOS Intel Homebrew
    "/usr/local/etc/openssl@3/cert.pem",
    "/opt/homebrew/etc/openssl@3/cert.pem",    # macOS Apple-silicon
)

_cached_ctx: Optional[ssl.SSLContext] = None


def ensure_utf8_stdout() -> None:
    """Reconfigure stdout/stderr to UTF-8. Safe to call more than once."""
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream is None:
            continue
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            pass


def _bundle_from_certifi() -> Optional[str]:
    """Return certifi's cacert.pem path, or None if certifi is absent."""
    try:
        import certifi  # type: ignore
        return certifi.where()
    except ImportError:
        return None


def _bundle_from_system() -> Optional[str]:
    """Return the first readable system trust bundle we recognize."""
    for path in _SYSTEM_BUNDLE_CANDIDATES:
        if os.path.isfile(path) and os.access(path, os.R_OK):
            return path
    return None


def ssl_context() -> ssl.SSLContext:
    """Return a process-wide SSLContext with a best-effort trust store.

    Priority: certifi → explicit `ARXIV_TOOLKIT_CA_BUNDLE` env var →
    OS-level bundle → Python default. Final escape hatch:
    `ARXIV_TOOLKIT_INSECURE=1` disables verification with a stderr
    warning (use only for debugging — *do not commit data downloaded
    this way*).
    """
    global _cached_ctx
    if _cached_ctx is not None:
        return _cached_ctx

    if os.environ.get("ARXIV_TOOLKIT_INSECURE") == "1":
        sys.stderr.write(
            "WARNING: ARXIV_TOOLKIT_INSECURE=1 is set. TLS verification "
            "is disabled. Do not commit any data fetched in this mode.\n"
        )
        ctx = ssl._create_unverified_context()
        _cached_ctx = ctx
        return ctx

    ctx = ssl.create_default_context()

    bundle = (
        os.environ.get("ARXIV_TOOLKIT_CA_BUNDLE")
        or _bundle_from_certifi()
        or _bundle_from_system()
    )
    if bundle:
        try:
            ctx.load_verify_locations(cafile=bundle)
        except (ssl.SSLError, OSError) as err:
            sys.stderr.write(
                f"warning: could not load CA bundle {bundle!r}: {err}\n"
            )

    _cached_ctx = ctx
    return ctx


def open_url(
    url: str, *, timeout: float = 30.0, extra_headers: Optional[dict] = None
):
    """urlopen with User-Agent and the shared SSL context. Returns the response."""
    headers = {"User-Agent": USER_AGENT}
    if extra_headers:
        headers.update(extra_headers)
    req = urllib.request.Request(url, headers=headers)
    return urllib.request.urlopen(req, timeout=timeout, context=ssl_context())


def fetch_bytes(
    url: str,
    *,
    timeout: float = 30.0,
    retries: int = 1,
    backoff: float = RETRY_BACKOFF_SECONDS,
    extra_headers: Optional[dict] = None,
) -> bytes:
    """GET a URL and return bytes. Retries on transient errors.

    ``retries`` controls total retry count. On HTTP 429 (rate limited)
    we honour a ``Retry-After`` header if present and otherwise apply
    exponential backoff (``backoff``, 2×, 4×, …). Transient network
    errors and 5xx use a fixed backoff.

    Non-retryable 4xx (other than 429) raise after the first attempt.
    """
    wait = backoff
    for attempt in range(retries + 1):
        try:
            with open_url(url, timeout=timeout, extra_headers=extra_headers) as resp:
                return resp.read()
        except urllib.error.HTTPError as err:
            is_transient = err.code == 429 or 500 <= err.code < 600
            if attempt >= retries or not is_transient:
                raise
            sleep_for = wait
            if err.code == 429:
                retry_after = err.headers.get("Retry-After") if err.headers else None
                try:
                    if retry_after:
                        sleep_for = max(sleep_for, float(retry_after))
                except (TypeError, ValueError):
                    pass
            sys.stderr.write(
                f"fetch got HTTP {err.code}; retrying in {sleep_for:.1f}s...\n"
            )
            time.sleep(sleep_for)
            wait *= 2
        except urllib.error.URLError as err:
            if attempt >= retries:
                raise
            sys.stderr.write(
                f"fetch failed ({err}); retrying in {wait:.1f}s...\n"
            )
            time.sleep(wait)
            wait *= 2
    # Unreachable but keeps type-checkers happy.
    raise RuntimeError("fetch_bytes exhausted retries without raising")
