---
name: paper-search
description: Use when the user wants to find academic papers by topic, author, or keyword — "find papers on X", "latest work on Y", "arXiv で〜を探して", "search for recent papers about Z". Queries arXiv via its public Atom API and Semantic Scholar via its graph API, and returns a ranked list of titles, authors, years, abstracts, and links. Records are schema-compatible across backends. Do NOT use for summarizing a single known paper (that is paper-summarize) or building citation graphs (citation-network).
---

# Paper Search (arXiv + Semantic Scholar)

Find academic papers for the user across multiple public sources and
return a ranked, readable summary in chat. This is the entry point to
the `arxiv-research-toolkit` plugin; downstream skills like
`paper-summarize` and `lit-review-draft` take this skill's output as
input.

Two backends are available:

- **arXiv** (`search_arxiv.py`) — fast, no auth, CS / physics / math
  focus. Use when the user mentions arXiv explicitly, or when the topic
  is strongly ML / physics / math.
- **Semantic Scholar** (`search_semantic_scholar.py`) — broader
  coverage (biology, medicine, social science), supports
  author/venue/year filters server-side. Use when the user wants
  cross-field search or when arXiv returns too few hits.

## When to use

Trigger when the user expresses a literature-search intent, for example:

- "find papers on diffusion models for video"
- "latest work on transformer efficiency"
- "arXiv で mixture of experts を探して"
- "what has been published on speculative decoding since 2024?"

Do NOT trigger when:

- The user names one specific paper and wants its contents (use
  `paper-summarize`).
- The user wants a citation graph (use `citation-network`).
- The user wants a Zotero/BibTeX export of an existing list (use
  `zotero-export`).

## Prerequisites

- Python 3.8+ on PATH. No pip installs required — the scripts use stdlib
  only (`urllib`, `xml.etree.ElementTree`, `argparse`, `json`).
- Network access to `export.arxiv.org` and/or `api.semanticscholar.org`.
- Optional: `SEMANTIC_SCHOLAR_API_KEY` env var for higher S2 rate limits.

## How to run

### arXiv backend

```
python skills/paper-search/search_arxiv.py --query "mixture of experts" --max-results 10
```

Flags:

| Flag | Purpose |
| --- | --- |
| `--query` | free-text query, required |
| `--max-results` | integer, default 10, hard cap 50 |
| `--year-from` / `--year-to` | integer year filters, inclusive |
| `--category` | arXiv category like `cs.LG`, `cs.CL` |
| `--sort` | `relevance` (default) or `submittedDate` |

### Semantic Scholar backend

```
python skills/paper-search/search_semantic_scholar.py --query "mixture of experts" --limit 10
python skills/paper-search/search_semantic_scholar.py --query "rag" --year-from 2023 --venue NeurIPS
```

Flags:

| Flag | Purpose |
| --- | --- |
| `--query` | free-text query, required |
| `--limit` | integer, default 10, hard cap 100 |
| `--offset` | integer, for pagination |
| `--year-from` / `--year-to` | integer year filters, inclusive (applied server-side) |
| `--venue` | restrict to a venue name (`NeurIPS`, `Nature`, etc.) |
| `--api-key` | optional, or set `SEMANTIC_SCHOLAR_API_KEY` |

Both scripts print a JSON array to stdout. Every record has the same
canonical shape: `id, source, title, authors, year, venue, abstract,
url, pdf_url, categories, published`. The `source` field is `"arxiv"`
or `"semantic-scholar"` — downstream skills handle both uniformly.

### Choosing a backend

If the user doesn't specify:

- Default to `search_arxiv.py` for CS / ML / physics / math queries.
- Reach for `search_semantic_scholar.py` when the topic is biomedical,
  social science, or humanities; when the user wants a specific
  publication venue; or when arXiv returns fewer than ~3 hits.
- If unclear, run both and deduplicate by `id` (arXiv IDs and DOIs are
  stable across sources; fall back to normalized title match).

## How to present results

1. Parse the JSON array from stdout.
2. Render a Markdown table with columns: `#`, `Title`, `Authors`, `Year`,
   `arXiv ID`, `Link` (use the `url` field).
3. Under each row, add a one-line abstract snippet (~200 chars, trim mid-
   sentence on a space, append an ellipsis).
4. If the user asked to save results for a later literature review, offer
   to write the raw JSON array to `./arxiv-search-<slug>-<timestamp>.json`
   in the current working directory. Do not write by default.

## Failure handling

- **Zero results** — tell the user the query returned nothing, suggest
  dropping the category filter, widening the year range, or broader terms.
- **Network error / DNS fail** — report "could not reach arXiv"; do not
  retry more than once.
- **HTTP 429 / throttle** — the script already sleeps 3s and retries once.
  If it still fails, tell the user arXiv is throttling and suggest waiting
  a minute.
- **Malformed XML** — surface the raw error message and recommend retrying
  the query.

## Implementation notes

- arXiv's API uses the Atom 1.0 namespace; the script parses it with
  `xml.etree.ElementTree`. If arXiv changes the feed shape, update the
  namespace constants at the top of `search_arxiv.py`.
- For the arXiv backend, `year_from` / `year_to` are applied
  client-side — arXiv's query language does not expose a clean date
  range for `all:` searches.
- For the Semantic Scholar backend, year filters are passed to S2
  server-side via `year=YYYY-YYYY` and re-applied client-side as a
  belt-and-braces safeguard.
- Both backends share SSL, UTF-8 stdout, HTTP retries, a 24 h search
  cache, and a per-host rate limiter via `skills/_lib/`. The rate
  limiter serializes concurrent callers on the same hostname across
  processes so running `search_arxiv.py` twice in parallel doesn't
  burst past the arXiv soft limit.
- PubMed is still a planned follow-up; the eutils endpoint is in the
  `_lib/rate_limit.py` defaults but no backend script exists yet.

## Extending

To add a new source (PubMed, CrossRef, …), drop a `search_<source>.py`
next to `search_arxiv.py` with the same CLI surface and JSON record
shape. Use `_lib.net`, `_lib.cache`, and `_lib.rate_limit` for
consistency with the existing backends.
