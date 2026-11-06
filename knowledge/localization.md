# Localization

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
