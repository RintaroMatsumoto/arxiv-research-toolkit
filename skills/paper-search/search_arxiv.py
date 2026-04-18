#!/usr/bin/env python3
"""
search_arxiv.py — Query the public arXiv Atom API and emit a JSON array.

MVP backend for the paper-search skill. Uses the Python stdlib only
(urllib for HTTP, xml.etree for Atom parsing) so it runs on any Python
3.8+ install without pip. Output is a JSON array of records matching the
canonical schema documented in docs/skills/paper-search.md.

Usage:
    python search_arxiv.py --query "mixture of experts" --max-results 10
    python search_arxiv.py --query "diffusion" --category cs.LG --sort submittedDate
    python search_arxiv.py --query "rag" --year-from 2024 --year-to 2025

The script prints JSON to stdout and any human-readable errors to stderr.
Exit codes: 0 on success (including zero results), 1 on network failure,
2 on argument error (argparse default), 3 on parse failure.

Shared helpers from ``skills/_lib/``:
  net.ensure_utf8_stdout  — avoid cp932 crashes on Windows ja-JP locale.
  net.fetch_bytes         — SSL-aware HTTP GET with retry.
  cache.memoized          — per-query response cache (24 h TTL).
  rate_limit.acquire      — cross-process politeness gate for arxiv.org.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
import urllib.error
import urllib.parse
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional

# Make the sibling ``_lib`` package importable regardless of cwd.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from _lib import cache, net, rate_limit  # noqa: E402

ARXIV_ENDPOINT = "http://export.arxiv.org/api/query"
ATOM_NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "arxiv": "http://arxiv.org/schemas/atom",
}
HARD_MAX_RESULTS = 50
SEARCH_TTL_SECONDS = 24 * 60 * 60  # 24 h for search results.


def build_query(query: str, category: Optional[str]) -> str:
    """Compose arXiv's search_query string from user inputs."""
    parts = [f"all:{query}"]
    if category:
        parts.append(f"cat:{category}")
    return " AND ".join(parts)


def build_url(
    query: str,
    max_results: int,
    category: Optional[str],
    sort: str,
) -> str:
    """Assemble the full arXiv API URL with query parameters."""
    params = {
        "search_query": build_query(query, category),
        "start": "0",
        "max_results": str(max_results),
    }
    if sort == "submittedDate":
        params["sortBy"] = "submittedDate"
        params["sortOrder"] = "descending"
    else:
        params["sortBy"] = "relevance"
        params["sortOrder"] = "descending"
    return f"{ARXIV_ENDPOINT}?{urllib.parse.urlencode(params)}"


def fetch(url: str) -> bytes:
    """GET the URL through the shared cache + rate limiter + net helpers."""
    def _go() -> bytes:
        rate_limit.acquire("export.arxiv.org")
        return net.fetch_bytes(url, timeout=30, retries=1)

    # Cache the raw Atom body keyed on the full URL (query + filters).
    return cache.memoized(
        source="arxiv-search",
        key=url,
        fetcher=_go,
        ttl_seconds=SEARCH_TTL_SECONDS,
    )


def _text(entry: ET.Element, path: str) -> str:
    """Return stripped text at an XPath, or empty string if missing."""
    node = entry.find(path, ATOM_NS)
    return node.text.strip() if node is not None and node.text else ""


def _arxiv_id_from_url(abs_url: str) -> str:
    """Extract the bare arXiv ID from the <id> absolute URL."""
    tail = abs_url.rstrip("/").rsplit("/", 1)[-1]
    return tail.split("v")[0] if "v" in tail and tail.split("v")[-1].isdigit() else tail


def parse_entry(entry: ET.Element) -> Dict[str, Any]:
    """Map a single arXiv <entry> into the canonical record schema."""
    abs_url = _text(entry, "atom:id")
    arxiv_id = _arxiv_id_from_url(abs_url)
    title = " ".join(_text(entry, "atom:title").split())
    abstract = " ".join(_text(entry, "atom:summary").split())
    published = _text(entry, "atom:published")
    year: Optional[int] = None
    if len(published) >= 4 and published[:4].isdigit():
        year = int(published[:4])

    authors: List[str] = []
    for author in entry.findall("atom:author", ATOM_NS):
        name = author.find("atom:name", ATOM_NS)
        if name is not None and name.text:
            authors.append(name.text.strip())

    categories: List[str] = []
    for cat in entry.findall("atom:category", ATOM_NS):
        term = cat.attrib.get("term")
        if term:
            categories.append(term)

    pdf_url = ""
    for link in entry.findall("atom:link", ATOM_NS):
        if link.attrib.get("title") == "pdf":
            pdf_url = link.attrib.get("href", "")
            break
    if not pdf_url and arxiv_id:
        pdf_url = f"https://arxiv.org/pdf/{arxiv_id}.pdf"

    return {
        "id": arxiv_id,
        "source": "arxiv",
        "title": title,
        "authors": authors,
        "year": year,
        "venue": "arXiv",
        "abstract": abstract,
        "url": abs_url or (f"https://arxiv.org/abs/{arxiv_id}" if arxiv_id else ""),
        "pdf_url": pdf_url,
        "categories": categories,
        "published": published,
    }


def parse_feed(xml_bytes: bytes) -> List[Dict[str, Any]]:
    """Parse the arXiv Atom feed into a list of canonical records."""
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as err:
        sys.stderr.write(f"arxiv response was not valid XML: {err}\n")
        sys.exit(3)
    entries = root.findall("atom:entry", ATOM_NS)
    return [parse_entry(e) for e in entries]


def filter_by_year(
    records: List[Dict[str, Any]],
    year_from: Optional[int],
    year_to: Optional[int],
) -> List[Dict[str, Any]]:
    """Apply client-side year-range filter to the record list."""
    if year_from is None and year_to is None:
        return records
    kept: List[Dict[str, Any]] = []
    for rec in records:
        y = rec.get("year")
        if y is None:
            continue
        if year_from is not None and y < year_from:
            continue
        if year_to is not None and y > year_to:
            continue
        kept.append(rec)
    return kept


def build_arg_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser."""
    p = argparse.ArgumentParser(
        prog="search_arxiv",
        description=(
            "Search arXiv via its public Atom API and print matching papers as JSON. "
            "Uses the Python stdlib only; no pip installs required."
        ),
    )
    p.add_argument("--query", required=True, help="Free-text search query.")
    p.add_argument(
        "--max-results",
        type=int,
        default=10,
        help=f"Max papers to return (1-{HARD_MAX_RESULTS}, default 10).",
    )
    p.add_argument(
        "--year-from",
        type=int,
        default=None,
        help="Earliest publication year, inclusive.",
    )
    p.add_argument(
        "--year-to",
        type=int,
        default=None,
        help="Latest publication year, inclusive.",
    )
    p.add_argument(
        "--category",
        default=None,
        help="arXiv category to restrict to, e.g. cs.LG, cs.CL, stat.ML.",
    )
    p.add_argument(
        "--sort",
        choices=["relevance", "submittedDate"],
        default="relevance",
        help="Sort order (default: relevance).",
    )
    p.add_argument(
        "--pretty",
        action="store_true",
        help="Pretty-print the JSON output (indent=2).",
    )
    return p


def main(argv: Optional[List[str]] = None) -> int:
    """CLI entry point. Returns a process exit code."""
    net.ensure_utf8_stdout()
    args = build_arg_parser().parse_args(argv)

    if args.max_results < 1:
        sys.stderr.write("--max-results must be >= 1\n")
        return 2
    max_results = min(args.max_results, HARD_MAX_RESULTS)

    url = build_url(
        query=args.query,
        max_results=max_results,
        category=args.category,
        sort=args.sort,
    )

    try:
        body = fetch(url)
    except (urllib.error.HTTPError, urllib.error.URLError) as err:
        sys.stderr.write(f"could not reach arXiv: {err}\n")
        return 1

    records = parse_feed(body)
    records = filter_by_year(records, args.year_from, args.year_to)

    indent = 2 if args.pretty else None
    sys.stdout.write(json.dumps(records, ensure_ascii=False, indent=indent))
    if args.pretty:
        sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
