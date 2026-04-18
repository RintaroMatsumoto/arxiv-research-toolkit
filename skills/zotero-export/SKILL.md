---
name: zotero-export
description: Use when the user wants to push a set of papers into their Zotero library, or to produce a BibTeX file — "save these to Zotero", "give me a .bib for this list", "Zotero に入れて", "出力を BibTeX にして". Takes the JSON array produced by paper-search, writes to the local Zotero connector (port 23119) OR the Zotero Web API (headless, needs API key), supports collection targeting and optional PDF attachment, and always writes a .bib fallback. Do NOT use for searching (paper-search), summarizing (paper-summarize), or building citation graphs (citation-network).
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
- For the **local connector** leg: Zotero desktop 6.x+ running on the
  same machine, default connector on `127.0.0.1:23119`.
- For the **Web API** leg: a Zotero API key and userID (or groupID),
  issued at <https://www.zotero.org/settings/keys>. No Zotero desktop
  needed — works headlessly.
- For the BibTeX leg: write access to the chosen output directory.

## How to run

The skill folder contains `zotero_export.py`. Call it with the paper
list (from `paper-search` or hand-assembled):

```
# Default: local connector (Zotero desktop must be running)
python skills/zotero-export/zotero_export.py --input results.json

# Drop into a specific collection by its 8-char Zotero key
python skills/zotero-export/zotero_export.py --input results.json --collection AB12CD34

# Web API mode (headless; no Zotero desktop required)
python skills/zotero-export/zotero_export.py --input results.json \
    --api-key $ZOTERO_API_KEY --user-id 1234567 --collection AB12CD34

# Attach open-access PDFs as linked-URL children (Web API only)
python skills/zotero-export/zotero_export.py --input results.json \
    --api-key $ZOTERO_API_KEY --user-id 1234567 --attach-pdfs

# Offline fallback only
python skills/zotero-export/zotero_export.py --input results.json --bib-only
```

Flags:

| Flag | Purpose |
| --- | --- |
| `--input` *or* `--stdin` | paper list source (exactly one, required) |
| `--bib-out` | explicit path for the .bib file; default is `<input>.bib` |
| `--bib-only` | skip Zotero entirely; write BibTeX only |
| `--zotero-only` | skip BibTeX; write to Zotero only |
| `--api-key` | Zotero Web API key (also via `$ZOTERO_API_KEY`). Enables Web API mode. |
| `--user-id` | Zotero userID (also via `$ZOTERO_USER_ID`) |
| `--group-id` | Zotero groupID (mutually exclusive with `--user-id`) |
| `--collection` | 8-char collection key; adds new items to that collection |
| `--attach-pdfs` | Web API only: create a `linked_url` child for each `pdf_url` |

The script prints a JSON report to stdout:

```
{
  "paper_count": 20,
  "zotero": {
    "mode": "connector|web_api",
    "status": "ok|unreachable|error",
    "items_sent": 20,
    "items_created": 20,
    "collection": "AB12CD34",
    "attachments_attempted": 17,
    "attachments_created": 17
  },
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
- **Web API vs. connector** — Web API mode is preferred for headless
  use (CI, remote machines, servers). The local connector is faster on
  the user's own laptop because it bypasses the internet, and supports
  Zotero's built-in "Save with translator" behaviour automatically if
  the user points the connector at a paper page. Passing `--api-key`
  switches mode — we never fall back from Web API to local on failure,
  because a bad API key is not the kind of error we want to paper over
  silently.
- **`--attach-pdfs`** creates `linked_url` attachments — i.e. Zotero
  stores a hyperlink to `pdf_url`, not the PDF bytes. That avoids the
  multipart `imported_url` upload dance (md5, file-size, pre-signed
  upload URL) and keeps this script stdlib-only. Users who want a
  stored copy can click through and use Zotero's "Attach Stored Copy of
  File" from the item menu. Full upload support is a v0.3 candidate.

## Chaining

`paper-search` → `zotero-export` is the canonical "I want these saved"
path. If the user also wants a review draft of the same set, call
`lit-review-draft` on the same JSON — the cite keys will match.
