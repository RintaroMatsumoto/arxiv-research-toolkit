---
name: paper-summarize
description: Use when the user wants a structured summary of a SINGLE specific paper they have already identified — "summarize arXiv 2401.12345", "この論文を要約して" with an ID/URL, or a local PDF path. Fetches the PDF, extracts text, and produces a markdown summary with TL;DR / Key contributions / Method / Results / Limitations / Open questions. Do NOT use for multi-paper literature reviews (that is lit-review-draft) or for finding papers in the first place (that is paper-search).
---

# Paper Summarize

Produce a structured, citable summary of one specific paper.

## When to use

Trigger when the user names a specific paper and asks for a deep-read, e.g.:

- "summarize arXiv 2401.12345"
- "TL;DR this: https://arxiv.org/pdf/2404.17762.pdf"
- "この論文を要約して / 深掘りして" with an ID, URL, or local PDF path
- "what are the key contributions of `./papers/foo.pdf`?"

Do NOT trigger when:

- The user is still searching for papers (use `paper-search`).
- The user has a list of papers and wants a synthesis across them (use
  `lit-review-draft`).
- The user wants a citation graph (use `citation-network`).

## Prerequisites

- Python 3.8+ on PATH.
- `pypdf` installed: `pip install pypdf`. Pure-Python, no native build.
- Network access to `arxiv.org` if fetching by `--arxiv-id` or `--url`.

## How to run

The skill folder contains `fetch_pdf.py`. Call it with exactly one source
flag:

```
python skills/paper-summarize/fetch_pdf.py --arxiv-id 2401.12345
python skills/paper-summarize/fetch_pdf.py --url https://arxiv.org/pdf/2401.12345.pdf
python skills/paper-summarize/fetch_pdf.py --path ./paper.pdf
```

Useful flags:

| Flag | Purpose |
| --- | --- |
| `--arxiv-id` / `--url` / `--path` | source (exactly one, required) |
| `--max-chars` | truncate extracted text (default 80000; 0 to disable) |
| `--header-only` | emit only the JSON metadata line; skip body text |

## Script output contract

The script prints **one JSON header line** followed by the extracted plain
text:

```
{"source":"arxiv","arxiv_id":"2401.12345","url":"...","page_count":14,"byte_count":1234567,"char_count":48210,"truncated":false}
<blank line>
<paper text>
```

Parse the first line as JSON; everything after the trailing newline is the
body. `truncated: true` means the body was cut at `--max-chars` — warn the
user before summarizing the Results/Limitations sections.

## How to present the summary

After receiving the text, respond in Markdown with these sections in order:

1. **TL;DR** — 2–3 sentences. What is the paper's single headline claim?
2. **Key contributions** — bulleted list of 3–5 concrete novel claims.
3. **Method** — the approach in enough detail for a reader to decide whether
   to read the paper fully. Name the model family, loss, dataset, and
   evaluation harness.
4. **Results** — the top numerical or qualitative results. Include the
   baseline so the delta is legible. If the paper reports on multiple
   benchmarks, summarize the trend, do not list every row.
5. **Limitations** — what the authors admit, plus any gaps you notice
   (e.g., missing ablations, narrow dataset).
6. **Open questions** — 1–3 things that would change the reader's
   confidence, or follow-up experiments the paper invites.

End with a one-line citation header:

```
> arXiv:<id> — <first author> et al., <year>. <title>.
```

## Failure handling

- **pypdf not installed** — the script exits 4 with a clear message.
  Surface it verbatim and suggest `pip install pypdf`.
- **PDF download failed (arXiv or URL)** — exit 1. Tell the user the
  paper could not be fetched; ask them to verify the ID/URL or provide a
  local path via `--path`.
- **PDF opened but text extraction was empty** — common for scanned or
  image-only PDFs. Tell the user OCR would be needed and that this MVP
  does not ship it. Offer to summarize based on the abstract if they can
  provide it.
- **`truncated: true`** — warn the user the body was capped at
  `--max-chars` and that the Limitations/Results sections near the end
  of the paper may be missing.

## Implementation notes

- Text extraction is pypdf-only in the MVP. OCR fallback (Tesseract or a
  cloud service) is listed as an open question in `DESIGN.md §5`.
- `--max-chars` defaults to 80 000 (~20 k tokens) — enough for most ML
  papers and a cost cap for the summarization step.
- The script deliberately returns plain text, not Markdown. Claude does
  the structuring — the script just gets bytes off disk and text off the
  PDF.

## Chaining

This skill is commonly called after `paper-search` with a paper the user
picked out of the results table, and its output feeds directly into
`lit-review-draft` when multiple papers need synthesis.
