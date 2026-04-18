# Contributing

Thanks for your interest!

`arxiv-research-toolkit` is at `v0.1.0` — five alpha skills. The skill
surfaces are usable but still in flux; breaking changes will wait for
`0.2.0` and be announced in `CHANGELOG.md` (not yet created).

## Ways to contribute

- **Bug reports.** Open an issue with the exact command invoked, the
  paper IDs involved, and the stderr output. Minimal reproductions win.
- **New paper sources.** Follow the pattern in
  `skills/paper-search/search_arxiv.py` — stdlib-only CLI that prints
  the canonical JSON record shape (see `README.md § Canonical paper-
  record schema`). Target order: Semantic Scholar → PubMed.
- **Skill polish.** Cache layers, OCR fallback for `paper-summarize`,
  richer graph layouts for `citation-network`. See open questions in
  `DESIGN.md § 5`.
- **Documentation.** Per-skill design docs in `docs/skills/` are the
  long-form counterpart to each `SKILL.md`. Worked examples help.

## Code conventions

- Python 3.8+ stdlib where at all possible. Pip dependencies are opt-in
  and must be declared in the skill's `SKILL.md` prerequisites section.
- One CLI per skill in `skills/<skill-name>/`, paired with a `SKILL.md`
  that Claude reads on activation and a short `README.md` for humans.
- Stdout is the machine-readable channel (JSON). Stderr is the human-
  readable channel (warnings, retry notices). Exit codes are documented
  at the top of each script.
- Cite-key format `<Surname><Year><titleSlug>` is shared across
  `lit-review-draft` and `zotero-export` and must stay in sync.

## Before sending a PR

1. Run `python -m py_compile skills/**/*.py` and confirm clean.
2. Exercise the CLI against a real paper or query; paste the first ~40
   lines of output into the PR description.
3. Update the affected `SKILL.md` if you changed the CLI surface.
