---
name: lit-review-draft
description: Use when the user wants a MULTI-paper literature review draft — "draft a lit review on X across these papers", "synthesize this bundle of papers into a related-work section", "これらの論文から文献レビューを起こして". Takes a JSON array of paper records (from paper-search or hand-assembled), assigns BibTeX-friendly cite keys, and hands Claude a consolidated envelope to draft a themed review with gap analysis. Do NOT use for single-paper summaries (paper-summarize) or for searching (paper-search).
---

# Literature Review Draft

Turn a set of papers into a themed literature review draft with cite keys
and a gap analysis.

## When to use

Trigger when the user wants a synthesis *across* papers, for example:

- "draft a lit review on retrieval augmentation from these 20 papers"
- "write a related-work section about mixture-of-experts using this JSON"
- "これらの論文をテーマ別にまとめて関連研究セクションを書いて"
- "summarize the 2022–2025 speculative decoding literature"

Do NOT trigger when:

- The user has one paper and wants a deep-read (use `paper-summarize`).
- The user is still searching (use `paper-search`).
- The user wants a visual citation graph (use `citation-network`).

## Prerequisites

- Python 3.8+ on PATH. Stdlib only; no pip installs required.
- A JSON array of paper records conforming to the `paper-search` schema:
  `{id, source, title, authors[], year, venue, abstract, url, ...}`.
  Records from `paper-search` already fit; hand-assembled lists only
  need those keys to be populated.

## How to run

The skill folder contains `prepare_review.py`. Call it with the input
paper list and a topic:

```
python skills/lit-review-draft/prepare_review.py \
    --input ./arxiv-search-rag-20260101.json \
    --topic "retrieval augmentation" \
    --length medium \
    --pretty
```

Or pipe the JSON in via stdin:

```
python search_arxiv.py --query "rag" --max-results 25 \
    | python skills/lit-review-draft/prepare_review.py \
        --stdin --topic "retrieval augmentation"
```

Useful flags:

| Flag | Purpose |
| --- | --- |
| `--input` *or* `--stdin` | paper list source (exactly one, required) |
| `--topic` | framing topic, required — feeds section suggestions |
| `--length` | `short` / `medium` / `long`, default `medium` |
| `--max-papers` | cap papers forwarded to Claude, default 50 |
| `--pretty` | pretty-print the JSON envelope |

## Script output contract

The script prints one JSON envelope with this shape:

```
{
  "topic": "...",
  "target_length": "medium",
  "paper_count": 20,
  "papers": [
    {"cite_key": "Smith2024rag", "id": "...", "title": "...", ...},
    ...
  ],
  "year_histogram": {"2022": 3, "2023": 8, "2024": 9},
  "suggested_sections": ["Motivation and scope", "..."]
}
```

`cite_key` is `<Surname><Year><titleSlug>` with lowercase alpha-only slug
(e.g. `Karpukhin2020denseretrieval`). Collisions get a trailing `a`, `b`,
`c`... suffix. Use these keys verbatim in the draft.

## How to present the review

1. Read the envelope; note `target_length` and `suggested_sections`.
2. Draft the review in Markdown with these sections (tune to topic):
   - **Motivation and scope** — why this area, what the review covers.
   - **Foundational work on <topic>** — the 2–4 seminal papers.
   - **Methodological families** — group papers by technique or lens.
   - **Temporal trends** (only if year range ≥ 3 years).
   - **Empirical findings and benchmarks** — what has been measured.
   - **Open problems and gaps** — explicit gap analysis; do not skip.
3. Cite papers inline using the `cite_key` from the envelope, e.g.
   `[Smith2024rag]`, not footnote numbers.
4. Length hint:
   - `short` → ~400 words, one paragraph per section.
   - `medium` → ~900 words (default).
   - `long` → ~1800 words, multiple paragraphs per section.
5. Emit a `References` section at the end in a BibTeX-flavored list:
   ```
   [Smith2024rag] Smith, J. et al. (2024). Retrieval Augmentation for X. arXiv:2401.12345.
   ```
6. If `paper_count < 4`, tell the user the corpus is probably too thin
   for a meaningful review and suggest running `paper-search` first.

## Failure handling

- **Input is not a JSON array** — the script exits 1 with a message. Tell
  the user the input must be a JSON array (or an object with a `papers`
  key) of records.
- **Empty array** — the script will still emit an envelope with
  `paper_count: 0`. Tell the user there is nothing to synthesize.
- **Missing abstracts** — the draft will be shallow. Flag the papers with
  empty `abstract` fields and suggest running `paper-summarize` on them
  first, or re-running `paper-search` which always returns abstracts.

## Implementation notes

- Cite key generation is deliberately deterministic — re-running with the
  same input yields the same keys, which matters for iterative drafting.
- The script does NOT fetch anything. It is a pure transform over the
  input list. If the caller wants enriched abstracts or full-text
  extracts, run `paper-summarize` upstream and merge the text into the
  records before calling this skill.
- Section suggestions are just defaults — tune them to the corpus.

## Chaining

`paper-search` (list) → `lit-review-draft` (synthesis) is the canonical
chain. If the user wants Zotero or BibTeX output of the same paper set,
feed the raw search JSON to `zotero-export`.
