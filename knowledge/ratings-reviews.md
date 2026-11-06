# Ratings and reviews

Getting ratings, the rating prompt, and replying to reviews.

## rr-why: Ratings move both rank and conversion
The number of ratings and the average influence search ranking and show next to the app in results.
The summary rating is per country. For a new app, more ratings is one of the highest-value goals.
- Source: Apple, https://developer.apple.com/app-store/ratings-and-reviews/

## rr-system-prompt: Use the system rating prompt
Ask with the system prompt (StoreKit requestReview; Flutter in_app_review; expo-store-review). iOS shows it
at most three times in 365 days per user and may decide not to show it, so never call it from a button that
promises a dialog. For a "Rate us" button, open the App Store write-review link instead.
- Source: Apple, https://developer.apple.com/app-store/ratings-and-reviews/

## rr-prompt-timing: Ask after a success, not at launch
Ask right after the user finishes something that went well (a task done, a level won, the third saved item),
after a few days of use. Never during onboarding, on first launch, after an error or crash, or in the middle
of a task. Wait months before asking the same user again.
- Source: Apple, https://developer.apple.com/app-store/ratings-and-reviews/ ; practitioner consensus

## rr-no-gating: Do not filter who gets asked
A custom "Do you like the app?" question that sends only happy users to the rating prompt filters feedback.
Apple asks apps to use its API for review prompts (Guideline 5.6.1) and treats filtered feedback as
manipulation. Ask everyone the same way and offer a separate, always-visible feedback option.
- Source: Apple, https://developer.apple.com/app-store/review/guidelines/

## rr-no-incentives: Never pay or reward for ratings
Rewards for ratings, paid or fake reviews, and review services can get the developer account removed.
- Source: Apple, https://developer.apple.com/app-store/review/guidelines/

## rr-reply: Reply to reviews, lowest first
The reviewer is notified and can update their review. Start with low ratings and bugs in the current
version. Be short, specific and human, no marketing. After a fix ships, mention it in What's New and reply
to the reviews that reported it.
- Source: Apple, https://developer.apple.com/app-store/ratings-and-reviews/

## rr-reset: Reset the summary rating rarely
A new version can reset the summary rating. Do it only when the old rating reflects problems that are now
fixed: an app with few ratings converts worse. Written reviews stay either way.
- Source: Apple, https://developer.apple.com/app-store/ratings-and-reviews/

## rr-thresholds: Below 4.0 hurts, 4.5 and up competes
People hesitate to install apps under about 4.0 stars, and below 4.5 an app loses against higher-rated
neighbours in the same results. One 1-star review needs several 5-star ones to recover the average.
- Source: practitioner consensus

## rr-mine: Reviews tell you what to build and say
Repeated complaints are the first fixes; repeated requests are candidates for the roadmap; the words happy
users use are good listing copy. Competitors' complaints are your differentiators.
- Source: practitioner consensus
