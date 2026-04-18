---
name: paper-search
description: Use when the user wants to find academic papers by topic, author, or keyword — "find papers on X", "latest work on Y", "arXiv で〜を探して", "search for recent papers about Z". Queries arXiv via its public Atom API (no auth needed) and returns a ranked list of titles, authors, years, abstracts, and links. MVP is arXiv-only; Semantic Scholar and PubMed are planned follow-ups. Do NOT use for summarizing a single known paper (that is paper-summarize) or building citation graphs (citation-network).
---

# Paper Search (arXiv MVP)

Find academic papers from arXiv for the user and return a ranked, readable
summary in chat. This is the entry point to the `arxiv-research-toolkit`
plugin; downstream skills like `paper-summarize` and `lit-review-draft` take
this skill's output as input.

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

- Python 3.8+ on PATH. No pip installs required — the script uses stdlib
  only (`urllib`, `xml.etree.ElementTree`, `argparse`, `json`).
- Network access to `export.arxiv.org`.

## How to run

The skill folder contains `search_arxiv.py`. Call it with the user's query:

```
python skills/paper-search/search_arxiv.py --query "mixture of experts" --max-results 10
```

Useful flags:

| Flag | Purpose |
| --- | --- |
| `--query` | free-text query, required |
| `--max-results` | integer, default 10, hard cap 50 |
| `--year-from` / `--year-to` | integer year filters, inclusive |
| `--category` | arXiv category like `cs.LG`, `cs.CL` |
| `--sort` | `relevance` (default) or `submittedDate` |

The script prints a JSON array to stdout. Each record has:
`id, source, title, authors, year, venue, abstract, url, pdf_url,
categories, published`.

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
- `year_from` / `year_to` are applied client-side after the fetch — arXiv's
  query language does not expose a clean date range for `all:` searches.
- Semantic Scholar and PubMed sources are planned but NOT implemented in
  this MVP. See `docs/skills/paper-search.md` for the design.

## Extending

To add a new source (Semantic Scholar, PubMed), drop a
`search_<source>.py` next to `search_arxiv.py` with the same CLI surface
and JSON record shape (see `docs/skills/paper-search.md` section 5.2).
Unified multi-source dispatch is a stretch goal; for now each source is a
standalone script.
