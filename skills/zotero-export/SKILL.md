---
name: zotero-export
description: Use when the user wants to push a set of papers into their Zotero library, or to produce a BibTeX file — "save these to Zotero", "give me a .bib for this list", "Zotero に入れて", "出力を BibTeX にして". Takes the JSON array produced by paper-search (or any list with the same shape), POSTs to the local Zotero connector on port 23119, and always writes a sibling .bib file as a fallback. Do NOT use for searching (paper-search), summarizing (paper-summarize), or building citation graphs (citation-network).
---

# Zotero / BibTeX Export

Move a paper set into the user's local Zotero library and/or emit a
BibTeX file suitable for LaTeX workflows.

## When to use

Trigger when the user wants to persist a paper list into a reference
manager or citation file, for example:

- "save these 20 papers to Zotero"
- "export this JSON to .bib"
- "add these to my Zotero collection on RAG"
- "Zotero にまとめて入れて"
- "BibTeX で出力して"

Do NOT trigger when:

- The user is still searching (use `paper-search`).
- The user wants a written summary or review (use `paper-summarize` or
  `lit-review-draft`).

## Prerequisites

- Python 3.8+ on PATH. Stdlib only; no pip installs required.
- For the Zotero leg: **Zotero desktop 6.x+ running** with the default
  connector on `127.0.0.1:23119`. No web API key needed.
- For the BibTeX leg: write access to the chosen output directory.

## How to run

The skill folder contains `zotero_export.py`. Call it with the paper
list (from `paper-search` or hand-assembled):

```
python skills/zotero-export/zotero_export.py --input results.json
```

Flags:

| Flag | Purpose |
| --- | --- |
| `--input` *or* `--stdin` | paper list source (exactly one, required) |
| `--bib-out` | explicit path for the .bib file; default is `<input>.bib` |
| `--bib-only` | skip Zotero entirely; write BibTeX only |
| `--zotero-only` | skip BibTeX; POST to Zotero only |

The script prints a JSON report to stdout:

```
{
  "paper_count": 20,
  "zotero":  {"status": "ok|unreachable|error", "message": "..."},
  "bibtex":  {"status": "ok|error", "path": "/abs/path.bib", "entry_count": 20}
}
```

## How to present results

1. Parse the stdout JSON.
2. Report both legs to the user:
   - If `zotero.status == "ok"`, say "Sent N items to Zotero." Tell them
     to check their Zotero window; unfiled items land in **My Library ▸
     Unfiled Items** by default.
   - If `zotero.status == "unreachable"`, say Zotero is not running
     and suggest launching Zotero desktop. The BibTeX file is still
     usable — mention it.
   - If `zotero.status == "error"`, surface the server message verbatim.
     Most often this is a connector version mismatch on older Zotero 5.
   - If `bibtex.status == "ok"`, share the absolute path so the user can
     open it, e.g. `~/papers/results.bib`.
3. If both legs failed, apologize and suggest running with `--bib-only`
   to a known-writable directory (e.g. `./out.bib`).

## Failure handling

- **Zotero not running** — the script reports `status: "unreachable"`
  and continues to the BibTeX leg. Tell the user to launch Zotero or
  pass `--bib-only`.
- **Zotero returned HTTP 4xx/5xx** — surface the message. A 400 from
  `/connector/saveItems` usually means a field shape mismatch; ask the
  user for a minimal failing record and re-run.
- **BibTeX write failed** — most often a permissions issue. Suggest a
  `--bib-out` path in the current working directory.
- **Input is not a JSON array** — the script exits 1 with a clear
  message. Tell the user the input must be the `paper-search` JSON
  array (or an object with a `papers` key).

## Implementation notes

- Cite-key format matches `lit-review-draft`:
  `<Surname><Year><titleSlug>` with collision suffix `a`/`b`/`c`/...
  That means the `.bib` and the review draft cite the same papers with
  identical keys.
- Zotero item types: arXiv records map to `preprint`, other sources to
  `journalArticle`. Extend `to_zotero_item()` if new sources land.
- The `.bib` leg runs even when Zotero succeeds, so the user always has
  an offline copy of the export.

## Chaining

`paper-search` → `zotero-export` is the canonical "I want these saved"
path. If the user also wants a review draft of the same set, call
`lit-review-draft` on the same JSON — the cite keys will match.
