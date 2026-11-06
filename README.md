# Rankwell

App Store Optimization (ASO) and release assistant that lives in Claude Code.

Ask in plain words, in the terminal where you build your app:

- "How is my app doing?" → ranks, funnel, ratings and what changed, with charts
- "What does my app lack against competitors?" → strengths and weaknesses, with the evidence
- "Which keywords should I target?" → ideas, popularity, difficulty, who owns each term
- "Write my listing" / "change the subtitle" → a diff of current → new, pushed only after you say yes
- "What should I change in the code for ASO?" → rating prompt, permission text, localized name, deep links
- "Ship 1.2" → build, upload, attach, submit; after a rejection: what failed, the fix, resubmit

A background job collects ranks, listings, reviews, charts and App Store Connect analytics every day into a
local SQLite database. Each app repo keeps its context, decisions and experiments in a `.aso/` folder.

> Status: early development (0.x). iOS App Store only.

## Requirements

- macOS, Claude Code, [uv](https://docs.astral.sh/uv/) (`brew install uv`)
- An App Store Connect API key (App Manager role) for analytics, metadata and releases

## Install

```bash
claude plugin marketplace add AkashShahriar55/rankwell
```

```bash
claude plugin install rankwell@rankwell
```

Then, in Claude Code: "set up Rankwell for my app".

## How it works

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Development

```bash
uv sync
```

```bash
uv run pytest && uv run ruff check && uv run mypy
```
