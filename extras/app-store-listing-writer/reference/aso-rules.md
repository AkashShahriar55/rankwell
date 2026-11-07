# App Store listing rules

Short rules with their sources, from the Rankwell project (MIT). Ids in brackets are cited in SKILL.md.


The text fields of the App Store listing: what each is for and its limits. Choosing words: keywords.md.

## meta-limits: Character limits
App name 30, subtitle 30, keyword field 100, promotional text 170, description 4,000, What's New 4,000.
The name needs at least 2 characters. 
- Source: Apple, https://developer.apple.com/help/app-store-connect/reference/app-information/

## meta-name-brand: Name = short brand + main keyword
Combine a short, distinctive brand with the main keyword ("Brand: Habit Tracker" or "Habit Tracker - Brand").
Apple asks for distinctive names and frowns on purely generic ones (Guideline 2.3.7), while indie developers
who lead with the keyword report better search results. Keep it readable either way, and make sure the
brand matches the name under the icon on the home screen.
- Source: Apple, https://developer.apple.com/app-store/product-page/ ; practitioner reports

## meta-truncation: Front-load the name and subtitle
In search results the name is often cut after about 25 characters and the subtitle earlier on some screens.
Put the words that sell and the main keyword first; anything at the end may not be seen.
- Source: practitioner measurements

## meta-subtitle: The subtitle states the benefit
Use the subtitle to say what the app does for the user, carrying secondary keywords in a natural phrase.
Avoid empty claims ("the best app") and references to other apps (Guideline 2.3.7).
- Source: Apple, https://developer.apple.com/app-store/product-page/

## meta-promo-text: Promotional text is for news, not search
The 170-character promotional text sits above the description, is not used for search, and can change at
any time without a new version. Use it for what is new or timely: a feature, a season, an event.
- Source: Apple, https://developer.apple.com/app-store/product-page/

## meta-description: Write the description for people
The description does not help search ranking. Its first lines show before "more", so lead with the main
benefit in one or two sentences, then a short list of key features. Answer the questions people ask before
installing. No prices (they differ by country and change).
- Source: Apple, https://developer.apple.com/app-store/product-page/

## meta-whats-new: Release notes describe real changes
List significant changes in order of importance; a generic "bug fixes" line is only fine for small fixes
(Guideline 2.3.12). When a fix answers user reviews, say so and then reply to those reviews.
- Source: Apple, https://developer.apple.com/app-store/review/guidelines/

## meta-no-claims: No prices, rankings or other platforms in the listing
Leave prices, "#1", "best", awards you cannot show, and names of other mobile platforms out of the name,
subtitle, keywords and screenshots (Guidelines 2.3.7 and 2.3.10).
- Source: Apple, https://developer.apple.com/app-store/review/guidelines/

## meta-features-real: Only claim what ships
Every feature named in the listing must be in the submitted build (Guideline 2.3.1). Keep the app's
`.aso/app.md` "Features that ship today" list current and write copy from it.
- Source: Apple, https://developer.apple.com/app-store/review/guidelines/

## meta-version-bound: Most fields change only with a new version
Name, subtitle, keyword field, description and screenshots are tied to an app version; promotional text is
not. Plan keyword and copy changes with a release, one experiment at a time.
- Source: Apple, https://developer.apple.com/help/app-store-connect/reference/app-information/

## meta-iap-names: Promoted in-app purchase names show in search
Promoted in-app purchases appear on the product page and can appear in search results. Give them descriptive
names that say what the buyer gets, using relevant words.
- Source: Apple, https://developer.apple.com/app-store/search/


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
A free popularity proxy is App Store autocomplete: a term suggested after fewer typed letters is
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


Listing languages, markets and the app's own languages.

## loc-listing-first: Localize the listing before the app
A localized name, subtitle, keyword field and screenshot captions can win searches in a market long before
the whole app is translated. Start with the listing where demand exists, then translate the main screens.
- Source: Apple, https://developer.apple.com/app-store/product-page/

## loc-pick-markets: Pick markets from your own data
Look at impressions, page views and downloads by country to find where interest already exists, and at
whether competitors there are localized. Start with one or two markets and measure.
- Source: Apple, https://developer.apple.com/localization/

## loc-own-keywords: Research keywords per language, do not translate them
Each localization has its own 100-character keyword field. People search with local words that a dictionary
translation misses. Check each candidate with App Store autocomplete and the top results in that country.
- Source: practitioner consensus

## loc-extra-locales: Some countries read more than one localization
Practitioners have observed that some storefronts index more than one localization (for example the United
States reading both English (U.S.) and Spanish (Mexico)). This can extend keyword coverage, but each
localization is still shown to its own speakers, so keep its text sensible for them.
- Source: practitioner observation; Apple does not document the ranking effect

## loc-native-check: Have a native speaker check translations
Machine translation is a fine start; a native speaker should review the listing before it goes live.
- Source: Apple, https://developer.apple.com/localization/

## loc-fallback: No localization means the primary language
Where a storefront has no listing in its language, the primary language listing is shown.
- Source: Apple, https://developer.apple.com/help/app-store-connect/reference/app-information/

## loc-display-name: Localize the home screen name with the store name
If the store name changes per language, add the matching CFBundleDisplayName in InfoPlist.strings (or
InfoPlist.xcstrings) so the name under the icon matches what people searched for.
- Source: practitioner consensus

## loc-declare-languages: The app must declare its languages
The product page lists the languages found in the app binary. Flutter apps must list their languages in
CFBundleLocalizations in Info.plist, or iOS treats the app as English only.
- Source: practitioner consensus (Flutter documentation)

## loc-culture: Adapt, not just translate
Units, dates, currency, names and imagery in screenshots should look local. Reorder screenshots when a market
cares about a different feature first.
- Source: Apple, https://developer.apple.com/localization/

