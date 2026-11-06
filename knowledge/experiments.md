# Experiments

How to run an ASO change as an experiment and judge it honestly. Rankwell keeps experiments in the app
repo's `.aso/experiments.md` (experiment_open, experiments_due, experiment_close).

## exp-one-change: One change at a time
Change one thing per experiment (the subtitle, or the first screenshot, or the rating prompt trigger). Two
changes at once make the result impossible to attribute.
- Source: practitioner consensus

## exp-hypothesis: Write the hypothesis before the change
"If we <change>, then <metric> will <move> because <reason from data>." The reason should come from data:
a keyword score, a competitor gap, review complaints, a rank trend.
- Source: practitioner consensus

## exp-baseline: Record the baseline with its dates
Take the metric over at least the 14 days before the change (two full weeks, so weekdays balance) and write
the value and the date range. Without a baseline there is no result.
- Source: practitioner consensus

## exp-metric-choice: Pick the metric that the change can move
Keyword or name change: ranks for the target terms and search impressions. Icon or screenshot change:
conversion from search (first downloads / unique impressions). Rating prompt change: new ratings per day.
Description or promo text: page view to download conversion (they only live on the product page).
- Source: practitioner consensus

## exp-duration: How long to wait
Keyword changes: 7 to 14 days after the version is live. Conversion changes: at least 14 days, longer for
small apps. Rating prompt changes: about 4 weeks. Set the check-after date when opening the experiment.
- Source: practitioner consensus

## exp-confounders: Note what else happened
Holidays and seasons, ads, featuring, press, price changes, other listing changes and App Store algorithm
shifts all move the same metrics. Write them in the result; avoid starting tests right before holidays.
- Source: practitioner consensus

## exp-verdict: Judge against normal noise
Confirmed: the metric moved in the expected direction by more than its usual week-to-week swing in the
baseline. Failed: no move, or a move the wrong way. Inconclusive: too little traffic, or a confounder hides
the effect. Say which numbers the verdict rests on.
- Source: practitioner consensus

## exp-low-traffic: Small apps should test big changes
With little traffic, small tweaks never show a clear result. Test bold differences (a new icon concept, a
different first screenshot message) and prefer rank-based metrics, which need less traffic.
- Source: practitioner consensus

## exp-ppo: Use Apple's A/B test where possible
For icon, screenshot and preview changes, product page optimization splits traffic at the same time, which
removes seasonality. Wait for App Store Connect to report at least 90% confidence before acting.
- Source: Apple, https://developer.apple.com/app-store/product-page-optimization/

## exp-record: Record every result, including failures
A failed or inconclusive experiment is still a lesson. Close it with the numbers and the verdict, and write
the decision it leads to (aso_note decision).
- Source: practitioner consensus
