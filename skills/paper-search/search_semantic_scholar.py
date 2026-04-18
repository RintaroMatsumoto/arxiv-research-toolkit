#!/usr/bin/env python3
"""
search_semantic_scholar.py — Query the Semantic Scholar graph API.

Second backend for the paper-search skill (issue #2). Where
``search_arxiv.py`` hits arXiv directly, this script uses Semantic
Scholar's ``/graph/v1/paper/search`` endpoint, which indexes ~200M papers
across publishers (not just arXiv) and supports CS, biology, medicine,
and the humanities.

Output matches the canonical record schema shared with the arXiv
backend, so downstream skills (lit-review-draft, zotero-export,
citation-network) don't need to care which source produced a record.

Usage:
    python search_semantic_scholar.py --query "mixture of experts"
    python search_semantic_scholar.py --query "rag" --year-from 2023 --year-to 2025
    python search_semantic_scholar.py --query "cnn" --venue NeurIPS --limit 20
    python search_semantic_scholar.py --query "transformer" --api-key $S2_KEY

Exit codes: 0 success, 1 network failure, 2 argparse error, 3 JSON parse
failure.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
import urllib.error
import urllib.parse
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from _lib import cache, net, rate_limit  # noqa: E402

S2_SEARCH_URL = "https://api.semanticscholar.org/graph/v1/paper/search"
S2_FIELDS = (
    "title,authors,year,venue,abstract,externalIds,openAccessPdf,"
    "publicationTypes,publicationDate,fieldsOfStudy"
)
HARD_MAX_RESULTS = 100  # S2 caps individual requests at 100.
SEARCH_TTL_SECONDS = 24 * 60 * 60


def build_url(
    query: str, limit: int, offset: int, year_range: Optional[str], venue: Optional[str]
) -> str:
    """Compose the full Semantic Scholar search URL."""
    params: Dict[str, str] = {
        "query": query,
        "limit": str(limit),
        "offset": str(offset),
        "fields": S2_FIELDS,
    }
    if year_range:
        params["year"] = year_range
    if venue:
        params["venue"] = venue
    return f"{S2_SEARCH_URL}?{urllib.parse.urlencode(params)}"


def fetch_search(url: str, api_key: Optional[str]) -> Dict[str, Any]:
    """GET the S2 search endpoint via the shared cache + rate limiter + net."""
    headers: Dict[str, str] = {"Accept": "application/json"}
    if api_key:
        headers["x-api-key"] = api_key

    # Cache key includes api-key-presence only as a boolean — the key
    # itself is secret and must not be written to disk.
    filters = {"authed": bool(api_key)}

    def _go() -> bytes:
        rate_limit.acquire("api.semanticscholar.org")
        try:
            # retries=2 for S2's aggressive 429 pattern on the free tier.
            return net.fetch_bytes(url, timeout=30, retries=2, extra_headers=headers)
        except urllib.error.HTTPError as err:
            sys.stderr.write(f"S2 HTTP {err.code}: {err.reason}\n")
            raise
        except urllib.error.URLError as err:
            sys.stderr.write(f"S2 network error: {err}\n")
            raise

    body = cache.memoized(
        source="s2-search",
        key=url,
        fetcher=_go,
        ttl_seconds=SEARCH_TTL_SECONDS,
        filters=filters,
    )
    try:
        return json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as err:
        sys.stderr.write(f"S2 returned non-JSON: {err}\n")
        sys.exit(3)


def _preferred_id(paper: Dict[str, Any]) -> str:
    """Pick a stable identifier: prefer arXiv ID, fall back to DOI, then S2."""
    ext = paper.get("externalIds") or {}
    arxiv = ext.get("ArXiv")
    if arxiv:
        return arxiv
    doi = ext.get("DOI")
    if doi:
        return doi
    return paper.get("paperId") or ""


def _abs_url(paper: Dict[str, Any]) -> str:
    """Compose a canonical abs-page URL. Prefer arXiv, fall back to S2."""
    ext = paper.get("externalIds") or {}
    arxiv = ext.get("ArXiv")
    if arxiv:
        return f"https://arxiv.org/abs/{arxiv}"
    doi = ext.get("DOI")
    if doi:
        return f"https://doi.org/{doi}"
    pid = paper.get("paperId")
    return f"https://www.semanticscholar.org/paper/{pid}" if pid else ""


def _pdf_url(paper: Dict[str, Any]) -> str:
    """Return the open-access PDF URL if S2 knows of one; else arXiv PDF."""
    oa = (paper.get("openAccessPdf") or {}).get("url") or ""
    if oa:
        return oa
    ext = paper.get("externalIds") or {}
    arxiv = ext.get("ArXiv")
    if arxiv:
        return f"https://arxiv.org/pdf/{arxiv}.pdf"
    return ""


def canonicalize(paper: Dict[str, Any]) -> Dict[str, Any]:
    """Map one S2 paper dict into the shared canonical record schema."""
    authors = [a.get("name", "") for a in (paper.get("authors") or []) if a.get("name")]
    fields_of_study = paper.get("fieldsOfStudy") or []
    pub_date = paper.get("publicationDate") or ""
    published = pub_date or (f"{paper['year']}-01-01" if paper.get("year") else "")

    return {
        "id": _preferred_id(paper),
        "source": "semantic-scholar",
        "title": (paper.get("title") or "").strip(),
        "authors": authors,
        "year": paper.get("year"),
        "venue": paper.get("venue") or "",
        "abstract": (paper.get("abstract") or "").strip(),
        "url": _abs_url(paper),
        "pdf_url": _pdf_url(paper),
        "categories": fields_of_study,
        "published": published,
    }


def filter_by_year(
    records: List[Dict[str, Any]],
    year_from: Optional[int],
    year_to: Optional[int],
) -> List[Dict[str, Any]]:
    """Client-side year filter (belt-and-braces — S2 already accepts ``year=``)."""
    if year_from is None and year_to is None:
        return records
    out: List[Dict[str, Any]] = []
    for rec in records:
        y = rec.get("year")
        if y is None:
            continue
        if year_from is not None and y < year_from:
            continue
        if year_to is not None and y > year_to:
            continue
        out.append(rec)
    return out


def build_arg_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser."""
    p = argparse.ArgumentParser(
        prog="search_semantic_scholar",
        description=(
            "Search Semantic Scholar for papers and emit canonical JSON "
            "records that are schema-compatible with search_arxiv.py."
        ),
    )
    p.add_argument("--query", required=True, help="Free-text search query.")
    p.add_argument(
        "--limit",
        type=int,
        default=10,
        help=f"Max papers to return (1-{HARD_MAX_RESULTS}, default 10).",
    )
    p.add_argument("--offset", type=int, default=0, help="Result offset for pagination.")
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
        "--venue",
        default=None,
        help="Restrict to a venue name (e.g. NeurIPS, ICML, Nature).",
    )
    p.add_argument(
        "--api-key",
        default=os.environ.get("SEMANTIC_SCHOLAR_API_KEY"),
        help=(
            "Optional Semantic Scholar API key (higher rate limits). "
            "Can also be supplied via $SEMANTIC_SCHOLAR_API_KEY."
        ),
    )
    p.add_argument(
        "--pretty",
        action="store_true",
        help="Pretty-print JSON output (indent=2).",
    )
    return p


def main(argv: Optional[List[str]] = None) -> int:
    """CLI entry point."""
    net.ensure_utf8_stdout()
    args = build_arg_parser().parse_args(argv)

    if args.limit < 1:
        sys.stderr.write("--limit must be >= 1\n")
        return 2
    limit = min(args.limit, HARD_MAX_RESULTS)

    # S2 supports year filtering server-side as "2021-2024" or "2023-".
    year_range: Optional[str] = None
    if args.year_from or args.year_to:
        year_range = f"{args.year_from or ''}-{args.year_to or ''}"

    url = build_url(
        query=args.query,
        limit=limit,
        offset=max(0, args.offset),
        year_range=year_range,
        venue=args.venue,
    )

    try:
        result = fetch_search(url, args.api_key)
    except (urllib.error.HTTPError, urllib.error.URLError) as err:
        sys.stderr.write(f"could not reach Semantic Scholar: {err}\n")
        return 1

    raw_papers = result.get("data") or []
    records = [canonicalize(p) for p in raw_papers]
    records = filter_by_year(records, args.year_from, args.year_to)

    indent = 2 if args.pretty else None
    sys.stdout.write(json.dumps(records, ensure_ascii=False, indent=indent))
    if args.pretty:
        sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
