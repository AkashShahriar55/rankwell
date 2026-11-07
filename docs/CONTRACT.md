# Build contract (shared by everyone working on the repo in parallel)

## Conventions
- Python 3.11+, `uv run ruff check` (line 120) and `uv run mypy` (strict) clean, `uv run pytest -q` green.
- Tests in `tests/`, network mocked with `respx` (see `tests/test_stores.py`), isolated home (see `tests/conftest.py`).
- Domain logic in `src/rankwell/<module>.py`, testable without MCP.
- MCP tools: new file `src/rankwell/mcp/tools_<area>.py` with `register(mcp: MCPServer)`; each tool is
  `@mcp.tool()` then `@safe`, returns a dict; human-facing output goes in a `display` Markdown string built with
  `rankwell.render`. Docstrings are what Claude reads: say what it does and when to use it.
- CLI: new file `src/rankwell/commands/<area>.py` with `register(main: click.Group)`.
- Schema: new migration file only, numbered per owner (below). Never edit 0001.
- Writes to App Store Connect and Google Play only through `rankwell.plans` (plan → user yes → apply_plan), with
  `plans.audit`. Play writes go through one edit (insert → change → validate → commit; delete on failure).
  Plan payload keys must not match `plans.RESERVED` (the plan's own columns).
- Do not edit files you do not own. Do not edit `pyproject.toml`, `cli.py`, `mcp/server.py`; ask for dependencies.
- Never print or log secrets. Never call App Store Connect write endpoints from tests or by hand.

## Owners
| Area | Files | Migration |
|---|---|---|
| Collector | `collect.py`, `schedule.py`, `importers/`, `commands/collect.py`, `mcp/tools_collect.py` | `0002_*.sql` |
| Insights | `stats.py`, `today.py`, `gap.py`, `mcp/tools_insights.py` | none |
| Listing and release | `listing.py`, `release.py`, `review.py`, `commands/release.py`, `mcp/tools_listing.py`, `mcp/tools_release.py` | `0003_*.sql` |
| State, audit, skills | `state.py`, `code_audit.py`, `knowledge.py`, `commands/hook.py`, `mcp/tools_state.py`, `mcp/tools_knowledge.py`, `skills/`, `knowledge/`, `hooks/` | none |
| Setup and reviews | `setup.py`, `replies.py`, `bin/rankwell`, `mcp/tools_setup.py`, `mcp/tools_reviews.py` | `0004_*.sql` |
| Data quality | `research.py`, `stores/google.py`, `demand.py`, `estimates.py`, `mcp/tools_estimates.py` | `0005_*.sql` |
| Google Play | `play.py`, `stores/play.py`, `commands/play.py`, `mcp/tools_play.py`, `skills/aso-play/` | `0006_*.sql` |
| Screenshots | `screenshots.py`, `commands/screenshots.py`, `mcp/tools_screenshots.py`, `skills/aso-screenshots/` | `0007_*.sql` |

## MCP tool names (skills call these)
Existing: list_apps, add_app, update_app, keywords_add, keywords_remove, competitors_add, competitors_remove,
keyword_ideas, keyword_score, market_check, app_lookup, apply_plan, recent_writes.

- Collector: collection_status, collect_now
- Insights: app_today, app_stats, keyword_ranks, gap_report, reviews_list, competitor_changes
- Listing and release: listing_live, listing_check, listing_draft_save, plan_listing, plan_create_version,
  release_check, build_start, build_status, plan_upload, plan_attach_build, plan_review_info, plan_submit,
  review_status, rejection_record
- State, audit, skills: aso_context, aso_init, aso_note, experiment_open, experiment_close, experiments_due,
  code_audit, knowledge_rules
- Setup and reviews: setup_status, asc_key_save, cli_install, reviews_asc, plan_review_replies,
  review_replies_sent; listing: plan_listing_languages
- Data quality: download_estimate (keyword_score gains google_demand, popularity_basis)
- Google Play: play_listing_live, play_listing_check, play_plan_listing, play_tracks, play_plan_upload,
  play_reviews, play_plan_review_reply
- Screenshots: screenshots_live, screenshots_scan, plan_screenshots

## CLI commands
- Collector: `rankwell collect [--app X] [--catch-up]` (`--catch-up` runs only when today has no successful
  collection; safe to call often; one run at a time via a lock file), `rankwell schedule install|uninstall|status`,
  `rankwell import tracker`
- Listing and release: `rankwell ship <app> [--step ...]`
- State: `rankwell hook session-start`
- Setup: `rankwell doctor`, `rankwell install-cli`
- Screenshots: `rankwell screenshots scan|live|upload <app> --locale --folder [--mode replace|append] [--yes]`
- Google Play: `rankwell play init --key <file.json> | --remove`, `rankwell play tracks <app>`,
  `rankwell play upload <app> [--aab] [--track] [--status] [--rollout] [--notes LANG=TEXT] [--yes]`
