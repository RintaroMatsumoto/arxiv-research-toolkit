#!/usr/bin/env python3
"""
fetch_pdf.py — Download a paper PDF and emit its extracted plain text.

Backend for the paper-summarize skill. Given an arXiv ID, a direct PDF URL,
or a local file path, this script:

  1. Resolves the PDF to bytes (download or read from disk).
  2. Extracts a plain-text rendering of the PDF.
  3. Prints the text to stdout, preceded by a small JSON header line with
     metadata (source, arxiv_id, page_count, char_count).

The summary itself is produced by Claude in its response — this script's
only job is to hand back clean text plus enough metadata for Claude to
cite pages and judge truncation.

Usage:
    python fetch_pdf.py --arxiv-id 2401.12345
    python fetch_pdf.py --url https://arxiv.org/pdf/2401.12345.pdf
    python fetch_pdf.py --path /tmp/paper.pdf
    python fetch_pdf.py --arxiv-id 2401.12345 --max-chars 60000

Exit codes:
    0  success
    1  network / IO failure
    2  argparse error (default)
    3  PDF parse failure
    4  missing optional dependency (pypdf) with actionable message
"""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Optional, Tuple

ARXIV_PDF_TEMPLATE = "https://arxiv.org/pdf/{arxiv_id}.pdf"
USER_AGENT = "arxiv-research-toolkit/0.1 (+https://github.com/RintaroMatsumoto/arxiv-research-toolkit)"
RETRY_BACKOFF_SECONDS = 3.0
DEFAULT_MAX_CHARS = 80_000  # ~20k tokens; enough for most papers, caps cost.

# Accept plain 2401.12345, 2401.12345v2, and older math.GT/0309136-style IDs.
ARXIV_ID_RE = re.compile(r"^[a-zA-Z\-\.]*\/?\d{4}\.\d{4,5}(v\d+)?$|^[a-zA-Z\-\.]+\/\d{7}(v\d+)?$")


def _fetch_bytes(url: str) -> bytes:
    """GET a URL with one retry on transient HTTP errors."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.read()
    except (urllib.error.HTTPError, urllib.error.URLError) as err:
        sys.stderr.write(
            f"pdf fetch failed ({err}); retrying in {RETRY_BACKOFF_SECONDS}s...\n"
        )
        time.sleep(RETRY_BACKOFF_SECONDS)
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.read()


def resolve_source(
    arxiv_id: Optional[str], url: Optional[str], path: Optional[str]
) -> Tuple[bytes, dict]:
    """Return (pdf_bytes, metadata_dict) from whichever source was given."""
    if arxiv_id:
        arxiv_id = arxiv_id.strip()
        if not ARXIV_ID_RE.match(arxiv_id):
            sys.stderr.write(
                f"warning: '{arxiv_id}' does not look like a canonical arXiv id; "
                "attempting anyway.\n"
            )
        pdf_url = ARXIV_PDF_TEMPLATE.format(arxiv_id=urllib.parse.quote(arxiv_id, safe="/."))
        body = _fetch_bytes(pdf_url)
        return body, {"source": "arxiv", "arxiv_id": arxiv_id, "url": pdf_url}
    if url:
        body = _fetch_bytes(url)
        return body, {"source": "url", "url": url}
    if path:
        with open(path, "rb") as fh:
            body = fh.read()
        return body, {"source": "path", "path": os.path.abspath(path)}
    raise ValueError("resolve_source called with no inputs")


def extract_text(pdf_bytes: bytes) -> Tuple[str, int]:
    """Extract text from PDF bytes. Returns (text, page_count)."""
    try:
        import pypdf  # type: ignore
    except ImportError:
        sys.stderr.write(
            "pypdf is required for paper-summarize but is not installed.\n"
            "Install it with:  pip install pypdf\n"
            "(It is a pure-Python package; no native build needed.)\n"
        )
        sys.exit(4)

    try:
        reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
    except Exception as err:  # pypdf raises various types
        sys.stderr.write(f"pypdf failed to open the PDF: {err}\n")
        sys.exit(3)

    page_count = len(reader.pages)
    chunks = []
    for i, page in enumerate(reader.pages):
        try:
            chunks.append(page.extract_text() or "")
        except Exception as err:
            sys.stderr.write(f"page {i} extraction failed ({err}); continuing.\n")
            chunks.append("")
    text = "\n\n".join(chunks)
    # Collapse excessive whitespace that pypdf often leaves behind.
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip(), page_count


def build_arg_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser."""
    p = argparse.ArgumentParser(
        prog="fetch_pdf",
        description=(
            "Fetch a paper PDF (arXiv ID, URL, or local path), extract its "
            "plain text with pypdf, and emit text to stdout preceded by a "
            "JSON metadata header line."
        ),
    )
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--arxiv-id", help="arXiv ID such as 2401.12345 or 2401.12345v2.")
    src.add_argument("--url", help="Direct PDF URL.")
    src.add_argument("--path", help="Local path to an already-downloaded PDF.")
    p.add_argument(
        "--max-chars",
        type=int,
        default=DEFAULT_MAX_CHARS,
        help=(
            f"Truncate extracted text to this many characters "
            f"(default {DEFAULT_MAX_CHARS}; 0 to disable)."
        ),
    )
    p.add_argument(
        "--header-only",
        action="store_true",
        help="Emit only the JSON metadata header (skip the body text).",
    )
    return p


def main(argv=None) -> int:
    """CLI entry point."""
    args = build_arg_parser().parse_args(argv)

    try:
        pdf_bytes, meta = resolve_source(args.arxiv_id, args.url, args.path)
    except (urllib.error.HTTPError, urllib.error.URLError, OSError) as err:
        sys.stderr.write(f"could not read PDF: {err}\n")
        return 1

    text, page_count = extract_text(pdf_bytes)
    truncated = False
    if args.max_chars and len(text) > args.max_chars:
        text = text[: args.max_chars]
        truncated = True

    header = {
        **meta,
        "page_count": page_count,
        "byte_count": len(pdf_bytes),
        "char_count": len(text),
        "truncated": truncated,
    }
    sys.stdout.write(json.dumps(header, ensure_ascii=False) + "\n")
    if not args.header_only:
        sys.stdout.write(text)
        if not text.endswith("\n"):
            sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
