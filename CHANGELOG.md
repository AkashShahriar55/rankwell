# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses [Semantic Versioning](https://semver.org/).

## [Unreleased]

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
