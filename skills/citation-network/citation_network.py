#!/usr/bin/env python3
"""
citation_network.py — Build a local citation graph via Semantic Scholar.

Backend for the citation-network skill. Given one or more seed papers
(arXiv ID, DOI, or Semantic Scholar paperId), this script walks the
citation / reference graph breadth-first up to a depth limit, bounded by
a hard node cap, and emits:

  1. A JSON node/edge graph to stdout (or to --out-json).
  2. Optionally an SVG visualization to --out-svg.

Stdlib-only (urllib + json + xml/etree). No pip installs required.

Semantic Scholar graph endpoints used:
  - /graph/v1/paper/{id}?fields=title,authors,year,venue,externalIds
  - /graph/v1/paper/{id}/references?fields=...
  - /graph/v1/paper/{id}/citations?fields=...

Usage:
    python citation_network.py --seed 2401.12345 --depth 1 --max-nodes 40
    python citation_network.py --seed 2401.12345 --direction references
    python citation_network.py --seed 2401.12345 --out-json graph.json --out-svg graph.svg

Exit codes:
    0  success
    1  network / IO failure
    2  argparse error (default)
"""
from __future__ import annotations

import argparse
import collections
import json
import math
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Deque, Dict, List, Optional, Set, Tuple

S2_BASE = "https://api.semanticscholar.org/graph/v1/paper"
S2_FIELDS = "title,authors,year,venue,externalIds"
USER_AGENT = "arxiv-research-toolkit/0.1 (+https://github.com/RintaroMatsumoto/arxiv-research-toolkit)"
RATE_LIMIT_SLEEP = 1.1  # Semantic Scholar free tier: ~100 req / 5 min.
RETRY_BACKOFF_SECONDS = 3.0
DEFAULT_MAX_NODES = 50
HARD_MAX_NODES = 300


def _fetch_json(url: str, api_key: Optional[str]) -> Optional[Dict[str, Any]]:
    """GET a Semantic Scholar JSON endpoint with one retry. Returns None on 404."""
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    if api_key:
        headers["x-api-key"] = api_key
    req = urllib.request.Request(url, headers=headers)
    for attempt in (1, 2):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as err:
            if err.code == 404:
                return None
            if err.code == 429 and attempt == 1:
                sys.stderr.write("S2 throttled (429); backing off 3s...\n")
                time.sleep(RETRY_BACKOFF_SECONDS)
                continue
            if 500 <= err.code < 600 and attempt == 1:
                sys.stderr.write(f"S2 HTTP {err.code}; retrying in 3s...\n")
                time.sleep(RETRY_BACKOFF_SECONDS)
                continue
            sys.stderr.write(f"S2 request failed ({err.code}): {url}\n")
            return None
        except urllib.error.URLError as err:
            if attempt == 1:
                sys.stderr.write(f"S2 network error ({err}); retrying in 3s...\n")
                time.sleep(RETRY_BACKOFF_SECONDS)
                continue
            sys.stderr.write(f"S2 network error, giving up: {err}\n")
            return None
    return None


def _seed_to_s2_id(seed: str) -> str:
    """Convert a user-supplied seed to the S2 paper-lookup identifier form."""
    seed = seed.strip()
    if re.match(r"^10\.\d{4,9}/", seed):
        return f"DOI:{seed}"
    if re.match(r"^\d{4}\.\d{4,5}(v\d+)?$", seed) or re.match(r"^[a-zA-Z\-\.]+/\d{7}", seed):
        # Strip version suffix — S2 keys on version-less arXiv IDs.
        base = re.sub(r"v\d+$", "", seed)
        return f"arXiv:{base}"
    # Fall through: treat as a native S2 paperId.
    return seed


def _compact_paper(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Strip the S2 paper payload down to the node fields we care about."""
    authors = [a.get("name", "") for a in (raw.get("authors") or []) if a.get("name")]
    ext = raw.get("externalIds") or {}
    return {
        "paperId": raw.get("paperId", ""),
        "title": raw.get("title", ""),
        "authors": authors,
        "year": raw.get("year"),
        "venue": raw.get("venue", ""),
        "arxivId": ext.get("ArXiv"),
        "doi": ext.get("DOI"),
    }


def fetch_paper(s2_id: str, api_key: Optional[str]) -> Optional[Dict[str, Any]]:
    """Fetch a single paper's metadata."""
    url = f"{S2_BASE}/{urllib.parse.quote(s2_id, safe=':/')}" f"?fields={S2_FIELDS}"
    raw = _fetch_json(url, api_key)
    return _compact_paper(raw) if raw else None


def fetch_edges(
    s2_id: str, direction: str, api_key: Optional[str], limit: int = 50
) -> List[Dict[str, Any]]:
    """Fetch references or citations for a paper. Returns compacted paper dicts."""
    assert direction in ("references", "citations")
    url = (
        f"{S2_BASE}/{urllib.parse.quote(s2_id, safe=':/')}/"
        f"{direction}?fields={S2_FIELDS}&limit={limit}"
    )
    raw = _fetch_json(url, api_key)
    if not raw:
        return []
    entries = raw.get("data") or []
    out = []
    for e in entries:
        # References put the paper under e["citedPaper"]; citations under e["citingPaper"].
        target = e.get("citedPaper") if direction == "references" else e.get("citingPaper")
        if target:
            out.append(_compact_paper(target))
    return out


def walk_graph(
    seeds: List[str],
    depth: int,
    direction: str,
    max_nodes: int,
    api_key: Optional[str],
) -> Tuple[Dict[str, Dict[str, Any]], List[Tuple[str, str, str]]]:
    """BFS walk. Returns (nodes_by_id, list_of_edges_src_dst_kind)."""
    nodes: Dict[str, Dict[str, Any]] = {}
    edges: List[Tuple[str, str, str]] = []
    queue: Deque[Tuple[str, int]] = collections.deque()
    visited: Set[str] = set()

    # Seed the queue.
    for s in seeds:
        s2_id = _seed_to_s2_id(s)
        seed_meta = fetch_paper(s2_id, api_key)
        time.sleep(RATE_LIMIT_SLEEP)
        if not seed_meta or not seed_meta.get("paperId"):
            sys.stderr.write(f"could not resolve seed '{s}'. Skipping.\n")
            continue
        pid = seed_meta["paperId"]
        seed_meta["is_seed"] = True
        nodes[pid] = seed_meta
        visited.add(pid)
        if depth > 0:
            queue.append((pid, 0))

    directions = (
        ["references", "citations"] if direction == "both" else [direction]
    )

    while queue and len(nodes) < max_nodes:
        pid, current_depth = queue.popleft()
        if current_depth >= depth:
            continue
        for dir_kind in directions:
            neighbors = fetch_edges(pid, dir_kind, api_key)
            time.sleep(RATE_LIMIT_SLEEP)
            for n in neighbors:
                npid = n.get("paperId")
                if not npid:
                    continue
                # Edge direction is always citer → cited.
                if dir_kind == "references":
                    src, dst = pid, npid
                else:
                    src, dst = npid, pid
                edges.append((src, dst, dir_kind))
                if npid not in visited:
                    visited.add(npid)
                    nodes[npid] = n
                    if current_depth + 1 < depth and len(nodes) < max_nodes:
                        queue.append((npid, current_depth + 1))
                if len(nodes) >= max_nodes:
                    break
            if len(nodes) >= max_nodes:
                break
    return nodes, edges


# --- SVG rendering (stdlib-only circle layout). ------------------------------

def _label_for(node: Dict[str, Any]) -> str:
    """Produce a short node label: 'FirstAuthor Year' fallback to title prefix."""
    authors = node.get("authors") or []
    first = authors[0].split()[-1] if authors and authors[0] else ""
    year = node.get("year") or ""
    if first and year:
        return f"{first} {year}"
    title = (node.get("title") or "untitled").strip()
    return title[:28] + ("…" if len(title) > 28 else "")


def render_svg(nodes: Dict[str, Dict[str, Any]], edges: List[Tuple[str, str, str]]) -> str:
    """Return a minimal circle-layout SVG string. Seed nodes are colored."""
    width = height = 640
    cx = cy = width // 2
    radius = min(width, height) // 2 - 60
    items = list(nodes.items())
    n = max(1, len(items))
    positions: Dict[str, Tuple[float, float]] = {}
    for i, (pid, _) in enumerate(items):
        angle = 2 * math.pi * i / n - math.pi / 2
        positions[pid] = (cx + radius * math.cos(angle), cy + radius * math.sin(angle))

    svg_parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" font-family="sans-serif" font-size="11">',
        '<rect width="100%" height="100%" fill="#fafafa"/>',
    ]
    # Edges first, so nodes draw over them.
    for src, dst, _kind in edges:
        if src not in positions or dst not in positions:
            continue
        x1, y1 = positions[src]
        x2, y2 = positions[dst]
        svg_parts.append(
            f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" '
            'stroke="#c5c5c5" stroke-width="1"/>'
        )
    for pid, node in items:
        x, y = positions[pid]
        fill = "#ff7043" if node.get("is_seed") else "#4a90e2"
        label = _label_for(node).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        svg_parts.append(
            f'<circle cx="{x:.1f}" cy="{y:.1f}" r="7" fill="{fill}" '
            'stroke="#333" stroke-width="0.5"/>'
        )
        # Place label outward of the node position to avoid overlap.
        tx = x + 11 if x >= cx else x - 11
        anchor = "start" if x >= cx else "end"
        svg_parts.append(
            f'<text x="{tx:.1f}" y="{y + 3:.1f}" text-anchor="{anchor}" '
            f'fill="#222">{label}</text>'
        )
    svg_parts.append("</svg>")
    return "\n".join(svg_parts)


def build_arg_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser."""
    p = argparse.ArgumentParser(
        prog="citation_network",
        description=(
            "Walk the citation/reference graph around one or more seed "
            "papers via Semantic Scholar, and emit a JSON graph plus an "
            "optional SVG visualization."
        ),
    )
    p.add_argument(
        "--seed",
        required=True,
        action="append",
        help=(
            "Seed paper identifier (arXiv ID, DOI, or S2 paperId). May be "
            "repeated for multiple seeds."
        ),
    )
    p.add_argument(
        "--depth",
        type=int,
        default=1,
        help="BFS depth from each seed (default 1; try 2 with caution).",
    )
    p.add_argument(
        "--direction",
        choices=["references", "citations", "both"],
        default="references",
        help="Which edges to follow (default: references — papers the seed cites).",
    )
    p.add_argument(
        "--max-nodes",
        type=int,
        default=DEFAULT_MAX_NODES,
        help=f"Hard cap on number of nodes (default {DEFAULT_MAX_NODES}, max {HARD_MAX_NODES}).",
    )
    p.add_argument(
        "--api-key",
        default=os.environ.get("SEMANTIC_SCHOLAR_API_KEY"),
        help="Optional Semantic Scholar API key (higher rate limits).",
    )
    p.add_argument("--out-json", default=None, help="Write the graph JSON to this path.")
    p.add_argument("--out-svg", default=None, help="Write an SVG visualization to this path.")
    p.add_argument("--pretty", action="store_true", help="Pretty-print the JSON output.")
    return p


def main(argv=None) -> int:
    """CLI entry point."""
    args = build_arg_parser().parse_args(argv)
    max_nodes = min(max(1, args.max_nodes), HARD_MAX_NODES)
    if args.depth < 0:
        sys.stderr.write("--depth must be >= 0\n")
        return 2

    nodes, edges = walk_graph(
        seeds=args.seed,
        depth=args.depth,
        direction=args.direction,
        max_nodes=max_nodes,
        api_key=args.api_key,
    )

    graph = {
        "seed_count": sum(1 for n in nodes.values() if n.get("is_seed")),
        "node_count": len(nodes),
        "edge_count": len(edges),
        "direction": args.direction,
        "depth": args.depth,
        "nodes": list(nodes.values()),
        "edges": [{"src": s, "dst": d, "kind": k} for s, d, k in edges],
    }

    payload = json.dumps(graph, ensure_ascii=False, indent=2 if args.pretty else None)
    if args.out_json:
        with open(args.out_json, "w", encoding="utf-8") as fh:
            fh.write(payload)
            fh.write("\n")
        sys.stderr.write(f"wrote JSON graph to {os.path.abspath(args.out_json)}\n")
    else:
        sys.stdout.write(payload)
        if args.pretty:
            sys.stdout.write("\n")

    if args.out_svg:
        svg = render_svg(nodes, edges)
        with open(args.out_svg, "w", encoding="utf-8") as fh:
            fh.write(svg)
        sys.stderr.write(f"wrote SVG graph to {os.path.abspath(args.out_svg)}\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())
