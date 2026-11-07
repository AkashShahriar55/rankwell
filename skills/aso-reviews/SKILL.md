---
name: aso-reviews
description: Read and answer App Store reviews - find unanswered low ratings, group them by problem, draft short human replies and post them through App Store Connect after the user's yes. Use when the user says "reply to reviews", "answer my reviews", "any bad reviews?", "respond to the 1-star review", "unanswered reviews", or "what are users complaining about?".
---

# Answer reviews

A reply notifies the reviewer, who can then update the review; low ratings first [rr-reply]. Every reply
is posted by a plan: show, ask, `apply_plan`.

## 1. Find what needs an answer
- `reviews_asc(app)`: 1-3 stars from the last 30 days with no reply yet, all countries. Show its `display`.
  Widen with `days`, `max_rating=5`, or `unanswered_only=False` when the user asks.
- No App Store Connect key: `reviews_list(app, max_rating=3)` reads the public feed (no reply state, no
  posting). Say that replying needs the key (aso-setup).
- Group the reviews by problem (crash, missing feature, price, confusion) and count them. Name the top
  problem in one line: it is input for the next release [rr-mine].

## 2. Draft
- `aso_context(app)` first: what the app does, recent releases and decisions, so replies are true.
- One reply per review, in the review's language, 2-4 sentences: thank, address the exact point, say what
  to do now (a setting, a workaround, support email) or that a fix is in the next version only when it is.
- Never ask for a better rating, never offer anything for one [rr-no-incentives]; no links (they are not
  clickable), no copy-paste replies across reviews, no blaming the user.
- Show the drafts in a table (review → reply) and let the user edit before planning.

## 3. Post
- `plan_review_replies(app, replies=[{"review_id", "text"}, ...])` (up to 25). Show its `display` and notes:
  reviews that already have a reply get it replaced.
- Ask "Post these N replies?" (Post / Edit first / Not now). Only on Post: `apply_plan(plan_id)`.
- Report sent and failed ones. Apple shows a reply after a short check (state PENDING_PUBLISH).
- `aso_note(app, "progress", "Replied to N reviews: <top problem>")`. When a fix for the top problem ships,
  come back and reply to the reviews that reported it, and mention it in What's New (aso-listing).
