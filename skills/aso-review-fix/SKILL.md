---
name: aso-review-fix
description: Handle an App Store rejection - read the review state, record Apple's message, map it to the guideline, plan the fix (code, metadata, screenshots or review notes), draft the reply and resubmit. Use when the user says "my app was rejected", "App Review rejected", "guideline 2.1", "guideline 5.1.1", "metadata rejected", "how do I fix this rejection?", "resubmit", or pastes a message from App Review.
---

# Rejection → fix → resubmit

Stay calm and concrete: most rejections are fixed in a day when the fix matches exactly what Apple asked.

## 1. Read the state
`review_status(app)`: show the submission, which items were rejected (version, in-app purchases, custom
product pages) and when.

## 2. Get Apple's exact words
The App Store Connect API does not give the message text. Ask the user to open App Store Connect → the app →
App Review (or the Resolution Center message) and paste the whole message, including the guideline numbers.
Then `rejection_record(app, ...)` with the message so it is kept with the submission.

## 3. Understand it
- Pull the guideline rules: `knowledge_rules(topic="review-guidelines")`, and the specific ids that match
  the numbers in the message (for example 2.1 → rev-complete, rev-demo-account; 2.3.7 → rev-name-keywords;
  5.1.1 → rev-permission-strings, rev-account-deletion; 5.1.2 → rev-tracking; 3.1.x → rev-iap-ready).
- Explain in plain words what Apple wants changed, quoting the key sentence of their message.
- Classify each point:
  - **Metadata** (name, subtitle, keywords, description, screenshots): no new build needed.
  - **Code** (permissions, login, purchases, crashes): new build needed.
  - **Information** (review notes, demo account): fix in review information.
  - **Misunderstanding**: the app already complies; reply with steps or a screen recording.
- For code issues, run `code_audit(app)`: it often points at the exact file.

## 4. Fix plan
Show a table: point, type, the change, where. Ask with AskUserQuestion: "Go with this plan?" (Yes / Change
it). Then:
- Code: edit the repo with the editor tools, then build, upload and attach with the aso-ship steps.
- Metadata: `plan_listing` → show → ask → `apply_plan`.
- Screenshots: say which ones break which rule and what to show instead; the user replaces them.
- Review information: `plan_review_info` → show → ask → `apply_plan`.

## 5. Reply to App Review
Draft a short, factual reply: what was changed, where the reviewer can see it, demo steps if relevant.
The user posts it in App Store Connect; Rankwell does not send messages. If the user believes the rejection
is wrong, explain the appeal option [rev-reply].

## 6. Resubmit
`plan_submit(app)` resubmits the same submission and marks the rejected items resolved [rev-resubmit].
Show the plan, ask "Resubmit now?", and only on yes `apply_plan`.

## 7. Record
- `aso_note(app, "decision", "Rejection <guideline>: <fix>. Why: <Apple's point>")`.
- If the cause could happen again (for example a vague permission text), add a todo or a rule in the user's
  own knowledge notes so the next release avoids it.
- Follow with `review_status(app)` in the next sessions.
