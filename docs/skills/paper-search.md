# paper-search — Skill Design

> Status: **MVP scaffold.** arXiv-only. Semantic Scholar and PubMed are stubs.

## 1. Purpose

Let a Claude user query academic literature without leaving the chat context.
The skill takes a free-text research question, hits a public paper API, and
returns a ranked, de-duplicated list of papers with enough metadata to decide
which ones deserve a deeper read. It is the entry point for the rest of the
toolkit: its output feeds `paper-summarize` (single-paper deep-read) and
`lit-review-draft` (multi-paper synthesis).

## 2. Triggering phrases

The skill should activate when the user expresses a literature-search intent.
Examples (non-exhaustive):

- "find papers on diffusion models for video"
- "latest work on transformer efficiency"
- "search arXiv for retrieval augmented generation"
- "arXiv で mixture of experts を探して"
- "最近の RLHF の論文を 10 本教えて"
- "what has been published on speculative decoding since 2024?"

Negative triggers (should NOT fire): requests to summarize a single known
paper (that is `paper-summarize`), requests to build a citation graph (that
is `citation-network`), requests to export to Zotero (that is `zotero-export`).

## 3. Data sources

| Source | Endpoint | Auth | Rate limit | MVP? |
| --- | --- | --- | --- | --- |
| arXiv | `http://export.arxiv.org/api/query` (Atom) | none | ~1 req / 3 s | **Yes (default)** |
| Semantic Scholar | `https://api.semanticscholar.org/graph/v1/paper/search` (JSON) | none (key optional) | ~100 req / 5 min | No (stub) |
| PubMed | NCBI E-utilities (`esearch` + `esummary`) | none | 3 req / s | No (stub) |

### 3.1 MVP default: arXiv

arXiv is the MVP default because:

1. **Target audience fit.** Rintaro and the freelance-AI-researcher persona
   live almost entirely on arXiv for CS/ML work. Semantic Scholar adds
   cross-field coverage; PubMed serves biomedical users we are not targeting
   yet.
2. **Simplest contract.** Atom feed, stable URL shape, no key, no quota
   account. A correctness bug ships as a 30-line stdlib fix, not a vendor
   ticket.
3. **Canonical identifier.** The arXiv ID is already the de-facto primary
   key for the rest of the toolkit (`paper-summarize` fetches
   `https://arxiv.org/pdf/<id>.pdf`; `citation-network` resolves IDs through
   Semantic Scholar later). Starting arXiv-only means no identifier-
   normalization work in the MVP.

### 3.2 Stub sources (TODO)

- **Semantic Scholar** — add `search_semantic_scholar.py` mirroring the
  arXiv script's CLI shape. Needs fields: `paperId, title, authors, year,
  venue, abstract, url, externalIds.DOI, externalIds.ArXiv`.
- **PubMed** — add `search_pubmed.py` using `esearch.fcgi` followed by
  `esummary.fcgi`. Lower priority; only ship when a biomedical user asks.

## 4. Inputs

| Param | Type | Default | Notes |
| --- | --- | --- | --- |
| `query` | string | required | free-text; passed through to arXiv `search_query=all:<q>` |
| `max_results` | int | 10 | hard cap 50 in MVP to avoid long chat dumps |
| `year_from` | int | none | client-side filter on `published` year |
| `year_to` | int | none | client-side filter on `published` year |
| `category` | string | none | arXiv category like `cs.LG`, `cs.CL`; ANDed into the query |
| `sort` | enum | `relevance` | `relevance` or `submittedDate` |

Stretch inputs (not in MVP): author filter, venue filter, citation-count
threshold (requires Semantic Scholar).

## 5. Output

### 5.1 Default: inline Markdown table

When the user asks a casual search question, Claude hands back a ranked
Markdown table:

| # | Title | Authors | Year | arXiv ID | Link |
| - | ----- | ------- | ---- | -------- | ---- |

...followed by a short abstract snippet (~200 chars) per row as a nested
bullet. This is the ergonomic path for "just tell me what's out there."

### 5.2 On request: JSON / BibTeX on disk

When the user says "save these for later" or the caller is another skill
(`lit-review-draft`), Claude writes to `./arxiv-search-<slug>-<timestamp>.json`
with the raw result array and optionally a sibling `.bib`. The JSON record
shape:

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
  "categories": ["cs.LG", "cs.CL"],
  "published": "2024-01-18T12:00:00Z"
}
```

This schema is the canonical contract for the rest of the toolkit. Any new
source must map onto it.

## 6. Deduplication (forward-looking)

Not needed in MVP (single source). When Semantic Scholar lands:

1. Prefer records with an `externalIds.ArXiv` match — merge into the arXiv
   row, enrich with `citationCount` and `venue`.
2. Fall back to normalized-title match (lowercase, strip punctuation, first
   80 chars) for pre-prints that lack a cross-link.
3. Keep the arXiv version as canonical when both sources have it; Semantic
   Scholar entries without an arXiv counterpart are appended at the tail of
   the ranking.

## 7. Failure modes

| Failure | User-visible message |
| --- | --- |
| Network unreachable / DNS fail | "Could not reach arXiv. Check your connection and retry." |
| HTTP 5xx from arXiv | "arXiv returned a server error. Retry in ~30s; arXiv is usually transient." |
| Zero results | "No arXiv papers matched `<query>`. Try broader terms, drop the category filter, or widen the year range." |
| Rate-limited (HTTP 429 or throttle) | "arXiv throttled the request. Waiting 3s and retrying." Retry once, then surface a clear message. |
| Malformed Atom XML | "arXiv returned an unexpected response. This is usually transient — retry, or open an issue with the raw query." |

The MVP retries once with a 3-second back-off on transient errors (HTTP 5xx
or `429`), then gives up. No exponential schedule yet.

## 8. Scope

### 8.1 MVP (this PR)

- arXiv-only search via Atom API, stdlib Python.
- CLI: `python search_arxiv.py --query "..." --max-results N`.
- JSON-to-stdout; Markdown rendering happens in Claude's response.
- `SKILL.md` explains when to trigger and how to call the script.
- No caching, no retries beyond one, no persistent output file.

### 8.2 Stretch (later PRs, in order)

1. `search_semantic_scholar.py` mirroring arXiv's CLI.
2. Unified multi-source dispatcher + dedup rules from section 6.
3. Disk output mode (`--save-json`, `--save-bib`).
4. Shared file cache at `~/.cache/arxiv-research-toolkit/search/` keyed by
   `sha256(source + normalized_query + filters)`, 24-hour TTL.
5. Token-bucket rate limiter shared across search scripts.
6. `search_pubmed.py` for biomedical users.

## 9. Open questions (for Rintaro)

- **Result cap.** Is 50 the right hard cap, or do we want `--max-results`
  to go up to a few hundred with pagination? Impacts whether we page the
  Atom feed in MVP.
- **Default sort.** arXiv's `relevance` is notoriously noisy for recent
  topics. Should the default flip to `submittedDate` descending?
- **Output destination.** Do we want the Markdown table in-chat by
  default, or always write a sidecar JSON so later skills can chain without
  re-searching?
