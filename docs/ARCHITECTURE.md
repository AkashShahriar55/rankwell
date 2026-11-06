# Rankwell architecture

Rankwell is an App Store Optimization (ASO) and release assistant that lives inside Claude Code. It is a
Claude Code plugin: skills tell Claude how to do ASO work, an MCP server gives Claude the data and the
actions, and a small background job collects data every day. There is no separate app to open.

It is built for one person working on their own apps (indie developers, small studios). Every user runs
their own copy on their own Mac with their own App Store Connect key. Nothing is hosted.

## Goals

1. **Know where the app stands.** Ranks, competitors, reviews, App Store Connect funnel, and what the app
   lacks against the market (strengths and weaknesses).
2. **Know what to change.** Metadata (name, subtitle, keyword field, description, promo text, screenshots
   captions), and ASO-related changes in the app's own code (rating prompt, permission text, localized app
   name, deep links for custom product pages).
3. **Change it safely.** Edit metadata from chat with a diff and a confirm. Build, upload, attach, submit,
   handle a rejection and resubmit: one pipeline, with fastlane or a custom command.
4. **Remember.** Progress, decisions and experiments survive between sessions, in files the user can read.
5. **Show it well in chat.** Tables, sparklines and bar charts that read in a terminal; questions with
   options for decisions.

Non-goals for 1.0: Google Play, hosted multi-user service, paid ads management.

## Pieces

```
rankwell/
├── .claude-plugin/
│   ├── plugin.json          plugin manifest
│   └── marketplace.json     the repo is its own marketplace
├── .mcp.json                starts the MCP server: uv run rankwell mcp
├── hooks/hooks.json         SessionStart → rankwell hook session-start (due items, nudges)
├── skills/<name>/SKILL.md   one folder per skill (see Skills)
├── knowledge/               default ASO rules shipped with the plugin (own words, cited)
├── src/rankwell/            Python package (CLI, MCP server, collector, domain logic)
├── tests/                   pytest; network mocked, no real keys
└── docs/
```

### Python package (`src/rankwell/`)

| Module | Job |
|---|---|
| `paths.py` | Where things live: `RANKWELL_HOME` (default `~/.rankwell`), database, config, logs, cache |
| `config.py` | User settings in `~/.rankwell/config.toml` (default countries, collect time, knowledge dirs) |
| `secrets.py` | App Store Connect key and other secrets in the macOS Keychain (`keyring`), env vars override |
| `db/` | SQLite connection (WAL, foreign keys, busy timeout), numbered SQL migrations |
| `stores/appstore.py` | Public App Store data: search, lookup, autocomplete, subtitle, charts, reviews |
| `stores/asc.py` | App Store Connect API client (JWT ES256, retries, pagination) |
| `stores/google.py` | Google and YouTube autocomplete, Google Trends (best effort) |
| `apps.py` | The user's apps, their keywords, competitors and countries |
| `research.py` | Keyword ideas, popularity proxy, difficulty, market check |
| `collect.py` | Daily collection: ranks, listing snapshots, change detection, reviews, charts, App Store Connect analytics |
| `stats.py` | Numbers for charts and reports: rank history, visibility, funnel, ratings, period comparison |
| `gap.py` | Market gap: the app vs its competitors (listing, ratings, keywords, review complaints, screenshots) |
| `listing.py` | Metadata: read live and draft, check limits, plan a change, apply a confirmed plan |
| `release.py` | Release pipeline: build, upload, wait for processing, attach, review info, submit, resubmit |
| `review.py` | Review state and rejection handling |
| `state.py` | The `.aso/` folder in each app repo: context, decisions, experiments, progress |
| `render.py` | Terminal output: tables, sparklines, bars, deltas |
| `importers/` | One-off imports (the original tracker's Supabase) |
| `mcp/server.py` | MCP tools Claude calls |
| `cli.py` | `rankwell` command: init, doctor, collect, mcp, schedule, hook, import |
| `schedule.py` | Installs and removes the launchd job for the daily collection |

## Storage

Two layers, each used for what it is good at.

**Markdown in each app repo (`<app repo>/.aso/`).** Human- and Claude-readable state that belongs to the
app and should be versioned with it:

- `app.md`: what the app is, market, positioning, what not to claim
- `decisions.md`: dated decisions and why
- `experiments.md`: hypothesis, change, baseline, result, verdict
- `progress.md`: open items and what was done, newest first
- `release.toml`: how this app builds and ships (see Release pipeline)

**SQLite (`~/.rankwell/rankwell.db`).** Time series and anything queried for charts: apps, keywords,
competitors, daily ranks, listing snapshots, detected changes, reviews, chart positions, App Store Connect
daily metrics, keyword scores, pending plans and an audit log of every write to App Store Connect.

The database is one file. Backup is a copy (`rankwell backup` writes a consistent copy with SQLite's
backup API). Migrations are numbered SQL files applied in order and recorded in `schema_migrations`.

**Secrets.** The App Store Connect issuer id, key id and private key (.p8 content) go in the Keychain under
the service `rankwell`. `RANKWELL_ASC_ISSUER_ID`, `RANKWELL_ASC_KEY_ID`, `RANKWELL_ASC_KEY_PATH` override
it (CI). Secrets never go in the database, the `.aso/` folder, logs or MCP responses.

## Daily collection

`rankwell collect` runs once a day from launchd (`rankwell schedule install --at 11:00`). It is
idempotent: running it twice on one day updates that day's rows. When the job missed days (Mac asleep or
off), the SessionStart hook runs a catch-up in the background.

Per app and country:

1. Lookup the app and its competitors (one call, up to 200 ids) → snapshot; diff against the previous
   snapshot → `changes` rows (title, subtitle, version, description, screenshots, price, rating count).
2. Search each tracked keyword (top 200) → rank of each tracked app.
3. Reviews (RSS, newest 50) → upsert.
4. Category chart (RSS top 100) → position of each tracked app.
5. App Store Connect analytics (when a key is set and the app is in the account) → daily metrics by source.

Public endpoints are rate limited (about 20 calls per minute for search and lookup), so collection is
sequential with a throttle and retries with backoff. One HTTP client is reused for the whole run.

## Safety model for writes

Every action that changes something outside the user's Mac is two steps:

1. `plan_*` reads the live state and returns a plan: an id, a list of changes (current → new), notes, and a
   hash of the live state it was built from. The plan is stored in `plans` and expires after 30 minutes.
2. `apply_plan(plan_id)` re-reads the live state, refuses when its hash changed, applies, records each
   change in `audit_log`, and marks the plan applied.

Skills tell Claude to show the plan as a table and ask the user before applying.

One write is automatic: App Store Connect analytics need report requests per app, and the collector
creates them once (audited; off with `[analytics] create_report_requests = false`). Submitting for review
and uploading a build are separate plans. Nothing deletes App Store Connect data.

## Release pipeline

`<app repo>/.aso/release.toml`:

```toml
[build]
# One of: "flutter", "xcode", "fastlane", "custom"
kind = "flutter"
command = "flutter build ipa --release --export-options-plist=ios/ExportOptions.plist"  # custom or override
ipa = "build/ios/ipa/*.ipa"

[upload]
# "altool" (xcrun altool with the API key), "fastlane" (pilot / deliver), or "custom"
kind = "altool"

[release]
type = "after-approval"      # or "manual"
phased = false
```

Steps: check (what Apple still needs) → build → upload → wait for processing → attach build → metadata and
screenshots → review info → submit. Each step can run alone. After a rejection: read the review
submission state, show what was rejected, fix (code, metadata or screenshots), mark the rejected item
resolved and resubmit in the same submission.

## Skills

| Skill | Use |
|---|---|
| `aso-today` | What changed and what to do now, per app |
| `aso-gap` | Strengths and weaknesses against competitors |
| `aso-keywords` | Keyword ideas, scores, market check, add to tracking |
| `aso-listing` | Write, check, edit and push metadata |
| `aso-stats` | Charts and comparisons over any period |
| `aso-code-audit` | ASO-related changes in the app's code |
| `aso-ship` | Build, upload, attach, submit |
| `aso-review-fix` | Rejection → fix plan → resubmit |
| `aso-experiment` | Open and close experiments |
| `aso-setup` | First run: key, apps, keywords, competitors, schedule |

Skills read the shipped `knowledge/` rules and any extra knowledge folders the user configured, and cite
the rule they use.

## Chat display

Claude Code renders Markdown. `render.py` turns numbers into Markdown tables, Unicode sparklines
(`▁▂▃▅▇`), horizontal bars and signed deltas, so MCP tools can return ready-to-show blocks next to the raw
data. Decisions use Claude's question prompt with options.

## Quality bar

- Python 3.11+, `uv`, `ruff`, `mypy --strict` on the package, `pytest` with network mocked.
- CI on GitHub Actions (macOS and Ubuntu) for lint, types and tests.
- No secret in a log line, exception message or MCP response.
- Every public endpoint call has a timeout, a retry policy and a clear error.
- Versioned with SemVer; `CHANGELOG.md`.

## Phases

0. Foundation: package, config, secrets, SQLite, store clients, MCP skeleton, plugin manifest, CLI, CI.
1. Daily value: collector, schedule, import from the original tracker, today, stats, keywords, gap, listing.
2. Shipping: release pipeline, review state, rejection and resubmit.
3. Depth: code audit, experiments loop, SessionStart nudges, knowledge rules.
4. Public: onboarding polish, docs, license, security review.
