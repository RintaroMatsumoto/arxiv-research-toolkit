# arxiv-research-toolkit

Research toolkit for Claude. Search, summarize, synthesize, export, and
explore academic papers from arXiv and Semantic Scholar, right inside a
Claude session.

`v0.1.0` — five skills shipped as an alpha. The surface will stay
backward-compatible during the `0.1.x` line; breaking changes wait for
`0.2.0`.

## Skills

- **paper-search** `[alpha]` — Query arXiv via its public Atom API and
  return a ranked Markdown table of papers. Stdlib-only.
- **paper-summarize** `[alpha]` — Fetch a paper PDF (arXiv ID, URL, or
  local path), extract its text, and produce a structured summary (TL;DR
  / Key contributions / Method / Results / Limitations / Open questions).
- **lit-review-draft** `[alpha]` — Consolidate a paper set (JSON from
  `paper-search` or hand-assembled) into a themed literature review
  draft with BibTeX-friendly cite keys and a gap analysis.
- **zotero-export** `[alpha]` — Push a paper set into the local Zotero
  connector (port 23119) and always emit a sibling `.bib` file as a
  fallback. Stdlib-only.
- **citation-network** `[alpha]` — Walk Semantic Scholar's citation
  graph from one or more seed papers and emit a JSON node/edge graph
  plus an optional SVG.

## Requirements

- Python 3.8+ on PATH.
- `pypdf` for `paper-summarize` only: `pip install pypdf`. Other skills
  are stdlib-only.
- Zotero desktop 6.x+ running locally for the Zotero leg of
  `zotero-export` (optional — BibTeX output works without it).
- Optional: `SEMANTIC_SCHOLAR_API_KEY` env var for
  `citation-network`'s higher rate-limit tier.

## Canonical paper-record schema

Every skill that exchanges paper data uses the same record shape:

```json
{
  "id": "2401.12345",
  "source": "arxiv",
  "title": "...",
  "authors": ["..."],
  "year": 2024,
  "venue": "arXiv",
  "abstract": "...",
  "url": "https://arxiv.org/abs/2401.12345",
  "pdf_url": "https://arxiv.org/pdf/2401.12345.pdf",
  "categories": ["cs.LG"],
  "published": "2024-01-18T12:00:00Z"
}
```

Chaining is deliberate: `paper-search → paper-summarize → lit-review-draft`
for a drafting workflow, and `paper-search → zotero-export` for
library management. `citation-network` takes any single paper ID as a
seed.

## Design and ADRs

Overall architecture and open questions live in
[`DESIGN.md`](DESIGN.md). Per-skill design notes are in
[`docs/skills/`](docs/skills/).

## License

MIT — see [LICENSE](LICENSE).
