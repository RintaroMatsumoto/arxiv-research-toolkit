# arxiv-research-toolkit — Design Document

> Status: **Pre-release / design stage.** No code has been written.
> This document captures intent. It will be split into ADRs once we begin implementation.

## 1. Architecture overview

The plugin is packaged as a Claude plugin (this repo) plus a thin Python backend that wraps the APIs of the three targeted sources.

```
Claude (user session)
  └─ plugin: arxiv-research-toolkit
       ├─ skills/paper-search/       → calls Python backend
       ├─ skills/paper-summarize/    → Claude API over fetched PDF text
       ├─ skills/lit-review-draft/   → Claude API over paper set
       ├─ skills/zotero-export/      → local Zotero HTTP server (23119)
       └─ skills/citation-network/   → Semantic Scholar graph endpoints
Python backend (invoked by skills)
  ├─ clients/arxiv_client.py           (uses `arxiv` package)
  ├─ clients/semantic_scholar_client.py (uses `requests`)
  ├─ clients/pubmed_client.py          (E-utilities via `requests`)
  ├─ pdf/extract.py                    (uses `pypdf`; OCR fallback TBD)
  └─ cache/                            (filesystem cache — strategy TBD)
```

Rationale: skills stay small and Claude-readable; Python handles auth headers, pagination, rate-limits, and PDF parsing which would be awkward to do purely in skill prompts.

## 2. Skill-by-skill plan

### 2.1 paper-search

- **Inputs**: free-text query, optional filters (`year_from`, `year_to`, `sources` subset of {arxiv, semantic_scholar, pubmed}, `limit`).
- **Outputs**: unified JSON list — `{id, source, title, authors[], year, abstract, pdf_url, doi?, citations?}`.
- **Dependencies**: `arxiv`, `requests`. Semantic Scholar API key optional (higher rate limit).

### 2.2 paper-summarize

- **Inputs**: paper identifier (arXiv ID, DOI, or S2 paper ID) OR a local PDF path.
- **Outputs**: markdown summary with sections `TL;DR`, `Key contributions`, `Method`, `Results`, `Limitations`, `Open questions`.
- **Dependencies**: `pypdf` for text extraction, `anthropic` for Claude API calls, network access for remote PDFs.

### 2.3 lit-review-draft

- **Inputs**: list of paper IDs (from `paper-search`), a topic/framing string, desired length.
- **Outputs**: markdown literature review draft with grouped themes, citation keys (BibTeX-compatible), and a gap-analysis section.
- **Dependencies**: output of `paper-summarize` as upstream; `anthropic` for synthesis.

### 2.4 zotero-export

- **Inputs**: paper list (same shape as `paper-search` output), target Zotero collection name (optional).
- **Outputs**: items written into the user's local Zotero via HTTP, plus a `.bib` file for offline use.
- **Dependencies**: Zotero desktop running with its local HTTP connector on port 23119, `requests`.

### 2.5 citation-network

- **Inputs**: seed paper ID(s), max depth, max nodes.
- **Outputs**: JSON node/edge graph + an SVG or HTML visualization.
- **Dependencies**: Semantic Scholar graph endpoints, a lightweight graph layout library (TBD — `networkx` + simple SVG emitter, no heavy JS framework).

## 3. Dependencies decision

Minimum viable dependency set (pinned loosely until MVP):

- `arxiv` — official-ish Python client; handles ATOM parsing and rate-limit etiquette.
- `requests` — HTTP for Semantic Scholar, PubMed E-utilities, and Zotero local server.
- `pypdf` — pure-Python PDF text extraction; no native build needed on Windows.
- `anthropic` — Claude API client for summarization and literature-review synthesis.

Deliberately excluded for now: `pymupdf` (native deps), heavy NLP stacks (`spacy`, `nltk`), vector DBs. If needed later, they will be introduced behind an extras group (`pip install arxiv-research-toolkit[ocr]`).

## 4. Zotero integration approach

Zotero exposes a local HTTP server at `http://127.0.0.1:23119/` when the desktop app is running. The plan:

1. Probe `http://127.0.0.1:23119/connector/ping` on skill start; fail with a clear message if Zotero is not running.
2. For each paper, POST to `/connector/saveItems` with a translated item payload (type, creators, title, abstractNote, DOI, url, tags).
3. Additionally emit a `.bib` file as a fallback export so the skill is still useful offline.

This avoids requiring the user to manage a Zotero web API key and keeps data on-device.

## 5. Open questions

- **PDF OCR fallback** — many older arXiv PDFs and virtually all scanned PubMed PDFs fail `pypdf` extraction. Do we bundle Tesseract via an optional extra, call a cloud OCR service, or simply warn and skip?
- **Rate limits** — arXiv asks for ~1 req/3s; Semantic Scholar free tier is ~100 req/5min; PubMed E-utilities is 3 req/s without an API key. Should we share a single token-bucket across skills, or let each client self-throttle?
- **Cache strategy** — file-based (`~/.cache/arxiv-research-toolkit/`) vs per-project (`./.arxiv-cache/`). TTL per source? Invalidation on DOI vs query-string? Needs a decision before `paper-search` ships.
- **Identifier normalization** — arXiv ID vs DOI vs Semantic Scholar paperId. Likely need a small resolver; who owns it?
- **Summarization cost** — long PDFs could be expensive via Claude API. Cap tokens per paper? Offer a cheaper "abstract-only" mode?

## 6. Milestones

- **MVP** — `paper-search` + `paper-summarize`. Enough to be useful as a solo research assistant.
- **V1** — add `lit-review-draft`. Turns the plugin into a drafting tool, not just a retriever.
- **V2** — add `zotero-export` + `citation-network`. Makes it viable for serious lit-review workflows and graph exploration.

Each milestone will be tagged (`v0.1.0`, `v0.2.0`, `v0.3.0`) and only then submitted to the Anthropic official marketplace.
