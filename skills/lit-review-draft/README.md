# lit-review-draft

MVP skill for the `arxiv-research-toolkit` plugin. Transforms a paper-
search JSON array into a review-ready envelope — cite keys, year
histogram, suggested sections — and hands it to Claude to draft a themed
literature review with gap analysis.

## Files

- `SKILL.md` — prompt Claude reads on activation. Declares trigger
  phrases, the `prepare_review.py` CLI contract, the envelope shape, and
  the target Markdown structure (sections, cite keys, references list).
- `prepare_review.py` — Python 3.8+ stdlib CLI. Takes `--input <json>` or
  `--stdin`, plus `--topic`, and prints a JSON envelope to stdout.

## Orchestration

1. User hands Claude a JSON paper list (usually from `paper-search`) and
   asks for a review on a topic.
2. Claude calls `python prepare_review.py --input ... --topic "..."`.
3. Claude drafts the review using the envelope's cite keys and section
   suggestions, honoring the `target_length` hint.

## Dependencies

- Python 3.8+ stdlib only. No pip installs.

## Not in this MVP

- Does not fetch full-text or enriched abstracts. Run `paper-summarize`
  upstream and merge its output into the records before calling this
  skill if deeper content is needed.
- Does not emit BibTeX — that is `zotero-export`'s job.
