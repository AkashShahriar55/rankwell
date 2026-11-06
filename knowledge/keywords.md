# Keywords

How App Store search finds an app, and how to pick the words to target. Where to place them: metadata.md.

## kw-fields: Only some fields count for search
Apple matches searches against the app name, subtitle, the hidden keyword field, the categories and promoted
in-app purchase names. The description is not a keyword field: Apple says not to stuff it for search.
Search ranking also uses behaviour: downloads, ratings and engagement after a search.
- Source: Apple, https://developer.apple.com/app-store/search/

## kw-title-weight: The name carries the most weight, then the subtitle, then the keyword field
Put the single most valuable term in the app name, the next ones in the subtitle and the rest in the keyword
field. Apple does not publish the weights; this order is what practitioners consistently measure.
- Source: practitioner consensus

## kw-no-repeat: Each word counts once
Words are pooled across name, subtitle and keyword field within one localization. Repeating a word in a
second field adds nothing and wastes characters. Category names count as already present.
- Source: Apple, https://developer.apple.com/app-store/search/

## kw-field-format: Keyword field format
100 characters, terms separated by commas without spaces. Use singular or plural, not both. Leave out
"app", the category name, filler words ("the", "and"), and anything already in the name or subtitle.
Single words combine with each other, so "photo,editor" also covers "photo editor".
- Source: Apple, https://developer.apple.com/app-store/product-page/

## kw-combine: Words combine across fields
Within one localization, a word in the name and a word in the keyword field can rank together as a phrase.
The exact phrase in the name still ranks best for the main term, so keep the most important phrase intact
there and use the keyword field for single words that extend it.
- Source: practitioner consensus

## kw-relevance-first: Target only what the app really does
A term that brings searchers who do not want the app converts badly, and low conversion pulls the rank
down again. Apple can also remove or reject irrelevant keywords. Relevance first, popularity second.
- Source: Apple, https://developer.apple.com/app-store/search/

## kw-no-trademarks: No competitor or brand names
Do not use other apps' names, trademarks or celebrity names in any field. It is a common rejection
(Guideline 2.3.7) and rarely converts anyway.
- Source: Apple, https://developer.apple.com/app-store/review/guidelines/

## kw-popularity-vs-difficulty: Weigh popularity against difficulty
A good target has real demand and a top 10 you can break into. Difficulty is about the top 10: how many
ratings they have and whether they name the term in their title. A new app starts with specific, longer
terms where the top 10 is weak, and moves to broad terms as ratings and downloads grow.
Rankwell's popularity is a proxy from App Store autocomplete: a term suggested after fewer typed letters is
searched more. Compare terms of similar length; zero means unknown, not no demand.
- Source: practitioner consensus

## kw-top10: Only the top 10 brings real traffic
Most searchers do not scroll far, so ranks past about 10 bring little. Before changing metadata, list the
terms where the app is already top 10 and keep their words in place.
- Source: practitioner consensus

## kw-brand-niches: Avoid niches owned by brands or ad budgets
When the top results for a term are big brands, or the leaders grow mainly through paid ads, an indie app
relying on search rarely reaches the top 10. Prefer terms where the top 10 has small or old apps.
- Source: practitioner consensus

## kw-measure: Give a keyword change one to two weeks
App Store ranks react within about a day of a new version going live, then settle. Judge on weekly averages
after one to two weeks, not on one day. Removed words lose their rank right away.
- Source: practitioner consensus

## kw-google-demand: Check what people search on the web too
Web searches show the problems people describe in their own words ("how to track habits", "habit app free").
Use them to find app-intent phrases for the keyword field, and questions to answer in the description and
screenshots. Treat web volume as direction only: app store searches are shorter.
- Source: practitioner consensus
