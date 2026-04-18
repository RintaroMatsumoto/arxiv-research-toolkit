#!/usr/bin/env python3
"""
zotero_export.py — Export a paper-search JSON array to Zotero and/or BibTeX.

Backend for the zotero-export skill. Given the canonical JSON produced by
paper-search (or any list with the same record shape), this script can
send records to Zotero via two transports, plus a BibTeX fallback:

  1. **Local connector** (default) — probes the Zotero desktop app at
     ``http://127.0.0.1:23119``. Works when Zotero is running on the
     same machine. No credentials required. Supports ``--collection``.

  2. **Web API** (``--api-key`` + ``--user-id`` or ``--group-id``) —
     uses ``https://api.zotero.org``. Works headlessly, no running
     Zotero required. Supports ``--collection`` and ``--attach-pdfs``.

  3. **BibTeX** — always writes a ``.bib`` file unless ``--zotero-only``
     is set. This is the offline-safe fallback.

Stdlib-only (urllib + json). No pip installs required.

Usage:
    # Local connector (Zotero desktop is running)
    python zotero_export.py --input results.json

    # Local connector, dropped into a collection by its 8-char key
    python zotero_export.py --input results.json --collection AB12CD34

    # Web API, headless
    python zotero_export.py --input results.json \\
        --api-key $ZOTERO_API_KEY --user-id 1234567 --collection AB12CD34

    # Web API, attach open-access PDFs as child links
    python zotero_export.py --input results.json \\
        --api-key $ZOTERO_API_KEY --user-id 1234567 --attach-pdfs

    # Offline: only write BibTeX
    python zotero_export.py --input results.json --bib-only

Exit codes:
    0  success (at least one output channel succeeded)
    1  IO / decode failure
    2  argparse error (default)
    3  all output channels failed

Shared helpers from ``skills/_lib/``: net, rate_limit — see
``search_arxiv.py`` for the rationale.
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import pathlib
import re
import sys
import unicodedata
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from _lib import net, rate_limit  # noqa: E402

ZOTERO_PING = "http://127.0.0.1:23119/connector/ping"
ZOTERO_SAVE = "http://127.0.0.1:23119/connector/saveItems"
ZOTERO_WEB_BASE = "https://api.zotero.org"


# -----------------------------------------------------------------------------
# Shared: cite keys, BibTeX, author parsing
# -----------------------------------------------------------------------------

def _strip_accents(s: str) -> str:
    """Fold accented characters for BibTeX-safe cite keys."""
    return "".join(
        c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c)
    )


def _author_name_parts(name: str) -> Dict[str, str]:
    """Split 'First Last' or 'Last, First' into Zotero creator parts."""
    if "," in name:
        last, first = [p.strip() for p in name.split(",", 1)]
    else:
        pieces = name.strip().split()
        if len(pieces) == 1:
            return {"creatorType": "author", "lastName": pieces[0], "firstName": ""}
        last = pieces[-1]
        first = " ".join(pieces[:-1])
    return {"creatorType": "author", "lastName": last, "firstName": first}


def to_zotero_item(
    rec: Dict[str, Any], collection: Optional[str] = None
) -> Dict[str, Any]:
    """Map a canonical paper record to a Zotero item (connector + Web API shape).

    The connector and Web API both accept roughly the same item template;
    the differences (version, key) are filled in on the server side.
    """
    item_type = "preprint" if rec.get("source") == "arxiv" else "journalArticle"
    creators = [_author_name_parts(a) for a in rec.get("authors", []) if a]
    tags = [{"tag": t} for t in rec.get("categories", []) if t]
    item: Dict[str, Any] = {
        "itemType": item_type,
        "title": rec.get("title", ""),
        "creators": creators,
        "abstractNote": rec.get("abstract", ""),
        "url": rec.get("url", ""),
        "tags": tags,
    }
    year = rec.get("year")
    if year:
        item["date"] = str(year)
    if rec.get("venue"):
        item["publicationTitle"] = rec["venue"]
    if rec.get("source") == "arxiv" and rec.get("id"):
        item["archiveID"] = f"arXiv:{rec['id']}"
        item["repository"] = "arXiv"
    if rec.get("doi"):
        item["DOI"] = rec["doi"]
    if collection:
        item["collections"] = [collection]
    return item


def _cite_key(rec: Dict[str, Any], taken: set) -> str:
    """BibTeX-friendly cite key — matches lit-review-draft's rules."""
    authors = rec.get("authors") or []
    if authors:
        first = authors[0]
        surname = first.split(",", 1)[0].strip() if "," in first else first.split()[-1]
    else:
        surname = "Unknown"
    surname = re.sub(r"[^A-Za-z0-9]", "", _strip_accents(surname)) or "Unknown"
    year = rec.get("year") or "nd"
    words = re.findall(r"[A-Za-z0-9]+", _strip_accents(rec.get("title", "")).lower())
    words = [w for w in words if w not in {"a", "an", "the", "of", "on", "for", "and"}]
    slug = "".join(words[:3]) or "paper"
    base = f"{surname}{year}{slug}"
    key = base
    suffix = ord("a")
    while key in taken:
        key = f"{base}{chr(suffix)}"
        suffix += 1
    taken.add(key)
    return key


def _bib_escape(s: str) -> str:
    """Minimal BibTeX escaping — curly brace the hot characters."""
    if not s:
        return ""
    return (
        s.replace("\\", "\\textbackslash{}")
        .replace("&", r"\&")
        .replace("%", r"\%")
        .replace("$", r"\$")
        .replace("#", r"\#")
        .replace("_", r"\_")
    )


def to_bibtex(rec: Dict[str, Any], cite_key: str) -> str:
    """Emit a single BibTeX entry for a paper record."""
    entry_type = "misc" if rec.get("source") == "arxiv" else "article"
    fields: List[str] = []
    if rec.get("title"):
        fields.append(f"  title = {{{_bib_escape(rec['title'])}}}")
    if rec.get("authors"):
        authors_str = " and ".join(_bib_escape(a) for a in rec["authors"] if a)
        fields.append(f"  author = {{{authors_str}}}")
    if rec.get("year"):
        fields.append(f"  year = {{{rec['year']}}}")
    if rec.get("source") == "arxiv" and rec.get("id"):
        fields.append(f"  eprint = {{{rec['id']}}}")
        fields.append("  archivePrefix = {arXiv}")
    if rec.get("venue") and rec.get("source") != "arxiv":
        fields.append(f"  journal = {{{_bib_escape(rec['venue'])}}}")
    if rec.get("url"):
        fields.append(f"  url = {{{rec['url']}}}")
    if rec.get("doi"):
        fields.append(f"  doi = {{{rec['doi']}}}")
    body = ",\n".join(fields)
    return f"@{entry_type}{{{cite_key},\n{body}\n}}"


def write_bib(records: List[Dict[str, Any]], out_path: str) -> int:
    """Write the paper list as a .bib file; returns the number of entries."""
    taken: set = set()
    entries = []
    for rec in records:
        key = _cite_key(rec, taken)
        entries.append(to_bibtex(rec, key))
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write("% arxiv-research-toolkit — zotero-export BibTeX output\n")
        fh.write(f"% generated: {datetime.datetime.utcnow().isoformat(timespec='seconds')}Z\n")
        fh.write(f"% entries:   {len(entries)}\n\n")
        fh.write("\n\n".join(entries))
        fh.write("\n")
    return len(entries)


def default_bib_path(input_path: Optional[str]) -> str:
    """Choose a default .bib output path next to the input, or in cwd."""
    stamp = datetime.datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    if input_path:
        stem, _ = os.path.splitext(os.path.abspath(input_path))
        return f"{stem}.bib"
    return os.path.abspath(f"./arxiv-research-toolkit-{stamp}.bib")


# -----------------------------------------------------------------------------
# Zotero local connector mode
# -----------------------------------------------------------------------------

def ping_zotero_connector() -> bool:
    """Return True if the Zotero connector is reachable on localhost."""
    try:
        with net.open_url(ZOTERO_PING, timeout=3) as resp:
            return 200 <= resp.status < 300
    except (urllib.error.URLError, urllib.error.HTTPError, OSError):
        return False


def send_via_connector(
    items: List[Dict[str, Any]], collection: Optional[str]
) -> Optional[str]:
    """POST items to the local Zotero connector. Returns error string or None."""
    payload: Dict[str, Any] = {"items": items}
    if collection:
        # The connector accepts a top-level "collection" hint in newer builds.
        payload["collection"] = collection
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        ZOTERO_SAVE,
        data=data,
        headers={
            "Content-Type": "application/json",
            "User-Agent": net.USER_AGENT,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            if 200 <= resp.status < 300:
                return None
            return f"Zotero returned HTTP {resp.status}"
    except urllib.error.HTTPError as err:
        try:
            body = err.read().decode("utf-8", errors="replace")
        except Exception:
            body = ""
        return f"Zotero HTTP error {err.code}: {body[:200]}"
    except urllib.error.URLError as err:
        return f"Zotero connection error: {err}"


# -----------------------------------------------------------------------------
# Zotero Web API mode
# -----------------------------------------------------------------------------

def _web_api_base(user_id: Optional[str], group_id: Optional[str]) -> str:
    """Return the library root URL for the user's or group's library."""
    if group_id:
        return f"{ZOTERO_WEB_BASE}/groups/{group_id}"
    if user_id:
        return f"{ZOTERO_WEB_BASE}/users/{user_id}"
    raise ValueError("Web API mode requires --user-id or --group-id")


def _web_api_request(
    url: str,
    *,
    api_key: str,
    method: str = "GET",
    body: Optional[bytes] = None,
    extra_headers: Optional[Dict[str, str]] = None,
    timeout: float = 30.0,
) -> Tuple[int, bytes, Dict[str, str]]:
    """Minimal Zotero Web API request helper. Returns (status, body, headers)."""
    rate_limit.acquire("api.zotero.org")
    headers = {
        "Zotero-API-Version": "3",
        "Authorization": f"Bearer {api_key}",
        "User-Agent": net.USER_AGENT,
    }
    if extra_headers:
        headers.update(extra_headers)
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(
            req, timeout=timeout, context=net.ssl_context()
        ) as resp:
            return resp.status, resp.read(), dict(resp.headers)
    except urllib.error.HTTPError as err:
        try:
            payload = err.read()
        except Exception:
            payload = b""
        return err.code, payload, dict(err.headers or {})


def send_via_web_api(
    items: List[Dict[str, Any]],
    *,
    api_key: str,
    user_id: Optional[str],
    group_id: Optional[str],
) -> Tuple[Optional[str], List[str]]:
    """Create items through the Zotero Web API.

    Zotero caps a single write to 50 items, so we chunk. Returns
    ``(error_message_or_None, created_item_keys)``.
    """
    base = _web_api_base(user_id, group_id)
    items_url = f"{base}/items"
    created_keys: List[str] = []

    for start in range(0, len(items), 50):
        chunk = items[start:start + 50]
        data = json.dumps(chunk).encode("utf-8")
        status, body, _ = _web_api_request(
            items_url,
            api_key=api_key,
            method="POST",
            body=data,
            extra_headers={"Content-Type": "application/json"},
        )
        if status >= 400:
            return (
                f"Zotero Web API HTTP {status}: {body[:200].decode('utf-8', 'replace')}",
                created_keys,
            )
        try:
            parsed = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return ("Zotero Web API returned non-JSON response", created_keys)
        # Response shape: {"successful": {"0": {...,"key": "ABC12345"}}, "failed": ...}
        successful = parsed.get("successful") or {}
        for _idx, itm in successful.items():
            key = (itm or {}).get("key") or (itm.get("data") or {}).get("key")
            if key:
                created_keys.append(key)
        failed = parsed.get("failed") or {}
        if failed:
            sys.stderr.write(
                f"Zotero Web API: {len(failed)} item(s) rejected in chunk "
                f"starting at {start}: {json.dumps(failed)[:300]}\n"
            )
    return None, created_keys


def attach_linked_pdfs(
    parent_keys: List[str],
    records: List[Dict[str, Any]],
    *,
    api_key: str,
    user_id: Optional[str],
    group_id: Optional[str],
) -> Tuple[int, int]:
    """Create ``linked_url`` attachment children pointing at each paper's PDF.

    We use ``linked_url`` rather than ``imported_url`` to keep this
    stdlib-only: linked_url stores a hyperlink in Zotero without
    uploading the actual file, so there is no multipart / md5 /
    storage-auth dance. Users who want the PDF bytes can click through
    or run Zotero's "Attach Stored Copy of File" manually.

    Returns ``(attempted, succeeded)``.
    """
    base = _web_api_base(user_id, group_id)
    items_url = f"{base}/items"
    attempted = 0
    succeeded = 0
    batch: List[Dict[str, Any]] = []

    def _flush() -> None:
        nonlocal succeeded
        if not batch:
            return
        data = json.dumps(batch).encode("utf-8")
        status, body, _ = _web_api_request(
            items_url,
            api_key=api_key,
            method="POST",
            body=data,
            extra_headers={"Content-Type": "application/json"},
        )
        if 200 <= status < 300:
            try:
                parsed = json.loads(body.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                parsed = {}
            succeeded += len((parsed or {}).get("successful") or {})
        else:
            sys.stderr.write(
                f"attach batch failed HTTP {status}: "
                f"{body[:200].decode('utf-8', 'replace')}\n"
            )
        batch.clear()

    for key, rec in zip(parent_keys, records):
        pdf_url = rec.get("pdf_url") or ""
        if not pdf_url:
            continue
        attempted += 1
        batch.append({
            "itemType": "attachment",
            "parentItem": key,
            "linkMode": "linked_url",
            "title": "Full Text PDF",
            "url": pdf_url,
            "contentType": "application/pdf",
        })
        if len(batch) >= 50:
            _flush()
    _flush()
    return attempted, succeeded


# -----------------------------------------------------------------------------
# Input loading + CLI
# -----------------------------------------------------------------------------

def load_input(path: Optional[str], stdin: bool) -> List[Dict[str, Any]]:
    """Load the paper list from a JSON file or stdin."""
    if stdin:
        raw = sys.stdin.read()
    else:
        if not path:
            raise ValueError("either --input or --stdin is required")
        with open(path, "r", encoding="utf-8") as fh:
            raw = fh.read()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as err:
        sys.stderr.write(f"input is not valid JSON: {err}\n")
        sys.exit(1)
    if isinstance(data, dict) and "papers" in data:
        data = data["papers"]
    if not isinstance(data, list):
        sys.stderr.write("input JSON must be an array of paper records.\n")
        sys.exit(1)
    return data


def build_arg_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser."""
    p = argparse.ArgumentParser(
        prog="zotero_export",
        description=(
            "Export a paper-search JSON array to the Zotero local connector "
            "or the Zotero Web API, with a BibTeX fallback. Stdlib-only."
        ),
    )
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--input", help="Path to a JSON file of paper records.")
    src.add_argument("--stdin", action="store_true", help="Read JSON from stdin.")

    p.add_argument(
        "--bib-out",
        default=None,
        help=(
            "Path to write BibTeX. Defaults to the input file's stem + '.bib', "
            "or a timestamped file in the current directory when reading stdin."
        ),
    )
    p.add_argument(
        "--bib-only",
        action="store_true",
        help="Skip Zotero entirely; only write BibTeX.",
    )
    p.add_argument(
        "--zotero-only",
        action="store_true",
        help="Skip BibTeX; only write to Zotero.",
    )

    # Web API options.
    p.add_argument(
        "--api-key",
        default=os.environ.get("ZOTERO_API_KEY"),
        help=(
            "Zotero Web API key. Supplying this enables Web API mode "
            "(instead of probing the local connector). Can also come from "
            "$ZOTERO_API_KEY."
        ),
    )
    p.add_argument(
        "--user-id",
        default=os.environ.get("ZOTERO_USER_ID"),
        help=(
            "Zotero userID for Web API mode. Find it on "
            "https://www.zotero.org/settings/keys. "
            "Mutually exclusive with --group-id."
        ),
    )
    p.add_argument(
        "--group-id",
        default=None,
        help=(
            "Zotero groupID for Web API mode (write to a group library "
            "instead of your personal library). Mutually exclusive with "
            "--user-id."
        ),
    )
    p.add_argument(
        "--collection",
        default=None,
        help=(
            "Zotero collection key (8-char alphanumeric) to drop new items "
            "into. Works for both the local connector and the Web API."
        ),
    )
    p.add_argument(
        "--attach-pdfs",
        action="store_true",
        help=(
            "Web API mode only: for each created item, create a linked-URL "
            "child attachment pointing at the paper's PDF (from the record's "
            "pdf_url field)."
        ),
    )
    return p


def _validate_args(args: argparse.Namespace) -> Optional[str]:
    """Return an error string if argument combination is invalid; else None."""
    if args.bib_only and args.zotero_only:
        return "--bib-only and --zotero-only are mutually exclusive."
    if args.user_id and args.group_id:
        return "--user-id and --group-id are mutually exclusive."
    if args.api_key and not (args.user_id or args.group_id):
        return "--api-key requires --user-id or --group-id."
    if args.attach_pdfs and not args.api_key:
        return "--attach-pdfs requires Web API mode (pass --api-key)."
    return None


def main(argv=None) -> int:
    """CLI entry point."""
    net.ensure_utf8_stdout()
    args = build_arg_parser().parse_args(argv)

    err = _validate_args(args)
    if err:
        sys.stderr.write(err + "\n")
        return 2

    records = load_input(args.input, args.stdin)
    if not records:
        sys.stderr.write("input contained zero paper records; nothing to export.\n")
        return 1

    report: Dict[str, Any] = {"paper_count": len(records)}
    any_success = False

    # Zotero leg.
    if not args.bib_only:
        items = [to_zotero_item(r, collection=args.collection) for r in records]

        if args.api_key:
            # Web API mode.
            web_err, created_keys = send_via_web_api(
                items,
                api_key=args.api_key,
                user_id=args.user_id,
                group_id=args.group_id,
            )
            zotero_report: Dict[str, Any] = {
                "mode": "web_api",
                "items_sent": len(items),
                "items_created": len(created_keys),
                "collection": args.collection,
            }
            if web_err is None:
                zotero_report["status"] = "ok"
                any_success = True
                if args.attach_pdfs:
                    attempted, succeeded = attach_linked_pdfs(
                        created_keys,
                        records[: len(created_keys)],
                        api_key=args.api_key,
                        user_id=args.user_id,
                        group_id=args.group_id,
                    )
                    zotero_report["attachments_attempted"] = attempted
                    zotero_report["attachments_created"] = succeeded
            else:
                zotero_report["status"] = "error"
                zotero_report["message"] = web_err
            report["zotero"] = zotero_report
        else:
            # Local connector mode.
            if ping_zotero_connector():
                c_err = send_via_connector(items, args.collection)
                if c_err is None:
                    report["zotero"] = {
                        "mode": "connector",
                        "status": "ok",
                        "items_sent": len(items),
                        "collection": args.collection,
                    }
                    any_success = True
                else:
                    report["zotero"] = {
                        "mode": "connector",
                        "status": "error",
                        "message": c_err,
                    }
            else:
                report["zotero"] = {
                    "mode": "connector",
                    "status": "unreachable",
                    "message": (
                        "Zotero connector on 127.0.0.1:23119 did not respond. "
                        "Open Zotero desktop (6.x+), or use Web API mode "
                        "(--api-key + --user-id), or use --bib-only."
                    ),
                }

    # BibTeX leg.
    if not args.zotero_only:
        bib_path = args.bib_out or default_bib_path(args.input)
        try:
            entry_count = write_bib(records, bib_path)
            report["bibtex"] = {
                "status": "ok",
                "path": os.path.abspath(bib_path),
                "entry_count": entry_count,
            }
            any_success = True
        except OSError as oerr:
            report["bibtex"] = {"status": "error", "message": str(oerr)}

    sys.stdout.write(json.dumps(report, ensure_ascii=False, indent=2))
    sys.stdout.write("\n")
    return 0 if any_success else 3


if __name__ == "__main__":
    sys.exit(main())
