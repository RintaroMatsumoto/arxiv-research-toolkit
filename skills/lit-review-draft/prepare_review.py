#!/usr/bin/env python3
"""
prepare_review.py — Consolidate a paper set into review-ready context.

Backend for the lit-review-draft skill. Takes the canonical JSON output
produced by paper-search (or a manually-assembled list with the same
shape) and emits a single JSON envelope that Claude can use to draft a
themed literature review. The script does NOT call any LLM — synthesis
is Claude's job in the response.

Envelope shape emitted to stdout:

    {
      "topic": "<user topic>",
      "target_length": "short|medium|long",
      "paper_count": N,
      "papers": [
        {
          "cite_key": "AuthorYEARslug",   // BibTeX-friendly
          "id": "2401.12345",
          "source": "arxiv",
          "title": "...",
          "authors": ["..."],
          "year": 2024,
          "venue": "arXiv",
          "abstract": "...",
          "url": "..."
        },
        ...
      ],
      "year_histogram": {"2022": 2, "2023": 5, ...},
      "suggested_sections": ["..."]
    }

Usage:
    python prepare_review.py --input results.json --topic "retrieval augmentation"
    python prepare_review.py --input results.json --topic "..." --length long
    cat results.json | python prepare_review.py --topic "..." --stdin

Exit codes:
    0  success
    1  IO / decode failure
    2  argparse error (default)
"""
from __future__ import annotations

import argparse
import collections
import json
import re
import sys
import unicodedata
from typing import Any, Dict, List, Optional

LENGTHS = {"short", "medium", "long"}


def _strip_accents(s: str) -> str:
    """Fold accented characters for BibTeX-safe cite keys."""
    return "".join(
        c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c)
    )


def _first_author_surname(authors: List[str]) -> str:
    """Best-effort surname extraction from a 'First Last' or 'Last, First' string."""
    if not authors:
        return "Unknown"
    first = authors[0].strip()
    if "," in first:
        surname = first.split(",", 1)[0].strip()
    else:
        parts = first.split()
        surname = parts[-1] if parts else first
    surname = _strip_accents(surname)
    surname = re.sub(r"[^A-Za-z0-9]", "", surname) or "Unknown"
    return surname


def _title_slug(title: str) -> str:
    """First three lowercase word characters of the title, for cite-key disambiguation."""
    words = re.findall(r"[A-Za-z0-9]+", _strip_accents(title).lower())
    words = [w for w in words if w not in {"a", "an", "the", "of", "on", "for", "and"}]
    return "".join(words[:3]) or "paper"


def make_cite_key(rec: Dict[str, Any], taken: set) -> str:
    """Produce a unique BibTeX-compatible cite key for a paper record."""
    surname = _first_author_surname(rec.get("authors", []))
    year = rec.get("year") or "nd"
    slug = _title_slug(rec.get("title", ""))
    base = f"{surname}{year}{slug}"
    key = base
    suffix = ord("a")
    while key in taken:
        key = f"{base}{chr(suffix)}"
        suffix += 1
    taken.add(key)
    return key


def build_year_histogram(papers: List[Dict[str, Any]]) -> Dict[str, int]:
    """Count papers per year; missing years fall into 'unknown'."""
    counter: collections.Counter = collections.Counter()
    for p in papers:
        y = p.get("year")
        counter[str(y) if y else "unknown"] += 1
    return dict(sorted(counter.items()))


def suggest_sections(topic: str, year_histogram: Dict[str, int]) -> List[str]:
    """Propose a default section list — a starting point for Claude to refine."""
    sections = [
        "Motivation and scope",
        f"Foundational work on {topic}",
        "Methodological families",
        "Empirical findings and benchmarks",
        "Open problems and gaps",
    ]
    # If we have papers spanning >3 years, add a temporal framing section.
    known_years = [int(y) for y in year_histogram if y.isdigit()]
    if known_years and (max(known_years) - min(known_years) >= 3):
        sections.insert(2, "Temporal trends")
    return sections


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
        prog="prepare_review",
        description=(
            "Consolidate a paper-search JSON array into a review-ready "
            "envelope (cite keys, year histogram, suggested sections)."
        ),
    )
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--input", help="Path to a JSON file of paper records.")
    src.add_argument(
        "--stdin",
        action="store_true",
        help="Read paper records as JSON from stdin.",
    )
    p.add_argument(
        "--topic",
        required=True,
        help="The framing topic of the literature review (e.g. 'retrieval augmentation').",
    )
    p.add_argument(
        "--length",
        choices=sorted(LENGTHS),
        default="medium",
        help="Target draft length hint for Claude (default: medium).",
    )
    p.add_argument(
        "--max-papers",
        type=int,
        default=50,
        help="Cap on number of papers forwarded to Claude (default 50).",
    )
    p.add_argument(
        "--pretty",
        action="store_true",
        help="Pretty-print the JSON envelope.",
    )
    return p


def main(argv=None) -> int:
    """CLI entry point."""
    args = build_arg_parser().parse_args(argv)

    papers = load_input(args.input, args.stdin)
    if args.max_papers and len(papers) > args.max_papers:
        sys.stderr.write(
            f"received {len(papers)} papers; truncating to {args.max_papers}.\n"
        )
        papers = papers[: args.max_papers]

    taken: set = set()
    enriched: List[Dict[str, Any]] = []
    for rec in papers:
        if not isinstance(rec, dict):
            continue
        cite_key = make_cite_key(rec, taken)
        enriched.append(
            {
                "cite_key": cite_key,
                "id": rec.get("id", ""),
                "source": rec.get("source", ""),
                "title": rec.get("title", ""),
                "authors": rec.get("authors", []),
                "year": rec.get("year"),
                "venue": rec.get("venue", ""),
                "abstract": rec.get("abstract", ""),
                "url": rec.get("url", ""),
            }
        )

    histogram = build_year_histogram(enriched)
    envelope = {
        "topic": args.topic,
        "target_length": args.length,
        "paper_count": len(enriched),
        "papers": enriched,
        "year_histogram": histogram,
        "suggested_sections": suggest_sections(args.topic, histogram),
    }

    indent = 2 if args.pretty else None
    sys.stdout.write(json.dumps(envelope, ensure_ascii=False, indent=indent))
    if args.pretty:
        sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
