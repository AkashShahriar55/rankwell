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
- Writes to App Store Connect only through `rankwell.plans` (plan → user yes → apply_plan), with `plans.audit`.
- Do not edit files you do not own. Do not edit `pyproject.toml`, `cli.py`, `mcp/server.py`; ask for dependencies.
- Never print or log secrets. Never call App Store Connect write endpoints from tests or by hand.

## Owners
| Area | Files | Migration |
|---|---|---|
| Collector | `collect.py`, `schedule.py`, `importers/`, `commands/collect.py`, `mcp/tools_collect.py` | `0002_*.sql` |
| Insights | `stats.py`, `today.py`, `gap.py`, `mcp/tools_insights.py` | none |
| Listing and release | `listing.py`, `release.py`, `review.py`, `commands/release.py`, `mcp/tools_listing.py`, `mcp/tools_release.py` | `0003_*.sql` |
| State, audit, skills | `state.py`, `code_audit.py`, `knowledge.py`, `commands/hook.py`, `mcp/tools_state.py`, `mcp/tools_knowledge.py`, `skills/`, `knowledge/`, `hooks/` | `0004_*.sql` |

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

## CLI commands
- Collector: `rankwell collect [--app X] [--catch-up]` (`--catch-up` runs only when today has no successful
  collection; safe to call often; one run at a time via a lock file), `rankwell schedule install|uninstall|status`,
  `rankwell import tracker`
- Listing and release: `rankwell ship <app> [--step ...]`
- State: `rankwell hook session-start`
