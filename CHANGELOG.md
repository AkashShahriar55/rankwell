# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.2.3] - 2026-11-11

### Added
- Linux scheduling: `rankwell schedule install|uninstall|status` use a systemd user timer
  (`~/.config/systemd/user/rankwell-collect.{service,timer}`, `Persistent=true` so a missed run happens at the
  next start) and fall back to a tagged crontab line when `systemctl --user` is not available. Cron cannot catch
  up missed days, and `status` and `doctor` say so. The macOS launchd job is unchanged.

- Linux keys: the App Store Connect and Google Play keys go in the Secret Service (GNOME Keyring or KWallet).
  Without a keyring, Rankwell says so and points to the `RANKWELL_ASC_*` and `RANKWELL_PLAY_KEY_PATH`
  environment variables instead of failing.

### Changed
- `rankwell doctor` and `collection_status` report the daily schedule on Linux too; `doctor` suggests the uv
  install script on Linux instead of Homebrew.
- README, CONTRIBUTING and the architecture notes cover Linux.

## [0.2.2] - 2026-11-08

### Fixed
- Listing checks during a plan count the live name, subtitle and keyword field: planning only the promotional
  text no longer lists every tracked keyword as uncovered, and repeats against the live fields are caught.

## [0.2.1] - 2026-11-07

### Added
- MIT license, contributing guide, security policy, issue and pull request templates, Dependabot, CODEOWNERS.
- Works outside Claude Code: `uv tool install "git+https://github.com/AkashShahriar55/rankwell@v0.2.1"` gives a
  `rankwell` command and MCP server for Cursor, Claude Desktop, Windsurf and other MCP clients; the shipped ASO
  rules now travel inside the package.
- `extras/app-store-listing-writer`: a standalone skill (no server, no network) that writes App Store
  listings with a stdlib checker for limits and wasted keywords.
- `extras/build_agensi.py` builds upload zips for skill marketplaces.

### Changed
- README rewritten: cover, quick start, example prompts, grouped skills, safety model, data sources, FAQ.
- CI uses actions/checkout v7 and astral-sh/setup-uv v7.

## [0.2.0] - 2026-11-07

### Added
- Setup from chat: `setup_status`, `asc_key_save` (stores the key from the .p8 file path; the key never reaches
  chat), `cli_install` / `rankwell install-cli` (links `~/.local/bin/rankwell`). `bin/rankwell` starts the MCP
  server and hook, finds uv outside a short PATH and explains what to do without it.
- Review replies through App Store Connect: `reviews_asc` (all countries, reply state), `plan_review_replies`,
  `review_replies_sent`; skill `aso-reviews`. The daily collection reads your own reviews from App Store Connect.
- `plan_listing_languages`: several languages in one plan, all checked first and re-checked together.
- Screenshot upload, replace and reorder for the version in preparation (`screenshots_live`, `screenshots_scan`,
  `plan_screenshots`; `rankwell screenshots scan|live|upload`; skill `aso-screenshots`). Replace deletes the old
  screenshots of a set only after the new ones finished processing.
- Google Play for your own apps: listing, .aab upload to a track (status, staged rollout, release notes), tracks,
  reviews and replies, each write one Play edit (`play_*` tools, `rankwell play init|tracks|upload`, skill
  `aso-play`, `[play]` in release.toml, `add_app`/`update_app` take `play_package`).
- Google demand: `keyword_score` adds `google_demand`; the daily collection checks tracked keywords on Google
  about once a week (autocomplete, best-effort Trends direction).
- Download estimates: `download_estimate` and a block in `gap_report`, fitted on your own App Store Connect
  downloads and chart ranks, always a labelled range with its data points.
- Weekly read-only live check of Apple's endpoints (`pytest -m live`, `.github/workflows/live-check.yml`).
- Session start in an app repo no tracked app is linked to points it out (0.1.1).

### Changed
- Popularity scores the letters typed before App Store autocomplete suggests a term, so long phrases are no
  longer inflated; never suggested is unknown (None), and weighs as rare in visibility. New `popularity_basis`.
- `doctor` and `setup_status` share one list of checks; optional ones (schedule, Xcode tools, Google Play key,
  terminal command) never fail it.
- Plans refuse payload keys that would hide the plan's own columns.

### Fixed
- Apple's public review feed returns nothing for URLs with a page segment and for non-browser clients since
  November 2026; page 1 goes without the segment and own reviews come from App Store Connect.

## [0.1.1] - 2026-11-07

### Added
- Session start in an iOS app repo that no tracked app is linked to (Flutter, React Native or Xcode) now says
  so, with the repo's bundle id, the tracked app it matches by bundle id, and a link or setup suggestion.

## [0.1.0] - 2026-11-07

### Added
- Foundation: SQLite storage with migrations, Keychain secrets, App Store and App Store Connect clients,
  keyword research, terminal rendering, MCP server, `rankwell` CLI, plugin manifest.
- Daily collection (ranks, snapshots, change detection, reviews, charts, App Store Connect analytics),
  launchd schedule with catch-up, import from the original Supabase tracker.
- Insights: today briefing, stats (ranks, visibility, funnel, ratings, charts), market gap report.
- Listing check/plan/apply; release pipeline (build, upload, attach, review info, submit, resubmit after
  rejection); rejection records. Plan/apply with audit log for every App Store Connect write.
- `.aso/` state in app repos, ASO code audit, shipped knowledge rules, SessionStart hook, 10 skills.
