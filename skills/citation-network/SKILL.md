---
name: citation-network
description: Use when the user wants to explore the citation graph around a specific paper — "show me what this paper cites and what cites it", "build a 2-hop reference graph around arXiv 2401.12345", "この論文の引用ネットワークを可視化して". Walks Semantic Scholar's graph API breadth-first from one or more seed papers up to a depth limit and emits a JSON node/edge graph plus optional SVG (circle or force layout), GraphViz DOT, and GraphML outputs. Do NOT use for flat paper search (paper-search), single-paper summaries (paper-summarize), or Zotero export (zotero-export).
---

# Citation Network

Build a small citation graph around one or more seed papers using
Semantic Scholar's graph API.

## When to use

Trigger when the user asks about citation structure around a known paper,
for example:

- "what does arXiv 2401.12345 cite?"
- "build a 2-hop reference graph from this paper"
- "which papers cite the dense retrieval paper?"
- "この論文の引用関係を図にして"

Do NOT trigger when:

- The user is discovering papers by topic (use `paper-search`).
- The user wants to read one paper (use `paper-summarize`).
- The user wants a BibTeX/Zotero export (use `zotero-export`).

## Prerequisites

- Python 3.8+ on PATH. Stdlib only; no pip installs required.
- Network access to `api.semanticscholar.org`.
- Optional: `SEMANTIC_SCHOLAR_API_KEY` env var or `--api-key` for higher
  rate limits. Free tier is ~100 requests per 5 minutes; the script
  sleeps 1.1 s between calls to stay within quota.

## How to run

The skill folder contains `citation_network.py`. Call it with one or
more seed IDs:

```
python skills/citation-network/citation_network.py \
    --seed 2401.12345 --depth 1 --max-nodes 40
python skills/citation-network/citation_network.py \
    --seed 10.1145/3442188.3445922 --direction citations
python skills/citation-network/citation_network.py \
    --seed 2401.12345 --depth 2 --direction both \
    --out-json graph.json --out-svg graph.svg
```

Flags:

| Flag | Purpose |
| --- | --- |
| `--seed` | seed paper ID (arXiv, DOI, or S2 paperId); repeat for multi-seed |
| `--depth` | BFS depth (default 1; try 2 with caution — node count explodes) |
| `--direction` | `references` (default), `citations`, or `both` |
| `--max-nodes` | hard cap on graph size (default 50, max 300) |
| `--layout` | SVG layout: `circle` (default, good ≲ 30 nodes) or `force` (Fruchterman-Reingold, scales) |
| `--out-json` | write graph JSON to disk instead of stdout |
| `--out-svg` | write an SVG visualization to this path |
| `--out-dot` | write a GraphViz DOT file (render with `dot -Tpng`, `neato`, etc.) |
| `--out-graphml` | write a GraphML file (readable by Gephi, yEd, networkx) |
| `--api-key` | Semantic Scholar API key (or use `SEMANTIC_SCHOLAR_API_KEY`) |

## Script output contract

The script emits JSON to stdout (or `--out-json`) with this shape:

```
{
  "seed_count": 1,
  "node_count": 38,
  "edge_count": 62,
  "direction": "references",
  "depth": 1,
  "nodes": [
    {"paperId": "...", "title": "...", "authors": [...], "year": 2024,
     "venue": "NeurIPS", "arxivId": "2401.12345", "doi": "10.48550/..."}
  ],
  "edges": [{"src": "<citer>", "dst": "<cited>", "kind": "references"}]
}
```

Edges are always **citer → cited**, regardless of the direction flag.
`kind` records which API call discovered the edge.

## How to present results

1. Parse the JSON. Report `node_count`, `edge_count`, `direction`, and
   `depth` to the user up front.
2. Render a Markdown summary:
   - Which papers are the **hubs** (highest in-degree if `direction` is
     `references`; highest out-degree if `citations`). Name the top 3–5.
   - Which **clusters or themes** appear, based on titles and venues.
   - Any **bridge papers** that connect otherwise-disconnected subgraphs.
3. If `--out-svg` was written, offer the absolute path to the SVG and
   tell the user it uses a simple circle layout — seed nodes are orange,
   others blue. Labels show `LastName Year`.
4. If the user asked for a deeper walk (`--depth 2+`), warn that the
   node cap may have truncated the frontier. The output's `node_count ==
   max_nodes` is a reliable truncation signal.

## Failure handling

- **Seed could not be resolved** — the script prints `could not resolve
  seed '<id>'` to stderr and continues with the remaining seeds.
  Possible causes: typo in the ID, non-indexed paper, S2 outage. Tell
  the user to verify the ID with `paper-search` first.
- **S2 throttled (HTTP 429)** — the script sleeps 3 s and retries once.
  If it still fails, surface "Semantic Scholar is rate-limiting; wait a
  minute or provide an API key."
- **Empty graph** — the seed was resolved but returned zero references/
  citations. Common for very new papers. Suggest `--direction both` or a
  different seed.
- **Max nodes reached** — tell the user the BFS was truncated at
  `--max-nodes`. The graph is a sample, not exhaustive.

## Implementation notes

- Identifier resolution: arXiv IDs become `arXiv:<id>` (version stripped),
  DOIs become `DOI:<doi>`, everything else is passed through as a native
  S2 paperId.
- **Layouts**: `circle` is deterministic and legible up to ~60 nodes.
  `force` runs an in-process Fruchterman-Reingold spring simulation
  (120 iterations, stdlib only, seeded for reproducibility) and handles
  denser graphs better. For even bigger graphs pipe `--out-dot` to
  GraphViz's `sfdp`/`neato` or `--out-graphml` to Gephi.
- **DOT output** is a plain digraph with node/edge styling preserved.
  Render with `dot -Tpng graph.dot -o graph.png` or
  `neato -Tsvg graph.dot -o graph.svg`.
- **GraphML output** exposes `title`, `year`, `venue`, and `is_seed`
  as typed node attributes, ready for `networkx.read_graphml()`.
- Rate limiting and caching are now handled by the shared `_lib`:
  the S2 rate limiter enforces ~1 req/s (cross-process), metadata is
  cached for 7 days, and reference lists are cached immutably so a
  repeat walk is near-instant.

## Chaining

`paper-search` → pick one result → `citation-network --seed <id>` is the
canonical exploration chain. When a hub paper pops out of the graph,
feed its ID to `paper-summarize` for a deep-read.
