---
name: rankwell
description: App Store Optimization and release automation for iOS and Google Play apps through the Rankwell MCP server - daily keyword ranks, competitor gaps, keyword research, listing changes, screenshot uploads, review replies, code audits and releases, each store change approved by the user first. Use when the user asks how their app is doing, what it lacks against competitors, which keywords to target, to change their App Store or Google Play listing, upload screenshots, reply to reviews, ship a version, or fix a rejection. Needs the Rankwell MCP server (free, open source, macOS); see SETUP.md.
---

# Rankwell

Rankwell is a free, open-source MCP server (MIT) for App Store Optimization and app releases. This skill
routes each request to the playbook in `playbooks/` that handles it. The playbooks call Rankwell's MCP tools,
so the server must be installed first.

## 0. Check the server
If Rankwell's tools (for example `setup_status`, `list_apps`, `app_today`) are not available, the server is
not set up: walk the user through `SETUP.md` in this folder, step by step, and stop until it is done. Never
ask the user to paste an App Store Connect key or Google Play key into the chat; the server reads them from a
file path the user types and stores them in the macOS Keychain.

## 1. Route the request
Read the matching playbook and follow it exactly:

| The user wants | Playbook |
|---|---|
| First-time setup, add an app, connect App Store Connect | `playbooks/aso-setup/SKILL.md` |
| "How is my app doing?", what changed, what to do today | `playbooks/aso-today/SKILL.md` |
| Numbers over time: ranks, downloads, conversion, charts | `playbooks/aso-stats/SKILL.md` |
| What the app lacks against competitors | `playbooks/aso-gap/SKILL.md` |
| Keyword ideas, popularity, difficulty | `playbooks/aso-keywords/SKILL.md` |
| Write or change the App Store listing | `playbooks/aso-listing/SKILL.md` |
| Upload, replace or reorder screenshots | `playbooks/aso-screenshots/SKILL.md` |
| Read and reply to App Store reviews | `playbooks/aso-reviews/SKILL.md` |
| ASO changes needed in the app's code | `playbooks/aso-code-audit/SKILL.md` |
| Build, upload and submit an iOS version | `playbooks/aso-ship/SKILL.md` |
| A rejection from App Review | `playbooks/aso-review-fix/SKILL.md` |
| Google Play listing, releases, reviews | `playbooks/aso-play/SKILL.md` |
| Did a change work, experiments | `playbooks/aso-experiment/SKILL.md` |

## 2. Rules that always apply
- Tool results carry a `display` block (tables, sparklines): show it as is, then explain.
- Never invent numbers. If data is missing, say so and how to get it.
- Anything that changes App Store Connect or Google Play is two steps: a `plan_*` tool returns current → new;
  show it, ask the user, and only on an explicit yes call `apply_plan`.
- Popularity is a proxy from autocomplete, not search volume. Download estimates are ranges.

## What it connects to
Rankwell runs on the user's Mac and calls only: Apple's public App Store endpoints (search, lookup,
autocomplete, charts, reviews), the App Store Connect API and the Google Play Developer API with the user's own
keys, and Google autocomplete and Trends for keyword demand. It has no server of its own and sends no data to
the author. Source: https://github.com/AkashShahriar55/rankwell
