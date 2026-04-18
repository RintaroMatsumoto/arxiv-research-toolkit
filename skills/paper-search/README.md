# paper-search

MVP skill for the `arxiv-research-toolkit` plugin. Queries arXiv's public
Atom API and returns a ranked JSON list of papers. stdlib-only; no pip
installs required.

## Files

- `SKILL.md` — the prompt Claude reads when the skill activates. Declares
  trigger phrases, negative triggers, input flags, and how to render the
  script's JSON output as an in-chat Markdown table.
- `search_arxiv.py` — standalone Python 3.8+ CLI. Takes `--query` plus
  optional `--max-results`, `--year-from`, `--year-to`, `--category`,
  `--sort`. Prints a JSON array to stdout. Retries once on transient
  network failures with a 3-second back-off.

## Orchestration

When a trigger phrase fires, Claude follows `SKILL.md`:

1. Call `python search_arxiv.py --query "..." [flags]`.
2. Parse the JSON array from stdout.
3. Render a Markdown table (title, authors, year, arXiv ID, link) plus a
   short abstract snippet per row.
4. On zero results, network error, or rate limit, surface the failure
   messages listed in `SKILL.md`.

## Design doc

See `docs/skills/paper-search.md` at the repo root for the full design,
canonical JSON record schema, deduplication plan for future sources, and
MVP-vs-stretch scope.

## Not in this MVP

Semantic Scholar and PubMed backends are stubs in the design doc. They
will land as sibling scripts (`search_semantic_scholar.py`,
`search_pubmed.py`) with the same CLI surface and canonical record shape.
