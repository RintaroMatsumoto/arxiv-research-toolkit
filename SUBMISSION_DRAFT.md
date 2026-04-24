> これは Anthropic の Claude Plugins 公式ディレクトリ提出フォームに手動で貼り付けるためのドラフトである。各フィールドはフォーム項目に 1:1 対応している。
>
> 提出順の推奨: **1 番目**（本命。v0.2.0 で 5 skills 完成、共有 `_lib/` 整備済みで要件充足度最高）

---

## arxiv-research-toolkit

- **Plugin name** (kebab-case): arxiv-research-toolkit
- **Public repository URL**: https://github.com/RintaroMatsumoto/arxiv-research-toolkit
- **Latest tag**: v0.2.0
- **Author**: Rintaro Matsumoto
- **License**: MIT
- **Homepage**: https://github.com/RintaroMatsumoto/arxiv-research-toolkit
- **Category (candidate)**: research — every skill targets the academic-paper lifecycle (search, summarize, review, export, graph), which is a tighter fit than the generic "productivity" bucket.
- **Keywords (5-8)**: research, arxiv, semantic-scholar, literature-review, zotero, bibtex, citation-graph, pdf-summarize

### Short tagline (<=60 chars, English)
Search arXiv, summarize PDFs, draft reviews, export to Zotero.

### Description (plain English, ~450 chars)
A research toolkit that keeps the academic-paper loop inside a Claude session. Five skills cover the common moves: query arXiv and Semantic Scholar, fetch a PDF and produce a structured TL;DR with scanned-page detection, consolidate a paper set into a themed literature-review draft with BibTeX-ready cite keys, export to the local Zotero connector or Web API (always with a `.bib` fallback), and walk the citation graph from seed papers to JSON and SVG. Shared `_lib/` handles HTTP, caching, and rate limits so each skill stays stdlib-only where possible.

### Differentiators (3, English)
- Stdlib-only Python for four of five skills; only `paper-summarize` needs `pypdf`. Installs clean on a fresh machine.
- One canonical paper-record schema plus stable BibTeX cite keys flow unchanged through every skill, so outputs compose without glue code.
- Zotero export always emits a `.bib` sibling even when the desktop connector is offline, so the skill degrades gracefully in CI and headless environments.

### Included skills (from plugin.json / skills/)
- paper-search - Query arXiv and Semantic Scholar and return a ranked Markdown table.
- paper-summarize - Fetch a PDF (ID, URL, or local path) and produce a structured summary.
- lit-review-draft - Consolidate a paper set into a themed review with cite keys and gap analysis.
- zotero-export - Push papers to the local Zotero connector or Web API; always writes `.bib`.
- citation-network - Walk Semantic Scholar citations from seed papers; outputs JSON, SVG, DOT, GraphML.

### Reviewer trial path (<=5 lines)
1. `/plugin install arxiv-research-toolkit`
2. Say "search arXiv for 'diffusion transformers' and return the top 10".
3. Say "summarize arXiv 2312.00752".
4. Say "draft a short literature review from the search results".
5. Say "export that set to BibTeX at ./papers.bib".

### Notes / Caveats
- `paper-summarize` requires `pip install pypdf`; all other skills are stdlib-only.
- Optional `SEMANTIC_SCHOLAR_API_KEY` lifts the rate-limit tier for `citation-network`.
- Zotero connector at `localhost:23119` is optional; `.bib` fallback always works.
