---
name: aso-setup
description: First-run setup of Rankwell for an iOS app - install check, App Store Connect key, add the app and its repo, find competitors, seed keywords, create the .aso/ folder, first data collection and the daily schedule. Use when the user says "set up Rankwell", "add my app", "track my app", "start ASO for this app", "connect App Store Connect", or when Rankwell has no apps yet or the session started in an app repo no tracked app is linked to.
---

# Set up Rankwell

Goal: by the end, the app is tracked with competitors and keywords, its repo has a `.aso/` folder, data has
been collected once, and a daily collection is scheduled. Each step can be skipped and done later; say so.

Explain terms the first time you use them, in one line:
- **ASO** (App Store Optimization): getting found in App Store search and turning views into downloads.
- **Keyword**: a word or phrase people type in App Store search.
- **Competitor**: an app that shows up for the same searches and that people compare yours with.

Use the Rankwell MCP tools named below. For every choice, ask with the AskUserQuestion tool and give options.

## 1. Check the install
Run `rankwell doctor` (Bash) from the plugin's environment: `uv run --project "${CLAUDE_PLUGIN_ROOT}" rankwell doctor`.
Show the result. Fix what you can in order: `uv` missing → `brew install uv`; Xcode tools missing →
`xcode-select --install` (only needed to upload builds). Lines about apps, key and collection are fixed by
the steps below.

## 2. App Store Connect key (optional, recommended)
Without a key Rankwell still tracks public data (ranks, competitors, reviews, charts). The key adds
App Store Connect analytics (impressions, page views, downloads), metadata edits and releases.

Tell the user, step by step:
1. App Store Connect → Users and Access → Integrations → App Store Connect API → Team Keys → "+".
2. Name it "Rankwell", role **App Manager**. That is the least access that can read analytics, edit the
   listing and submit builds. Do not use Admin; Finance is not needed.
3. Download the `.p8` file (Apple allows one download). Copy the **Issuer ID** (top of the page) and note the
   **Key ID**.
4. In their own terminal: `rankwell init --asc-key ~/Downloads/AuthKey_XXXXXXXXXX.p8 --issuer-id <issuer id>`
   (inside the plugin: `uv run --project "${CLAUDE_PLUGIN_ROOT}" rankwell init ...`). The key goes into the
   macOS Keychain. Afterwards the `.p8` can be deleted or stored somewhere safe.

Never ask the user to paste the key, the `.p8` content or the issuer id into chat, and never read `.p8` files.

## 3. Add the app
- First check it is not tracked already: `list_apps`. If the session-start context says this repo's bundle id
  matches a tracked app, or the user says the repo is one of the tracked apps, link it instead of adding:
  `update_app(app, repo_path=..., bundle_id=...)`, then skip to step 6 for the `.aso/` folder.
- Ask: is the app live on the App Store or not released yet?
- Live: `app_lookup` with its name; show the matches and ask which one is theirs.
- Repo folder: if the current directory looks like the app's code (`ios/`, `*.xcodeproj`, `pubspec.yaml`,
  `package.json` with react-native), propose it; otherwise ask for the path.
- Countries: ask with options, e.g. "United States only", "US + UK + Canada + Australia", "Let me list them".
  Start small: every country multiplies the daily work.
- `add_app(name, store_url_or_id, repo_path=..., countries=[...])`. For a pre-launch app leave the store id
  empty; `update_app` adds it on release day.

## 4. Find competitors (3 to 6)
- Ask for 2 or 3 phrases a user would type to find the app (or derive them from the repo's README and
  `.aso/app.md`).
- `market_check` each phrase: it shows who owns the top 10 and whether a new app can get in.
- Propose the apps that appear most and match the app's purpose. Ask with a multi-select question, then
  `competitors_add` each pick.

## 5. Seed keywords (10 to 25 to track)
- `keyword_ideas` for each seed phrase; drop terms that do not describe what the app really does
  [kw-relevance-first].
- `keyword_score` the best 25 and show its `display` table. Explain once: popularity is estimated from
  autocomplete (fewer letters typed before the term is suggested = more searched); difficulty is how strong
  the current top 10 is [kw-popularity-vs-difficulty].
- Recommend a list: a few broad terms for the long run, mostly specific terms the app can reach soon.
  Ask with a multi-select question, then `keywords_add`.

## 6. Create the app's memory
- `aso_init(app)` creates `.aso/` in the repo (existing files are kept).
- Fill `.aso/app.md` with the user: what the app does, who it is for, features that ship today, what not to
  claim. Draft it from the code and README, show it, and write it after they confirm. Listing copy is written
  from this file later, so it must be true.

## 7. First collection and the schedule
- `collect_now(app)`; it takes a few minutes per country. `collection_status` shows progress.
- Ask before installing the daily job: `rankwell schedule install` (default 11:00; `--at HH:MM` to change).
  If the Mac is asleep at that time, the next Claude session starts a catch-up automatically.

## 8. Record and hand over
- `aso_note(app, "decision", "Tracking <n> keywords in <countries>; competitors: <names>. Why: ...")`.
- `aso_note(app, "progress", "Rankwell set up")`.
- Tell the user what to do next, in order:
  1. "Check my code for ASO issues" (aso-code-audit), most useful before the next release.
  2. "What does my app lack against competitors?" (aso-gap), once the first collection is done.
  3. Tomorrow: "How is my app doing?" (aso-today).
