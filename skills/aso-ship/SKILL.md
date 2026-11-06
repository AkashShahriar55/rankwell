---
name: aso-ship
description: Ship an iOS app version - check what App Store Connect still needs, create the version, build, upload, attach the build, review information and submit for review, each step confirmed by the user. Use when the user says "ship 1.2", "release the app", "submit for review", "upload the build", "build and upload", "prepare the release", or "is the version ready to submit?".
---

# Ship a version

Every step that changes App Store Connect is a plan: show it, ask, then `apply_plan`. Each step can run on its
own; start where the release actually is.

## 1. Where the release stands
- `aso_context(app)`: `.aso/release.toml` (how this app builds and uploads) and recent decisions.
- `release_check(app)`: show its `display`. It lists what Apple still needs: version, build, screenshots per
  size, review information, export compliance, App Privacy, in-app purchases to attach [rel-checklist].
- If `release.toml` is missing or still the commented example, set it up with the user: ask the build kind
  (Flutter, Xcode, fastlane, custom command) with AskUserQuestion, then edit the file.

## 2. Version
If there is no editable version for the new number: `plan_create_version(app, version)`, show, ask,
`apply_plan`.

## 3. Build and upload
- Suggest running `code_audit(app)` first if it has not run since the last code changes (high findings can
  fail upload or review).
- `build_start(app)` first runs nothing: it returns the exact shell command, the folder it runs in and the
  log path. Show all three to the user and ask "Run this build?" (Run / Not now). Only on Run:
  `build_start(app, confirmed=True)`, then follow `build_status` until it finishes. On failure, show the end
  of the log and fix with the user.
- `plan_upload(app)`: show what will be uploaded, ask, `apply_plan`. A large upload can outlast a tool call:
  if `apply_plan` times out, or the .ipa is over ~200 MB, run the upload in the terminal instead, in the
  background, after the user's yes: `rankwell ship <app> --step upload --yes` (it uploads and waits for
  Apple's processing). Then `release_check(app)` until the build shows as processed (10-30 minutes).

## 4. Attach, listing, review information
- `plan_attach_build(app, ...)`: show, ask, `apply_plan`.
- Listing changes for this version: aso-listing (its plans go through the same confirm step). What's New
  must describe real changes [meta-whats-new].
- `plan_review_info(app, ...)`: write review notes for a stranger: what the app does, where the main feature
  is, a demo account if anything needs a login [rel-review-notes] [rev-demo-account]. Ask the user for demo
  credentials only if they want them stored in App Store Connect; never put them in `.aso/` files or notes.
  Show, ask, `apply_plan`.

## 5. Submit
- `release_check(app)` again; everything must be green.
- `plan_submit(app)`: show the plan including release type (after approval or manual [rel-manual-release])
  and phased release [rel-phased]. Ask explicitly: "Submit version X for App Review?" Options: Submit / Not
  yet. Only on Submit: `apply_plan`.

## 6. Record and follow up
- `aso_note(app, "progress", "Submitted <version>: <highlights>")`.
- If the listing changed, make sure an experiment is open with its baseline (aso-experiment).
- Tell the user review usually takes one to three days [rel-review-time]; `review_status(app)` shows the
  state. If it is rejected, use aso-review-fix.
