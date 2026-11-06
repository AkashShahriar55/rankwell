---
name: aso-stats
description: Charts and comparisons of an app's App Store numbers over any period - keyword ranks, visibility, impressions, page views, downloads, conversion, ratings and chart positions - with what moved and why. Use when the user asks "show my downloads", "rank history", "compare this month with last month", "how did the update do?", "chart my keywords", "conversion rate", or any numbers question about their app.
---

# Stats and comparisons

## 1. Understand the question
Work out the app, the metric(s), the period and the comparison. Defaults: last 28 days against the 28 days
before. If the question is ambiguous ("how is it going?"), ask with AskUserQuestion: ranks, downloads and
conversion, ratings, or everything.

## 2. Get the numbers
- `app_stats(app, ...)` for funnel, downloads, conversion, ratings and period comparison.
- `keyword_ranks(app, ...)` for rank history with sparklines.
- Show each tool's `display` block as is, then explain it.
If App Store Connect numbers are missing, say why (no key yet, or Apple's reports lag 1-3 days) and how to
fix it (aso-setup step 2). Never estimate or invent numbers.

## 3. Explain what moved
- Name the 2-4 changes that matter, with the numbers.
- Look for causes in the same dates: `aso_context(app)` decisions and experiments, `competitor_changes(app)`,
  releases. If nothing lines up, say the cause is unknown.
- Read conversion per source and next to downloads: more generic search traffic can lower the rate while
  downloads grow [cvr-measure].
- Explain any term the first time (impression: the app was shown; page view: the product page was opened;
  conversion: downloads divided by impressions).

## 4. Next
Offer one or two follow-ups: open an experiment for a change being considered (aso-experiment), look at
keywords that dropped (aso-keywords), or compare with competitors (aso-gap).
