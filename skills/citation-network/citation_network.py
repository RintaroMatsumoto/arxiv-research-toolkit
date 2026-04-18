#!/usr/bin/env python3
"""
citation_network.py — Build a local citation graph via Semantic Scholar.

Backend for the citation-network skill. Given one or more seed papers
(arXiv ID, DOI, or Semantic Scholar paperId), this script walks the
citation / reference graph breadth-first up to a depth limit, bounded by
a hard node cap, and emits:

  1. A JSON node/edge graph (stdout or ``--out-json``).
  2. An optional SVG visualization (``--out-svg``) using either a circle
     layout or a Fruchterman-Reingold force-directed layout (issue #5).
  3. Optional GraphViz DOT (``--out-dot``) and GraphML (``--out-graphml``)
     files for downstream tools (issue #5).

Stdlib-only (urllib + json + math). No pip installs required.

Semantic Scholar graph endpoints used:
  - /graph/v1/paper/{id}?fields=title,authors,year,venue,externalIds
  - /graph/v1/paper/{id}/references?fields=...
  - /graph/v1/paper/{id}/citations?fields=...

Usage:
    python citation_network.py --seed 2401.12345 --depth 1 --max-nodes 40
    python citation_network.py --seed 2401.12345 --direction references
    python citation_network.py --seed 2401.12345 --out-json graph.json --out-svg graph.svg
    python citation_network.py --seed 2401.12345 --layout force --out-svg graph.svg
    python citation_network.py --seed 2401.12345 --out-dot graph.dot --out-graphml graph.xml

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
import pathlib
import random
import re
import sys
import urllib.error
import urllib.parse
from typing import Any, Deque, Dict, List, Optional, Set, Tuple

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from _lib import cache, net, rate_limit  # noqa: E402

S2_BASE = "https://api.semanticscholar.org/graph/v1/paper"
S2_FIELDS = "title,authors,year,venue,externalIds"
DEFAULT_MAX_NODES = 50
HARD_MAX_NODES = 300

PAPER_TTL = 7 * 24 * 60 * 60  # 7 d for metadata.
REFS_TTL = 0                   # immutable for a given paper id.


def _fetch_json(url: str, api_key: Optional[str]) -> Optional[Dict[str, Any]]:
    """GET a Semantic Scholar JSON endpoint via shared helpers. None on 404."""
    headers = {"Accept": "application/json"}
    if api_key:
        headers["x-api-key"] = api_key
    rate_limit.acquire("api.semanticscholar.org")
    try:
        # retries=2 → three total attempts with exponential backoff, which
        # is what S2's 429s need when the free-tier budget is exhausted.
        body = net.fetch_bytes(url, timeout=30, retries=2, extra_headers=headers)
    except urllib.error.HTTPError as err:
        if err.code == 404:
            return None
        sys.stderr.write(f"S2 request failed ({err.code}): {url}\n")
        return None
    except urllib.error.URLError as err:
        sys.stderr.write(f"S2 network error, giving up: {err}\n")
        return None
    try:
        return json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as err:
        sys.stderr.write(f"S2 returned non-JSON for {url}: {err}\n")
        return None


def _seed_to_s2_id(seed: str) -> str:
    """Convert a user-supplied seed to the S2 paper-lookup identifier form."""
    seed = seed.strip()
    if re.match(r"^10\.\d{4,9}/", seed):
        return f"DOI:{seed}"
    if re.match(r"^\d{4}\.\d{4,5}(v\d+)?$", seed) or re.match(
        r"^[a-zA-Z\-\.]+/\d{7}", seed
    ):
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
    """Fetch a single paper's metadata (cached 7 d).

    Transient failures (429 / 5xx / network) are NOT cached — the next
    call will re-fetch. Only successful responses are written.
    """
    url = f"{S2_BASE}/{urllib.parse.quote(s2_id, safe=':/')}?fields={S2_FIELDS}"
    hit = cache.get(source="s2-paper", key=url)
    if hit is not None:
        try:
            raw = json.loads(hit.decode("utf-8"))
            return _compact_paper(raw) if raw else None
        except (UnicodeDecodeError, json.JSONDecodeError):
            pass  # corrupted entry — fall through to refetch
    raw = _fetch_json(url, api_key)
    if raw is None:
        return None  # intentionally do not poison the cache
    cache.put(
        source="s2-paper",
        key=url,
        body=json.dumps(raw).encode("utf-8"),
        ttl_seconds=PAPER_TTL,
    )
    return _compact_paper(raw)


def fetch_edges(
    s2_id: str, direction: str, api_key: Optional[str], limit: int = 50
) -> List[Dict[str, Any]]:
    """Fetch references or citations for a paper (cached immutably).

    Like ``fetch_paper``, transient failures are not cached.
    """
    assert direction in ("references", "citations")
    url = (
        f"{S2_BASE}/{urllib.parse.quote(s2_id, safe=':/')}/"
        f"{direction}?fields={S2_FIELDS}&limit={limit}"
    )
    raw: Optional[Dict[str, Any]] = None
    hit = cache.get(source=f"s2-{direction}", key=url)
    if hit is not None:
        try:
            raw = json.loads(hit.decode("utf-8")) or None
        except (UnicodeDecodeError, json.JSONDecodeError):
            raw = None
    if raw is None:
        raw = _fetch_json(url, api_key)
        if raw is None:
            return []  # transient — do not cache
        cache.put(
            source=f"s2-{direction}",
            key=url,
            body=json.dumps(raw).encode("utf-8"),
            ttl_seconds=REFS_TTL,
        )
    entries = raw.get("data") or []
    out = []
    for e in entries:
        # References put the paper under e["citedPaper"]; citations under e["citingPaper"].
        target = (
            e.get("citedPaper") if direction == "references" else e.get("citingPaper")
        )
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


# --- Layout algorithms -------------------------------------------------------

def _circle_layout(
    node_ids: List[str], width: int, height: int
) -> Dict[str, Tuple[float, float]]:
    """Arrange nodes evenly on a circle. Deterministic, useful for small N."""
    cx = width / 2
    cy = height / 2
    radius = min(width, height) / 2 - 60
    n = max(1, len(node_ids))
    positions: Dict[str, Tuple[float, float]] = {}
    for i, pid in enumerate(node_ids):
        angle = 2 * math.pi * i / n - math.pi / 2
        positions[pid] = (
            cx + radius * math.cos(angle),
            cy + radius * math.sin(angle),
        )
    return positions


def _force_layout(
    node_ids: List[str],
    edges: List[Tuple[str, str, str]],
    width: int,
    height: int,
    iterations: int = 120,
    seed: int = 1,
) -> Dict[str, Tuple[float, float]]:
    """Fruchterman-Reingold layout, stdlib-only.

    Cooling schedule is linear; sufficient for N ≲ 300. Random initial
    positions are seeded so re-runs on the same graph are reproducible.
    """
    rng = random.Random(seed)
    n = max(1, len(node_ids))
    area = width * height
    k = math.sqrt(area / n)  # ideal edge length
    positions: Dict[str, List[float]] = {
        pid: [rng.uniform(0, width), rng.uniform(0, height)] for pid in node_ids
    }

    id_set = set(node_ids)
    valid_edges = [(s, d) for s, d, _ in edges if s in id_set and d in id_set]

    t = width / 10.0  # initial "temperature" (max displacement per step)
    cooling = t / max(1, iterations)

    for _ in range(iterations):
        # Repulsive forces: every pair pushes apart.
        disp: Dict[str, List[float]] = {pid: [0.0, 0.0] for pid in node_ids}
        for i, u in enumerate(node_ids):
            ux, uy = positions[u]
            for v in node_ids[i + 1:]:
                vx, vy = positions[v]
                dx, dy = ux - vx, uy - vy
                dist = math.hypot(dx, dy) or 0.01
                force = k * k / dist
                fx = dx / dist * force
                fy = dy / dist * force
                disp[u][0] += fx
                disp[u][1] += fy
                disp[v][0] -= fx
                disp[v][1] -= fy

        # Attractive forces along edges.
        for u, v in valid_edges:
            ux, uy = positions[u]
            vx, vy = positions[v]
            dx, dy = ux - vx, uy - vy
            dist = math.hypot(dx, dy) or 0.01
            force = dist * dist / k
            fx = dx / dist * force
            fy = dy / dist * force
            disp[u][0] -= fx
            disp[u][1] -= fy
            disp[v][0] += fx
            disp[v][1] += fy

        # Apply displacement, capped by current temperature; keep in-box.
        for pid in node_ids:
            dx, dy = disp[pid]
            mag = math.hypot(dx, dy) or 0.01
            step_x = dx / mag * min(mag, t)
            step_y = dy / mag * min(mag, t)
            x, y = positions[pid]
            x = min(width - 20, max(20, x + step_x))
            y = min(height - 20, max(20, y + step_y))
            positions[pid] = [x, y]

        t = max(0.0, t - cooling)

    return {pid: (xy[0], xy[1]) for pid, xy in positions.items()}


# --- SVG rendering -----------------------------------------------------------

def _label_for(node: Dict[str, Any]) -> str:
    """Produce a short node label: 'FirstAuthor Year' fallback to title prefix."""
    authors = node.get("authors") or []
    first = authors[0].split()[-1] if authors and authors[0] else ""
    year = node.get("year") or ""
    if first and year:
        return f"{first} {year}"
    title = (node.get("title") or "untitled").strip()
    return title[:28] + ("…" if len(title) > 28 else "")


def render_svg(
    nodes: Dict[str, Dict[str, Any]],
    edges: List[Tuple[str, str, str]],
    layout: str = "circle",
) -> str:
    """Return an SVG with the requested layout. Seed nodes coloured orange."""
    width = height = 640
    node_ids = list(nodes.keys())
    if layout == "force":
        positions = _force_layout(node_ids, edges, width, height)
    else:
        positions = _circle_layout(node_ids, width, height)

    cx = width / 2
    svg_parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" font-family="sans-serif" font-size="11">',
        '<rect width="100%" height="100%" fill="#fafafa"/>',
    ]
    # Edges first so nodes draw over them.
    for src, dst, _kind in edges:
        if src not in positions or dst not in positions:
            continue
        x1, y1 = positions[src]
        x2, y2 = positions[dst]
        svg_parts.append(
            f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" '
            'stroke="#c5c5c5" stroke-width="1"/>'
        )
    for pid, node in nodes.items():
        x, y = positions[pid]
        fill = "#ff7043" if node.get("is_seed") else "#4a90e2"
        label = (
            _label_for(node)
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )
        svg_parts.append(
            f'<circle cx="{x:.1f}" cy="{y:.1f}" r="7" fill="{fill}" '
            'stroke="#333" stroke-width="0.5"/>'
        )
        tx = x + 11 if x >= cx else x - 11
        anchor = "start" if x >= cx else "end"
        svg_parts.append(
            f'<text x="{tx:.1f}" y="{y + 3:.1f}" text-anchor="{anchor}" '
            f'fill="#222">{label}</text>'
        )
    svg_parts.append("</svg>")
    return "\n".join(svg_parts)


# --- GraphViz DOT ------------------------------------------------------------

def _dot_escape(s: str) -> str:
    """Escape a string for safe use inside a DOT double-quoted literal."""
    return s.replace("\\", "\\\\").replace('"', '\\"')


def render_dot(
    nodes: Dict[str, Dict[str, Any]],
    edges: List[Tuple[str, str, str]],
) -> str:
    """Return a GraphViz DOT document. Seed nodes get a different fill."""
    lines = [
        "digraph citations {",
        "  graph [overlap=false, splines=true, bgcolor=\"#fafafa\"];",
        "  node  [shape=ellipse, style=filled, fontname=\"Helvetica\"];",
        "  edge  [color=\"#c5c5c5\"];",
    ]
    for pid, node in nodes.items():
        label = _label_for(node)
        fill = "#ff7043" if node.get("is_seed") else "#4a90e2"
        lines.append(
            f'  "{_dot_escape(pid)}" [label="{_dot_escape(label)}", '
            f'fillcolor="{fill}", fontcolor="white"];'
        )
    for src, dst, kind in edges:
        lines.append(
            f'  "{_dot_escape(src)}" -> "{_dot_escape(dst)}" '
            f'[tooltip="{_dot_escape(kind)}"];'
        )
    lines.append("}")
    return "\n".join(lines) + "\n"


# --- GraphML -----------------------------------------------------------------

def _xml_escape(s: str) -> str:
    """Minimal XML escape for attribute/text values."""
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def render_graphml(
    nodes: Dict[str, Dict[str, Any]],
    edges: List[Tuple[str, str, str]],
) -> str:
    """Return a GraphML document. Enough for Gephi / yEd / networkx."""
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<graphml xmlns="http://graphml.graphdrawing.org/xmlns">',
        '  <key id="title"  for="node" attr.name="title"  attr.type="string"/>',
        '  <key id="year"   for="node" attr.name="year"   attr.type="int"/>',
        '  <key id="venue"  for="node" attr.name="venue"  attr.type="string"/>',
        '  <key id="is_seed" for="node" attr.name="is_seed" attr.type="boolean"/>',
        '  <key id="kind"   for="edge" attr.name="kind"   attr.type="string"/>',
        '  <graph id="G" edgedefault="directed">',
    ]
    for pid, node in nodes.items():
        lines.append(f'    <node id="{_xml_escape(pid)}">')
        lines.append(f'      <data key="title">{_xml_escape(node.get("title", ""))}</data>')
        year = node.get("year")
        if isinstance(year, int):
            lines.append(f'      <data key="year">{year}</data>')
        lines.append(f'      <data key="venue">{_xml_escape(node.get("venue", "") or "")}</data>')
        lines.append(
            f'      <data key="is_seed">{"true" if node.get("is_seed") else "false"}</data>'
        )
        lines.append("    </node>")
    for i, (src, dst, kind) in enumerate(edges):
        lines.append(
            f'    <edge id="e{i}" source="{_xml_escape(src)}" '
            f'target="{_xml_escape(dst)}">'
        )
        lines.append(f'      <data key="kind">{_xml_escape(kind)}</data>')
        lines.append("    </edge>")
    lines.append("  </graph>")
    lines.append("</graphml>")
    return "\n".join(lines) + "\n"


def build_arg_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser."""
    p = argparse.ArgumentParser(
        prog="citation_network",
        description=(
            "Walk the citation/reference graph around one or more seed "
            "papers via Semantic Scholar, and emit a JSON graph plus "
            "optional SVG / DOT / GraphML visualizations."
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
    p.add_argument(
        "--layout",
        choices=["circle", "force"],
        default="circle",
        help=(
            "SVG layout algorithm. 'circle' is deterministic and readable "
            "for ≲ 30 nodes; 'force' uses Fruchterman-Reingold and scales "
            "better for dense graphs."
        ),
    )
    p.add_argument("--out-json", default=None, help="Write the graph JSON to this path.")
    p.add_argument("--out-svg", default=None, help="Write an SVG visualization to this path.")
    p.add_argument("--out-dot", default=None, help="Write a GraphViz DOT file to this path.")
    p.add_argument("--out-graphml", default=None, help="Write a GraphML (XML) file to this path.")
    p.add_argument("--pretty", action="store_true", help="Pretty-print the JSON output.")
    return p


def main(argv=None) -> int:
    """CLI entry point."""
    net.ensure_utf8_stdout()
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
        svg = render_svg(nodes, edges, layout=args.layout)
        with open(args.out_svg, "w", encoding="utf-8") as fh:
            fh.write(svg)
        sys.stderr.write(
            f"wrote SVG graph ({args.layout} layout) to {os.path.abspath(args.out_svg)}\n"
        )

    if args.out_dot:
        with open(args.out_dot, "w", encoding="utf-8") as fh:
            fh.write(render_dot(nodes, edges))
        sys.stderr.write(f"wrote GraphViz DOT to {os.path.abspath(args.out_dot)}\n")

    if args.out_graphml:
        with open(args.out_graphml, "w", encoding="utf-8") as fh:
            fh.write(render_graphml(nodes, edges))
        sys.stderr.write(f"wrote GraphML to {os.path.abspath(args.out_graphml)}\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())
