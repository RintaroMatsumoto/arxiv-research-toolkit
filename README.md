# arxiv-research-toolkit

> Pre-release — placeholder repository. No working code yet.

Research toolkit for Claude. Search, summarize, and synthesize academic papers from arXiv, Semantic Scholar, and PubMed.

This repository reserves the name and documents design intentions. Implementation will follow once the portfolio plan enters the build phase.

## Planned skills

- **paper-search** `[planned]` — Cross-search arXiv, Semantic Scholar, and PubMed with unified result schema.
- **paper-summarize** `[planned]` — Claude API-powered PDF summarization with key contributions extraction.
- **lit-review-draft** `[planned]` — Generate literature review drafts from a set of papers.
- **zotero-export** `[planned]` — BibTeX and Zotero library export via the local Zotero HTTP server.
- **citation-network** `[planned]` — Build and visualize citation graphs across a paper set.

## Requirements (planned)

- Python 3.10+
- `arxiv` Python package
- Zotero Local Server (port 23119) for library sync
- Anthropic API key for summarization skills

## License

MIT — see [LICENSE](LICENSE).

## Roadmap

See [DESIGN.md](DESIGN.md) for the current design document and milestones.

This is part of a three-plugin portfolio. See [PLUGIN_PORTFOLIO_PLAN.md](https://github.com/RintaroMatsumoto/ProgrammaticVideoGen/blob/main/docs/PLUGIN_PORTFOLIO_PLAN.md).
