---
name: aso-listing
description: Write, check and change the App Store listing - app name, subtitle, keyword field, promotional text, description, What's New and screenshot captions - with several options, a recommendation, a current-to-new diff and a confirmed push to App Store Connect. Use when the user says "write my listing", "change the subtitle", "new app name", "improve my description", "write release notes", "screenshot captions", "update metadata", or "push the listing".
---

# Write and change the listing

Rankwell writes the copy; the user decides; nothing reaches App Store Connect without their yes.

## 1. Gather the facts
- `aso_context(app)`: features that ship today and what not to claim. If app.md is thin, read the code or
  ask before writing. Never advertise a feature that is not in the build [meta-features-real].
- `listing_live(app)`: the current fields per localization.
- `keyword_ranks(app)`: terms to protect (top 10) [kw-top10]; target terms from aso-keywords decisions.
- `gap_report(app)` or `competitor_changes(app)`: competitor names and subtitles, so the copy stands apart.
- `reviews_list(app)`: words happy users use and complaints about competitors [rr-mine].
- `knowledge_rules(topic="metadata")`, plus `keywords`, `screenshots` and `review-guidelines`. Cite ids.

## 2. Write three options
Each option is a complete set: name, subtitle, keyword field, promotional text, the first three lines of the
description, and captions for the first three screenshots (plus What's New when a version is coming).
Give each a strategy:
- **Reach**: the highest-popularity terms the app can realistically rank for.
- **Easy wins**: specific terms with a weak top 10, for a newer app.
- **Balanced**: a mix.

Copy that grabs attention:
- Lead with what the user gets, in their words, in the first 25 characters [meta-truncation].
- One concrete, true detail beats an adjective ("Scan a receipt in 2 seconds" over "Fast and easy").
- Echo the searched phrase in the subtitle or first caption so searchers see a match [shot-caption-keywords].
- No "best", "#1", prices, other platforms or competitor names [meta-no-claims] [rev-name-keywords].
- Each word once across name, subtitle and keyword field [kw-no-repeat]; keyword field: commas, no spaces,
  no "app", no category words [kw-field-format].
- Keep the brand matching the home screen name [meta-name-brand] [rev-name-match].

For each option say: the hook (why it makes someone stop scrolling), the terms it covers, the current words it
drops and their ranks, and risks (review, conversion, brand).

## 3. Check every option
`listing_check` on each option: it counts every limit and flags repeats and risky words. Never count
characters by hand. Fix and re-check until clean.

## 4. Recommend and decide
Recommend one option and say why in two or three sentences, tied to data (scores, ranks, competitor gaps).
Ask with AskUserQuestion: Option A / B / C / "Mix or edit". Then `listing_draft_save` the chosen set.

## 5. Push (only after a yes)
1. Name, subtitle, keywords, description and screenshots need an editable version [meta-version-bound]. If
   there is none, `plan_create_version`, show the plan, ask, `apply_plan`.
2. `plan_listing(app, ...)`: show the current → new table from its `display`, plus notes.
3. Ask: "Apply these changes to App Store Connect?" with options Apply / Change something / Not now.
4. Only on Apply: `apply_plan(plan_id)`. If it says the live listing changed, make a new plan and show it
   again.
Promotional text can go live without a version; everything else goes live when the version is released.

## 6. Record
- `aso_note(app, "decision", "Listing: <what changed>. Why: <reason, rule ids>")`.
- Offer `experiment_open` (aso-experiment) with the target ranks or conversion as metric and the current
  values as baseline, so the change gets a verdict in 1-2 weeks.
- Next: if the version is ready, aso-ship.
