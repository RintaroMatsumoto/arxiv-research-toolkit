# zotero-export

MVP skill for the `arxiv-research-toolkit` plugin. Pushes a paper set
into the user's local Zotero via the connector on port 23119 and always
writes a sibling `.bib` file as a fallback.

## Files

- `SKILL.md` — prompt Claude reads on activation. Declares trigger
  phrases, the `zotero_export.py` CLI contract, and how to interpret the
  report JSON (Zotero leg + BibTeX leg).
- `zotero_export.py` — Python 3.8+ stdlib CLI. Takes `--input <json>` or
  `--stdin`, plus optional `--bib-out`, `--bib-only`, `--zotero-only`.
  Prints a JSON report to stdout.

## Orchestration

1. User hands Claude a JSON paper list and asks for a Zotero/BibTeX
   export.
2. Claude calls `python zotero_export.py --input ...`.
3. The script pings Zotero at `127.0.0.1:23119/connector/ping`:
   - If reachable → POST items to `/connector/saveItems`.
   - Regardless, write a `.bib` file next to the input (unless
     `--zotero-only`).
4. Claude reports both legs and the `.bib` path to the user.

## Dependencies

- Python 3.8+ stdlib only. No pip installs.
- Zotero desktop 6.x+ running locally for the Zotero leg (optional).

## Not in this MVP

- Specific collection targeting. The Zotero connector API puts items in
  "Unfiled Items" by default. Collection-selection requires the Web API
  with a user key, which this MVP avoids.
- Attaching the PDF. Only metadata is exported. PDFs can be pulled
  separately via `paper-summarize`'s `fetch_pdf.py`.
