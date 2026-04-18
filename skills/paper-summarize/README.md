# paper-summarize

MVP skill for the `arxiv-research-toolkit` plugin. Fetches a paper PDF,
extracts plain text via `pypdf`, and hands it to Claude to produce a
structured summary.

## Files

- `SKILL.md` — prompt Claude reads on activation. Declares trigger
  phrases, the `fetch_pdf.py` CLI contract, and the output structure
  (TL;DR / Key contributions / Method / Results / Limitations / Open
  questions).
- `fetch_pdf.py` — Python 3.8+ CLI. One of `--arxiv-id`, `--url`, or
  `--path` is required. Prints a JSON metadata header line followed by
  the extracted body text. Requires `pypdf` (`pip install pypdf`).

## Orchestration

1. User names a specific paper ("summarize arXiv 2401.12345").
2. Claude calls `python fetch_pdf.py --arxiv-id 2401.12345`.
3. Claude parses the first line as JSON metadata, the rest as body text.
4. Claude writes the six-section summary and a citation footer.

## Dependencies

- `pypdf` (pure Python, no native build). Install with `pip install pypdf`.
- Network access to arXiv when fetching by ID or URL.

## Not in this MVP

- OCR fallback for scanned PDFs. See `DESIGN.md §5` open question.
- Caching of previously-summarized papers. Will be shared with the
  `paper-search` cache when that lands.
