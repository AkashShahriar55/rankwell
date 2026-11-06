---
name: aso-keywords
description: Keyword research for an iOS app - ideas from App Store autocomplete, popularity and difficulty scores, who owns each term, which to target in the name, subtitle and keyword field, and which to track. Use when the user asks "which keywords should I target?", "find keywords", "keyword research", "is this keyword worth it?", "what do people search for?", "add keywords", or "why don't I rank for X?".
---

# Keyword research

A keyword is a word or phrase people type in App Store search. The goal is a short list of terms that
describe what the app really does, have real demand, and have a top 10 the app can enter.

## 1. Know the starting point
- `aso_context(app)`: what the app does and must not claim.
- `listing_live(app)`: words already used in the name, subtitle and keyword field [kw-no-repeat].
- `keyword_ranks(app)`: current ranks; terms in the top 10 are assets to protect [kw-top10].
- `knowledge_rules(topic="keywords")` and cite the rules you use.

## 2. Generate ideas
Seeds come from the app's features (app.md), competitor names and subtitles (`gap_report` or
`competitor_changes`), words in reviews, and the problems people describe on the web [kw-google-demand].
Run `keyword_ideas` on 3-6 seeds. Drop anything the app does not do [kw-relevance-first] and any brand or
competitor name [kw-no-trademarks].

## 3. Score
`keyword_score` on the best 25 and show its `display`. Explain once:
- **popularity** is a proxy from autocomplete; "letters" is how many letters were typed before the term was
  suggested (fewer = searched more). Compare terms of similar length; zero means unknown.
- **difficulty** is how strong the current top 10 is (ratings, exact title matches).
For the 3-5 most promising terms, `market_check` to see who owns the top 10 and get a verdict.

## 4. Recommend
Group the terms in a table [kw-popularity-vs-difficulty]:
- **Name**: the one most valuable phrase (it carries the most weight [kw-title-weight]).
- **Subtitle**: 2-3 supporting words in a readable phrase.
- **Keyword field**: single words that combine with the others [kw-field-format] [kw-combine].
- **Track only**: terms to watch but not place yet.
Say which current words would be dropped and their current rank, so nothing ranking in the top 10 is lost
by accident.

## 5. Decide
Ask with AskUserQuestion (multi-select) which terms to track; `keywords_add`. If some tracked terms are dead
weight, propose removing them and use `keywords_remove` only after a yes.
Record: `aso_note(app, "decision", "Target <terms>. Why: <scores, verdicts>")`.

## 6. Next
If the user wants these words in the listing, continue with aso-listing. Rank changes need 1-2 weeks after the
version is live to judge [kw-measure]; offer to open an experiment (aso-experiment) when the change ships.
