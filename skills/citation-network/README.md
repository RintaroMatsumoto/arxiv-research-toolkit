# citation-network

MVP skill for the `arxiv-research-toolkit` plugin. Walks Semantic
Scholar's citation graph breadth-first from one or more seed papers and
emits a JSON node/edge graph plus an optional SVG.

## Files

- `SKILL.md` — prompt Claude reads on activation. Declares trigger
  phrases, the `citation_network.py` CLI contract, how to interpret the
  JSON graph, and how to describe hubs / clusters / bridge papers.
- `citation_network.py` — Python 3.8+ stdlib CLI. Takes `--seed`
  (repeatable), `--depth`, `--direction`, `--max-nodes`, and optional
  `--out-json`, `--out-svg`, `--api-key`.

## Orchestration

1. User picks a seed paper (often from `paper-search` results).
2. Claude calls
   `python citation_network.py --seed 2401.12345 --depth 1 --out-svg g.svg`.
3. Claude reads the JSON, reports node/edge counts, names the hubs,
   and shares the SVG path when requested.

## Dependencies

- Python 3.8+ stdlib only. No pip installs.
- Semantic Scholar graph API. Free tier rate-limited to ~100 req / 5 min.
  Set `SEMANTIC_SCHOLAR_API_KEY` or pass `--api-key` for a higher quota.

## Not in this MVP

- Rich graph layouts (force-directed, hierarchical). The SVG uses a
  simple circle layout; route large graphs to Gephi/Cytoscape via
  `--out-json`.
- Deduplication across Semantic Scholar versions of the same paper.
  S2's `paperId` is used as the primary key for now.
