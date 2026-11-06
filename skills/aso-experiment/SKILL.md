---
name: aso-experiment
description: Open, check and close ASO experiments - a listing, keyword, screenshot, icon or rating prompt change with a hypothesis, baseline, metric and check-after date, judged later as confirmed, failed or inconclusive. Use when the user says "track this change", "start an experiment", "did the new subtitle work?", "experiments due", "close the experiment", "A/B test", or when a listing change has just shipped.
---

# Experiments

An experiment is one change with a written guess about what it will do, the number before, and a date to
look again. Rankwell keeps them in the repo's `.aso/experiments.md`; the user can read and edit that file.
Rules: `knowledge_rules(topic="experiments")`.

## Open an experiment
1. `aso_context(app)`: make sure no other open experiment changes the same metric [exp-one-change]. If one
   does, say so and ask whether to wait.
2. Pick the metric the change can move [exp-metric-choice] and get the baseline from data, never from memory:
   `keyword_ranks(app)` for ranks, `app_stats(app)` for impressions, conversion and ratings, over the 14 days
   before the change [exp-baseline]. Write the value and the date range.
3. Write the hypothesis: "If we <change>, <metric> will <move> because <reason from data>" [exp-hypothesis].
4. Pick the check-after date [exp-duration]: keywords 7-14 days after the version is live, conversion 14+,
   rating prompt about 28.
5. Show the experiment and ask with AskUserQuestion (Open it / Change something).
6. `experiment_open(app, hypothesis, change, metric, baseline, title=..., check_after=...)`.
For icon and screenshot changes with enough traffic, suggest Apple's product page optimization instead of a
before/after comparison [exp-ppo].

## Check and close
1. `experiments_due()` (or `experiments_due(app)`): show the table.
2. For each due experiment, get the same metric over a window of the same length after the change, from the
   same tool as the baseline. Show baseline and result side by side.
3. Look for confounders in the same dates: `competitor_changes(app)`, other decisions in `aso_context`,
   seasons, ads, featuring [exp-confounders].
4. Propose a verdict with its reason [exp-verdict]: confirmed, failed or inconclusive. Small apps are often
   inconclusive; that is a valid answer [exp-low-traffic].
5. Ask with AskUserQuestion: the proposed verdict first, then the other two, and "Wait longer".
6. `experiment_close(app, experiment_id, result, verdict)` with the numbers in the result.
7. `aso_note(app, "decision", ...)`: what happens next (keep the change, revert, try a bigger change)
   [exp-record].

## List
`aso_context(app)` shows open experiments with their dates and the recently closed ones.
