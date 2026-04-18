#!/usr/bin/env python3
"""
zotero_export.py — Export a paper-search JSON array to Zotero and/or BibTeX.

Backend for the zotero-export skill. Given the canonical JSON produced by
paper-search (or any list with the same record shape), this script:

  1. Probes the local Zotero connector at http://127.0.0.1:23119/connector/ping.
  2. If Zotero is running, POSTs the paper set as items via the connector
     endpoint. Writes the session count and any server-side errors to stderr.
  3. Always writes a sibling `.bib` file with the same paper set, so the
     skill is still useful when Zotero is not running or the user declined
     to use it.

Stdlib-only (urllib + json). No pip installs required.

Usage:
    python zotero_export.py --input results.json
    python zotero_export.py --input results.json --bib-only
    python zotero_export.py --input results.json --bib-out ./my.bib
    cat results.json | python zotero_export.py --stdin

Exit codes:
    0  success (at least one output channel succeeded)
    1  IO / decode failure
    2  argparse error (default)
    3  Zotero unreachable AND no .bib could be written (all paths failed)
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import sys
import unicodedata
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

ZOTERO_PING = "http://127.0.0.1:23119/connector/ping"
ZOTERO_SAVE = "http://127.0.0.1:23119/connector/saveItems"
USER_AGENT = "arxiv-research-toolkit/0.1 (+https://github.com/RintaroMatsumoto/arxiv-research-toolkit)"


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


def to_zotero_item(rec: Dict[str, Any]) -> Dict[str, Any]:
    """Map a canonical paper record to a Zotero connector item."""
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
    return item


def ping_zotero() -> bool:
    """Return True if the Zotero connector is reachable on localhost."""
    req = urllib.request.Request(ZOTERO_PING, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=3) as resp:
            return 200 <= resp.status < 300
    except (urllib.error.URLError, urllib.error.HTTPError, OSError):
        return False


def send_to_zotero(items: List[Dict[str, Any]]) -> Optional[str]:
    """POST items to the Zotero connector. Returns an error message or None."""
    payload = {"items": items}
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        ZOTERO_SAVE,
        data=data,
        headers={
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
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
            "Export a paper-search JSON array to the local Zotero connector "
            "(port 23119) and/or a BibTeX file. Stdlib-only."
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
        help="Skip Zotero; only write BibTeX.",
    )
    p.add_argument(
        "--zotero-only",
        action="store_true",
        help="Skip BibTeX; only POST to Zotero.",
    )
    return p


def main(argv=None) -> int:
    """CLI entry point."""
    args = build_arg_parser().parse_args(argv)
    if args.bib_only and args.zotero_only:
        sys.stderr.write("--bib-only and --zotero-only are mutually exclusive.\n")
        return 2

    records = load_input(args.input, args.stdin)
    if not records:
        sys.stderr.write("input contained zero paper records; nothing to export.\n")
        return 1

    report: Dict[str, Any] = {"paper_count": len(records)}
    any_success = False

    # Zotero leg.
    if not args.bib_only:
        if ping_zotero():
            items = [to_zotero_item(r) for r in records]
            err = send_to_zotero(items)
            if err is None:
                report["zotero"] = {"status": "ok", "items_sent": len(items)}
                any_success = True
            else:
                report["zotero"] = {"status": "error", "message": err}
        else:
            report["zotero"] = {
                "status": "unreachable",
                "message": (
                    "Zotero connector on 127.0.0.1:23119 did not respond. "
                    "Open Zotero desktop (6.x+) and try again, or use --bib-only."
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
        except OSError as err:
            report["bibtex"] = {"status": "error", "message": str(err)}

    sys.stdout.write(json.dumps(report, ensure_ascii=False, indent=2))
    sys.stdout.write("\n")
    return 0 if any_success else 3


if __name__ == "__main__":
    sys.exit(main())
