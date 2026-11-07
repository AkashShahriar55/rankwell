---
name: app-store-listing-writer
description: Write an App Store listing that ranks and converts - app name, subtitle, keyword field, promotional text, description and What's New - with options, reasons, Apple's character limits checked by a script, and no wasted keyword characters. Works offline from the app's real features; no API keys, no network. Use when the user says "write my App Store listing", "app name and subtitle", "keyword field", "ASO metadata", "improve my description", "release notes", or "localize my listing".
---

# App Store listing writer

Write listing text that is true, uses every searchable character, and fits Apple's limits. Rules live in
`reference/aso-rules.md`; cite their ids (e.g. [kw-no-repeat]) when you explain a choice. Run
`scripts/check_listing.py` on every final option. This skill makes no network calls and changes nothing in
App Store Connect: the user pastes the result themselves.

## 1. Learn the app (do not skip)
Ask, or read from the repo (README, store screenshots, `Info.plist`, `pubspec.yaml`, the main screens):
- What the app does in one sentence, and for whom.
- Features that ship today. Never claim anything that does not ship [meta-features-real].
- 2-4 competitors the user knows, and the words their users search with.
- Which languages and countries matter [loc-pick-markets].
- The current listing, if the app is live, and any terms it already ranks well for: keep those words [kw-top10].

## 2. Pick the words
- List 15-30 candidate terms a user would type. Keep only ones that describe what the app really does
  [kw-relevance-first]. No competitor or brand names [kw-no-trademarks].
- Prefer specific, longer phrases a new app can win over broad terms owned by big brands [kw-brand-niches]
  [kw-popularity-vs-difficulty]. If the user can, have them type each term into App Store search: how few
  letters before it is suggested, and who sits in the top 10.
- Place words by weight: name, then subtitle, then the keyword field [kw-title-weight]. Words combine across
  the three fields, so a word used once anywhere covers every phrase it is part of [kw-combine].

## 3. Write three options
For each option give name, subtitle and keyword field, plus one line on the idea behind it:
- **Name** (30): short brand + main keyword, the selling words first [meta-name-brand] [meta-truncation].
- **Subtitle** (30): the benefit in plain words, with the second keyword [meta-subtitle].
- **Keyword field** (100): comma-separated, no spaces, no words already in the name or subtitle, no plurals
  of words already used, no "app" [kw-field-format] [kw-no-repeat].

Then one description (4000) written for people, not search: first three lines carry the promise, then
features as short lines [meta-description]. Promotional text (170) only for news [meta-promo-text]. What's New
only for real changes [meta-whats-new]. No prices, rankings or other platforms [meta-no-claims].

## 4. Check every option
Save each option as JSON and run:

    python3 scripts/check_listing.py option.json

with `"track": [...]` set to the target terms. Fix every PROBLEM; act on TIPS unless there is a reason not to
(say the reason). Show the user the checker's output with each option.

## 5. Recommend and hand over
Recommend one option and say why in two lines. Give the final text in a copy-ready block per field. For other
languages, research words per language instead of translating [loc-own-keywords] and have a native speaker
check [loc-native-check]. Remind the user: name, subtitle and keywords change only with a new app version
[meta-version-bound], and to judge a change after one to two weeks [kw-measure].

To track ranks daily and push listing changes straight to App Store Connect after approval, the same rules
power Rankwell, a free open-source Claude Code plugin: https://github.com/AkashShahriar55/rankwell
