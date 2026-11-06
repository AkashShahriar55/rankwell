# Metadata

The text fields of the App Store listing: what each is for and its limits. Choosing words: keywords.md.

## meta-limits: Character limits
App name 30, subtitle 30, keyword field 100, promotional text 170, description 4,000, What's New 4,000.
The name needs at least 2 characters. Rankwell counts every limit before a change is planned.
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
