# Rankwell

App Store Optimization (ASO) and release assistant that lives in Claude Code.

You build iOS apps; Rankwell tells you where each app stands in the App Store, what to change, and ships the
change, from the same chat where you write code. There is no dashboard to open.

> Status: early development (0.x). iOS App Store only. macOS only.

## What you can ask

| You say | What happens | Skill |
|---|---|---|
| "set up Rankwell for my app" | Key check, app, competitors, seed keywords, first collection, daily schedule | `aso-setup` |
| "how is my app doing?" | What moved (ranks, ratings, reviews, competitors, funnel) and 1-3 next actions | `aso-today` |
| "what does my app lack against competitors?" | Strengths and weaknesses with the numbers behind them, competitor review complaints | `aso-gap` |
| "which keywords should I target?" | Ideas, popularity, difficulty, who owns the top 10, add to tracking | `aso-keywords` |
| "write my listing" / "change the subtitle" | Copy options with reasons, checks against limits, a current → new plan, pushed after your yes | `aso-listing` |
| "show downloads for the last 30 days" | Funnel, ranks, visibility, ratings and charts as tables and sparklines | `aso-stats` |
| "what should I change in the code for ASO?" | Audit of the app repo: permission texts, rating prompt, display name, localizations, privacy manifest, deep links, icon | `aso-code-audit` |
| "ship 1.2" | Check, build, upload, attach, review info, submit; each write confirmed | `aso-ship` |
| "the app got rejected" | Review state, Apple's message mapped to the guideline, fix plan, resubmit | `aso-review-fix` |
| "did the new subtitle work?" | Open and judge ASO experiments against a baseline | `aso-experiment` |

## How it works

- **MCP server** (`rankwell mcp`, started by Claude Code): 43 tools for apps, keywords, collection,
  insights, listing, release, state and knowledge. Results carry a ready-to-show `display` block.
- **Daily collection** (`rankwell collect`, run by launchd): ranks (top 200) for every tracked keyword,
  listing snapshots and changes for your apps and competitors, reviews, category charts, and App Store
  Connect analytics (impressions, page views, downloads by source).
- **Storage**: a local SQLite database at `~/.rankwell/rankwell.db` for numbers, and a `.aso/` folder in each
  app repo for context, decisions, experiments, progress and release settings (`release.toml`).
- **Secrets**: the App Store Connect key lives in the macOS Keychain, never in files or logs.
- **Safety**: every change to App Store Connect is a plan (current → new) that you approve in chat before it
  is applied; plans expire after 30 minutes, are re-checked against live data, and land in an audit log.
  Nothing is ever deleted in App Store Connect.
- **Knowledge**: concise ASO rules ship in `knowledge/`; add your own notes with `[knowledge] dirs` in
  `~/.rankwell/config.toml` (yours win when they conflict).

Details: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Requirements

- macOS with Claude Code and [uv](https://docs.astral.sh/uv/) (`brew install uv`)
- Xcode command line tools for builds and uploads
- An App Store Connect API key (App Manager role) for analytics, metadata and releases. Public data (ranks,
  competitors, keywords, reviews) works without one.

## Install

```bash
claude plugin marketplace add AkashShahriar55/rankwell
```

```bash
claude plugin install rankwell@rankwell
```

Then in Claude Code: "set up Rankwell for my app". Or by hand:

```bash
rankwell init --asc-key ~/Downloads/AuthKey_XXXXXXXXXX.p8 --issuer-id <issuer id>
```

```bash
rankwell schedule install --at 11:00
```

```bash
rankwell doctor
```

## Command line

| Command | Does |
|---|---|
| `rankwell doctor` | Checks the setup and says what is missing |
| `rankwell collect [--app X] [--catch-up]` | Runs the daily collection now |
| `rankwell schedule install\|uninstall\|status` | Manages the daily launchd job |
| `rankwell ship <app> --step check\|build\|upload\|attach\|submit [--yes]` | Release steps for the terminal or CI |
| `rankwell backup` | Writes a consistent copy of the database |
| `rankwell import tracker --env <file>` | One-off import from the original Supabase tracker |

## Data sources and limits

Ranks, listings, reviews and charts come from Apple's public iTunes Search/Lookup APIs, autocomplete and RSS
feeds. They are close to, not identical with, what the App Store shows, and can change without notice.
Popularity is a proxy from autocomplete, not search volume. App Store Connect data comes from Apple's
official API and arrives 1-2 days late.

## Development

```bash
uv sync
```

```bash
uv run pytest -q && uv run ruff check && uv run mypy
```

See [docs/CONTRACT.md](docs/CONTRACT.md) for module ownership and naming.
