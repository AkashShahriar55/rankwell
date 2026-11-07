---
name: aso-screenshots
description: Upload, replace and reorder App Store screenshots for a version in preparation, per locale and display size, with a check of sizes and alpha first and a confirmed push to App Store Connect. Use when the user says "upload screenshots", "replace screenshots", "push my screenshots", "reorder screenshots", "screenshot sizes", "which screenshot sizes do I need", "are my screenshots the right size", or "what screenshots are live".
---

# Upload and reorder screenshots

Rankwell checks the files, plans the upload and waits for the user's yes. Nothing reaches App Store Connect
before that. Captions and the story the screenshots tell are written with aso-listing; this skill moves files.
`knowledge_rules(topic="screenshots")` has the rules; cite their ids.

## 1. Where things stand
- `screenshots_live(app, locale)`: show its `display`. It lists each set (APP_IPHONE_67 = iPhone 6.9",
  APP_IPHONE_65 = 6.5", APP_IPAD_PRO_3GEN_129 = iPad 13", ...), count, files in order and processing state.
- Screenshots change only on a version in preparation. If there is none, make one with
  `plan_create_version` (show, ask, `apply_plan`). The live version is never touched.
- If the locale is missing from the version, add the language first with aso-listing (`plan_listing`).

## 2. Check the folder
`screenshots_scan(folder, locale)`: show its `display`. Layouts it reads:
- `<folder>/<locale>/<display type or any>/01-name.png` (several locales at once),
- `<folder>/<locale>/01-name.png` (display type from the pixel size),
- a flat folder of files plus the `locale` argument.
Files go up in name order (`01-`, `02-`, ... `10-`). Fix every problem with the user before planning:
wrong pixel size, PNG with alpha or transparency (Apple rejects it; export without alpha), more than 10 in a
set [shot-specs]. When a size fits two slots (2048×2732 is iPad 13" and 12.9"), the note says which was used.
Before uploading, look at the order with the user: the first three carry the main benefit and never a login
or welcome screen [shot-first-three] [shot-real-ui]; no prices [shot-no-prices].

## 3. Plan, show, ask
- `plan_screenshots(app, folder, locale, mode)`. `mode="replace"` (default) makes the folder the whole set;
  `mode="append"` adds after the current ones. Reordering = replace with the files renamed in the new order.
- Show the plan's `display` (current → new per set, and the new files in order) and its notes as they are.
  Say plainly when replacing deletes the current screenshots of a set in the version in preparation: they are
  deleted only after the new uploads finished processing; if any of those fails, nothing more is deleted. A set
  holds at most 10, also while uploading, so a crowded set uploads in two rounds and a full one first loses its
  last screenshot to make room; the notes say when.
- Ask: "Upload these screenshots to App Store Connect?" with options Upload / Change something / Not now.

## 4. Apply (only after a yes)
- `apply_plan(plan_id)`. If it says App Store Connect or a file changed since the plan, make a new plan and
  show it again.
- Many or large files can outlast a tool call. Then, after the yes, run the command from the plan's notes in
  the terminal in the background: `rankwell screenshots upload <app> --locale <locale> --folder <dir>
  --mode replace --yes`.
- Show the result's `display`. If some uploads failed, say exactly which and why; the old screenshots are
  still there. Fix the files and plan again (replace mode also clears the failed uploads).
- Finish with `screenshots_live` so the user sees the new order.

## 5. Record
- `aso_note(app, "decision", "Screenshots <locale>: <what changed>. Why: <reason>")`.
- Offer `experiment_open` (aso-experiment) with conversion rate as the metric, so the new set gets a verdict
  [shot-test].
- Next: aso-ship when the version is ready to submit.
