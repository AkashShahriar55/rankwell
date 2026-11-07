# Rankwell

App Store Optimization (ASO) and release assistant that lives in Claude Code.

You build iOS apps; Rankwell tells you where each app stands in the App Store, what to change, and ships the
change, from the same chat where you write code. There is no dashboard to open.

> Status: early development (0.x). App Store and Google Play. macOS only.

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
| "reply to my bad reviews" | Unanswered low ratings from App Store Connect, grouped by problem, drafted replies posted after your yes | `aso-reviews` |
| "upload these screenshots" | Folder check (sizes, alpha, count), current → new per device size, upload and reorder after your yes | `aso-screenshots` |
| "release on Google Play" | Play listing, .aab upload to a track with staged rollout, Play reviews and replies | `aso-play` |
| "did the new subtitle work?" | Open and judge ASO experiments against a baseline | `aso-experiment` |

## How it works

- **MCP server** (`rankwell mcp`, started by Claude Code through `bin/rankwell`): about 60 tools for apps,
  keywords, collection, insights, listing, screenshots, reviews, release, Google Play, state and knowledge.
  Results carry a ready-to-show `display` block.
- **Daily collection** (`rankwell collect`, run by launchd): ranks (top 200) for every tracked keyword,
  listing snapshots and changes for your apps and competitors, reviews (your own from App Store Connect),
  category charts, App Store Connect analytics (impressions, page views, downloads by source), and once a
  week Google demand for your keywords.
- **Storage**: a local SQLite database at `~/.rankwell/rankwell.db` for numbers, and a `.aso/` folder in each
  app repo for context, decisions, experiments, progress and release settings (`release.toml`).
- **Secrets**: the App Store Connect key and the Google Play service account live in the macOS Keychain,
  never in files or logs.
- **Safety**: every change to App Store Connect is a plan (current → new) that you approve in chat before it
  is applied; plans expire after 30 minutes, are re-checked against live data, and land in an audit log.
  The only delete is replacing screenshots of a version in preparation, after the new ones are processed.
  Google Play changes are one Play edit each, committed only when every step worked.
- **Numbers you can trust**: popularity is a proxy built from how few letters you type before App Store
  autocomplete suggests a term (fair across short and long terms; unknown is shown as unknown, not 0), not
  search volume. `google_demand` is the same idea on Google. Download estimates for competitors are fitted on
  your own App Store Connect downloads and chart ranks, always shown as a range with the number of data
  points, and say "needs more data" until one of your apps has charted.
- **Knowledge**: concise ASO rules ship in `knowledge/`; add your own notes with `[knowledge] dirs` in
  `~/.rankwell/config.toml` (yours win when they conflict).

Details: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Requirements

- macOS with Claude Code and [uv](https://docs.astral.sh/uv/) (`brew install uv`)
- Xcode command line tools for builds and uploads
- An App Store Connect API key (App Manager role) for analytics, metadata, reviews and releases. Public data
  (ranks, competitors, keywords) works without one.
- Optional: a Google Play service account (JSON) with access to your apps, for Google Play.

## Install

```bash
claude plugin marketplace add AkashShahriar55/rankwell
```

```bash
claude plugin install rankwell@rankwell
```

Then in Claude Code: "set up Rankwell for my app". It checks the install, saves your App Store Connect key
from the `.p8` file path (Claude never sees the key), adds the app and its competitors, picks keywords, runs
the first collection and offers the daily schedule. Or by hand:

```bash
rankwell init --asc-key ~/Downloads/AuthKey_XXXXXXXXXX.p8 --issuer-id <issuer id>
```

```bash
rankwell schedule install --at 11:00
```

```bash
rankwell doctor
```

Inside the plugin `rankwell` runs as `~/.claude/plugins/cache/rankwell/rankwell/<version>/bin/rankwell`;
`rankwell install-cli` links it as `~/.local/bin/rankwell` for any terminal.

## Command line

| Command | Does |
|---|---|
| `rankwell doctor` | Checks the setup and says what is missing |
| `rankwell install-cli` | Links `~/.local/bin/rankwell` (keeps working across plugin updates) |
| `rankwell collect [--app X] [--catch-up]` | Runs the daily collection now |
| `rankwell schedule install\|uninstall\|status` | Manages the daily launchd job |
| `rankwell ship <app> --step check\|build\|upload\|attach\|submit [--yes]` | Release steps for the terminal or CI |
| `rankwell screenshots scan\|live\|upload <app> --locale --folder [--mode replace\|append] [--yes]` | Screenshot uploads from a terminal (large sets) |
| `rankwell play init --key <sa.json>` | Stores the Google Play service account in the Keychain |
| `rankwell play tracks <app>` / `rankwell play upload <app> --track internal [--yes]` | Play releases per track; .aab upload from a terminal or CI |
| `rankwell backup` | Writes a consistent copy of the database |
| `rankwell import tracker --env <file>` | One-off import from the original Supabase tracker |

## Data sources and limits

Ranks, listings and charts come from Apple's public iTunes Search/Lookup APIs, autocomplete and RSS feeds.
They are close to, not identical with, what the App Store shows, and can change without notice: a weekly
GitHub workflow (`tests/test_live.py`) calls them read-only to notice. Since November 2026 Apple's public
review feed answers empty to non-browser clients, so your own reviews come from App Store Connect and
competitor reviews are not refreshed. App Store Connect data comes from Apple's official API and arrives
1-2 days late. Google demand uses Google autocomplete and, best effort, Google Trends.

## Development

```bash
uv sync
```

```bash
uv run pytest -q && uv run ruff check && uv run mypy
```

`uv run pytest -m live` runs the read-only checks against Apple's real endpoints (App Store Connect ones need
`RANKWELL_ASC_ISSUER_ID`, `RANKWELL_ASC_KEY_ID` and `RANKWELL_ASC_KEY_PATH`).

See [docs/CONTRACT.md](docs/CONTRACT.md) for module ownership and naming.
