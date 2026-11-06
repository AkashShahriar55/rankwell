---
name: aso-today
description: Daily ASO briefing for the user's apps - what changed in ranks, ratings, reviews and competitors, what is due, and the next one to three actions. Use when the user asks "how is my app doing?", "what changed?", "anything new?", "morning check", "ASO update", "what should I do today?", or opens a session in an app repo and wants a status.
---

# What changed and what to do now

Keep it short: the user wants the news and a clear next step, not every number.

## 1. Pick the app
If the session context names an active app, use it. Otherwise `list_apps`; with several apps, ask with the
AskUserQuestion tool (options: each app, "All apps").

## 2. Check the data is fresh
`collection_status`. If today's collection has not run or failed, say so in one line and offer
`collect_now`. Never present stale numbers as today's.

## 3. The briefing
- `app_today(app)`: show its `display` block as is (ranks that moved, ratings, new reviews, funnel if the App
  Store Connect key is set).
- `competitor_changes(app)`: only the changes that matter (a competitor changed its name, subtitle or
  screenshots, or released a version).
- `reviews_list(app)` for new 1-2 star reviews: one line each with the complaint.
- `experiments_due(app)`: experiments ready for a verdict.
- `aso_context(app)`: open items and recent decisions, so you connect today's numbers to what was changed.

## 4. Explain
In plain words, three short sections:
- **What changed**: the 2-4 movements that matter and their likely cause. Tie them to dated decisions and
  experiments from `.aso/` when the dates line up; otherwise say the cause is unknown. Never invent numbers.
- **Watch**: anything worrying (a top-10 keyword dropping out [kw-top10], a rating dip, a competitor
  targeting the same words).
- **Do now**: at most three actions, ordered by impact, each with the skill that does it (aso-experiment to
  close a due experiment, aso-listing for copy, aso-keywords for new terms, a reply to a review).

## 5. Let the user choose
Ask with AskUserQuestion: the actions from "Do now" as options, plus "Nothing today". Then do the chosen one
(load the matching skill).

## 6. Record
When something was decided or done, `aso_note(app, "decision" | "progress", ...)`. Open items for later:
`aso_note(app, "todo", ...)`.
