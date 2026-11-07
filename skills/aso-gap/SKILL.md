---
name: aso-gap
description: Strengths and weaknesses of the user's app against its App Store competitors - listing, ratings, keywords, screenshots and review complaints - with evidence and a prioritized fix list. Use when the user asks "what does my app lack?", "how do I compare to competitors?", "why do they rank above me?", "gap analysis", "competitive analysis", or "what are my weaknesses?".
---

# Market gap: the app against its competitors

## 1. Load context
- `aso_context(app)` for positioning, features that ship and what not to claim.
- `gap_report(app)`: show its `display` block. It compares listing fields, ratings, keyword coverage,
  screenshot counts and review complaints with the tracked competitors.
- Downloads for charted apps are **estimates** from chart rank, fitted on the user's own App Store Connect
  downloads: always say "estimate", give the range and the data points behind it, and never compare them to real
  numbers as if they were measured. When the block says it needs more data, say so and move on.
- If the app has fewer than 3 competitors, offer to add some first (`market_check` on a main keyword, then
  `competitors_add` after the user picks).

## 2. Ground the reading in rules
`knowledge_rules(topic=...)` for the areas the report flags (metadata, keywords, screenshots,
ratings-reviews, conversion). Cite rule ids next to each point. The user's own rules come first when they
disagree with shipped ones; say when that happens.

## 3. Write the verdict
Two tables, each row with the evidence (a number or a quote from the data) and the rule id:
- **Strengths** to keep and lean on in the listing.
- **Weaknesses**, each with: the fix, effort (small, medium, large), expected impact (ranking, conversion,
  ratings) and the skill that does it (aso-listing, aso-keywords, aso-code-audit, aso-experiment).

Competitor complaints in reviews are opportunities: if users of a competitor complain about something the app
does well, say it plainly in the listing [rr-mine].

Explain any ASO term the first time (for example "conversion: the share of people who see the app and
download it").

## 4. Decide with the user
Ask with AskUserQuestion (multi-select) which weaknesses to work on now. Then:
- `aso_note(app, "decision", "Gap review: working on <items>. Why: <evidence>")`.
- `aso_note(app, "todo", ...)` for each item kept for later.
- Start the first chosen item with its skill.
