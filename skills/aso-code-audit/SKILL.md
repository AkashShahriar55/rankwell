---
name: aso-code-audit
description: Check an app's source code (Flutter, Swift/Xcode, React Native) for changes that affect App Store ranking, conversion or App Review - permission texts, rating prompt timing, home screen name vs store name, localizations, privacy manifest, deep links, app icon, in-app purchase ids, placeholder text, tracking prompt vs ads SDKs - and fix them. Use when the user asks "what should I change in the code for ASO?", "check my app before release", "audit my code", "why was my permission text rejected?", "add a rating prompt", or "is my app ready for review?".
---

# ASO code audit

Some ASO lives in the app's code: when the rating prompt shows, what the permission texts say, the name under
the icon, the languages the app declares. This skill finds those issues and fixes them with the user.

## 1. Find the repo
Use the active app from the session context, or `aso_context(app)` for its repo folder. The user can also
give a path. If no repo is set, `aso_init(app, repo_path=...)`.

## 2. Run the audit
`code_audit(app_or_path)`. Show its `display` table. Each finding has a severity:
- **high**: likely rejection, crash or failed upload. Fix before the next submission.
- **medium**: hurts ratings, conversion or review odds. Fix soon.
- **low**: worth knowing; fix when convenient.
The `facts` part lists what was found (display name, languages, product ids, SDKs); use it in your answer.

## 3. Explain
Go from high to low. For each finding, in plain words: what was found (file:line), why it matters for
ranking, conversion or App Review, and the fix. Pull the cited rules with `knowledge_rules(ids=[...])` when
the user wants the background. Group many similar low findings into one line.

## 4. Decide what to fix
Ask with AskUserQuestion (multi-select) which findings to fix now. For anything with wording (permission
texts, the home screen name), propose the text and ask before writing it:
- Permission text: "<App> uses <thing> to <specific purpose>, so you can <benefit>." [rev-permission-strings]
- Rating prompt: after a successful core action, behind a counter (e.g. third completed task, at least three
  days after install), never at launch or after an error, no "do you like the app?" filter
  [rr-prompt-timing] [rr-no-gating].
- Home screen name: the brand part of the store name [rev-name-match].

## 5. Fix in the code
Edit the files in the user's repo with the editor tools, keeping changes minimal and in the project's style.
Show what changed. Do not touch signing settings, secrets, `.env` or `.p8` files. For a missing privacy
manifest or icon, explain the Xcode steps when a file cannot be created correctly by hand.

## 6. Verify and record
- Run `code_audit` again and show that the fixed findings are gone.
- `aso_note(app, "progress", "Code audit: fixed <items>")`; `aso_note(app, "todo", ...)` for deferred ones.
- If a fix changes what the listing should say (name, features), offer aso-listing.
- In-app purchase ids: remind the user to compare them with App Store Connect.
- Code changes reach users only with a new build: next step is aso-ship.
