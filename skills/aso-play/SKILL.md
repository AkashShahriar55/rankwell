---
name: aso-play
description: Google Play for the user's own Android apps - read and change the Play store listing (title, short and full description), upload an .aab to the internal, closed, open or production track with release notes and a staged rollout, show what is on each track, and read and reply to Play reviews, each write confirmed by the user. Use when the user says "Google Play", "Play Store", "Play Console", "upload the aab", "Android release", "ship to Android", "staged rollout", "promote to production", "change the Play listing", "short description", or "reply to Play reviews".
---

# Google Play

Same safety model as App Store Connect: every write is a plan. Show its `display`, ask, and only on the
user's yes call `apply_plan`. Listing and release changes are committed as one Play edit (validated first);
nothing is half-applied.

## 0. Setup (once)
- If a tool says there is no Google Play key: the user creates a service account in Google Cloud, adds it in
  Play Console → Users and permissions with access to the app (release and store listing permissions, plus
  reply to reviews), downloads its JSON key, then runs in the terminal:
  `rankwell play init --key <service-account.json>`. Never ask them to paste the key into chat.
- If the app has no package name: `update_app(app, play_package="com.example.app")` (from the Android
  applicationId in android/app/build.gradle).

## 1. Listing
- `aso_context(app)` for what the app does and what not to claim [meta-features-real].
- `play_listing_live(app, language)`: show the current title (30), short description (80), full
  description (4000).
- Write options like aso-listing does, adapted to Play: Play indexes the full description, so the main
  terms belong in the title, the short description and naturally a few times in the full description; there
  is no keyword field. No ranking, price or promotion words (best, top, #1, free, new, sale) and no emoji in
  the title: Google's metadata policy can reject the update [meta-no-claims].
- `play_listing_check(app, fields, language)` on each option; fix every problem before planning.
- `play_plan_listing(app, fields, language)`: show the plan, ask "Push this to Google Play?" (Push / Not
  yet), then `apply_plan`. Google reviews listing changes before they show.

## 2. Release (Flutter)
- `play_tracks(app)`: what is on internal, alpha (closed), beta (open) and production now.
- Build: the command is `[play] command` in `.aso/release.toml` when set, else `flutter build appbundle
  --release` (`[play] aab` overrides where the .aab is looked for). Show the command and the repo folder, ask,
  and only on yes run it in the terminal. Raise the +N build number in pubspec.yaml first if that version code is
  already on Play (the plan refuses a duplicate).
- `play_plan_upload(app, track, release_notes={"en-US": "..."}, rollout=...)`:
  - New app never published on Play: status "draft".
  - Testing first: track "internal", status completed.
  - Production: suggest a staged rollout (rollout 0.1-0.2) for risky updates [rel-phased].
  - Release notes describe real changes, 500 characters per language [meta-whats-new].
  Show the plan, ask "Upload and release on <track>?" (Upload / Not yet), then `apply_plan`.
- A large .aab can outlast a tool call: if `apply_plan` times out or the file is over ~150 MB, run it in the
  terminal after the user's yes: `rankwell play upload <app> --track <track> --notes en-US="..." --yes`.
- After: `play_tracks(app)` to confirm, and `aso_note(app, "progress", "Play <track>: <version> ...")`.

## 3. Reviews
- `play_reviews(app)`: newest first. The API only returns reviews from the last 7 days, so check weekly;
  Rankwell keeps the ones it has seen.
- Reply lowest ratings first, name the fix or the workaround, no copy-paste replies [rr-reply]. Max 350
  characters.
- `play_plan_review_reply(app, review_id, text)`: show the review and the reply, ask, then `apply_plan`. A
  new reply replaces the old one and is public at once.
- Turn repeated complaints into work items with the user [rr-mine].
