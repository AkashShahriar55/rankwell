# App Review: metadata rules and common rejections

The guideline numbers App Review cites most for listings and ASO-related code, and how to fix each.
Always read the exact message Apple sent; it names the guideline and often the screen.

## rev-complete: 2.1 App completeness
Rejected for crashes, placeholder text, broken links, features that do not load, or in-app purchases that do
not appear. Fix: test the release build on a device, remove placeholder strings, make sure every product id
loads, and resubmit with a note on what changed.
- Source: Apple, https://developer.apple.com/app-store/review/guidelines/

## rev-demo-account: 2.1 Give the reviewer a way in
If any feature needs an account, put a working demo account (or a demo mode) and short steps in App Review
Information. Explain hardware, location or subscription needs there too. Missing access is one of the most
common rejections and the easiest to avoid.
- Source: Apple, https://developer.apple.com/app-store/review/guidelines/

## rev-accurate-metadata: 2.3 and 2.3.1 Metadata must match the app
Description, screenshots and previews must show what the app does today. Promising features that are not in
the build, hidden features, or misleading claims get rejected. Fix: remove the claim or ship the feature.
- Source: Apple, https://developer.apple.com/app-store/review/guidelines/

## rev-screenshots: 2.3.3 Screenshots show the app in use
Not only title art, splash or login screens. Fix: replace with screenshots of the real interface; overlays
and captions are fine.
- Source: Apple, https://developer.apple.com/app-store/review/guidelines/

## rev-name-keywords: 2.3.7 Name, subtitle and keywords
A unique name, no prices, no other apps' names, trademarks or irrelevant terms packed into metadata, no
unverifiable claims in the subtitle. Fix: remove the offending words with a listing plan; this needs no new
build if only metadata was rejected.
- Source: Apple, https://developer.apple.com/app-store/review/guidelines/

## rev-name-match: 2.3.8 Store name and device name should be similar
Metadata must suit all audiences (4+), and the store name, the name under the icon and the icons should be
similar enough not to confuse people. Fix: make the home screen name the brand part of the store name.
- Source: Apple, https://developer.apple.com/app-store/review/guidelines/

## rev-other-platforms: 2.3.10 No other platforms
No names, icons or images of other mobile platforms or app stores in metadata, unless there is approved
interactive functionality. Fix: remove them from text and screenshots.
- Source: Apple, https://developer.apple.com/app-store/review/guidelines/

## rev-whats-new: 2.3.12 Describe significant changes
What's New must describe significant changes. Fix: list the real changes.
- Source: Apple, https://developer.apple.com/app-store/review/guidelines/

## rev-permission-strings: 5.1.1 Permission text must explain the use
Each permission request needs a usage string that clearly says what the data is used for, ideally with an
example. Only ask for what the feature needs, when the feature needs it, and do not block the app when the
user says no. Fix: rewrite the string ("<App> uses the camera to scan receipts so you can attach them to
expenses") and resubmit a new build.
- Source: Apple, https://developer.apple.com/app-store/review/guidelines/

## rev-account-deletion: 5.1.1 Account deletion in the app
Apps that let people create an account must let them start deleting it from inside the app. Fix: add a
clear "Delete account" path in settings.
- Source: Apple, https://developer.apple.com/app-store/review/guidelines/

## rev-privacy-policy: 5.1.1 Privacy policy link
A privacy policy link is required in App Store Connect and must be easy to find in the app. App Privacy
answers must match what the app and its SDKs collect.
- Source: Apple, https://developer.apple.com/app-store/review/guidelines/

## rev-tracking: 5.1.2 Ask before tracking
Tracking users across other companies' apps or websites (personalised ads, attribution) requires the App
Tracking Transparency prompt first, and the App Privacy answers must say so. Ask once the app is active and
visible, or the prompt may not appear and the reviewer cannot find it.
- Source: Apple, https://developer.apple.com/documentation/apptrackingtransparency

## rev-privacy-manifest: Privacy manifest for required-reason APIs
Apps and SDKs that use certain APIs (for example UserDefaults, file timestamps, system boot time, disk space)
must declare an approved reason in PrivacyInfo.xcprivacy; uploads that do not are flagged.
- Source: Apple, https://developer.apple.com/documentation/bundleresources/privacy-manifest-files

## rev-sign-in: 4.8 Login services
If the app offers a third-party or social login for the primary account, it must also offer an equivalent
login option with strong privacy features, such as Sign in with Apple.
- Source: Apple, https://developer.apple.com/app-store/review/guidelines/

## rev-iap-ready: 3.1.1 and 3.1.2 In-app purchases and subscriptions
Digital content and features must be sold with in-app purchase. Products must exist in App Store Connect with
the same ids as the code, the first purchase of each type is submitted together with an app version, and a
Restore Purchases option must exist. Subscriptions must say clearly what the user gets, the price and the
period, and link to terms and privacy policy. Fix: check ids, attach the products to the version, add the
missing text and links.
- Source: Apple, https://developer.apple.com/app-store/review/guidelines/

## rev-min-functionality: 4.2 and 4.3 Minimum functionality and spam
Apps that are thin wrappers, near-copies of other apps, or crowd an already saturated category without being
clearly different get rejected. Fix: explain in the review notes what makes the app distinct, or add the
feature that does.
- Source: Apple, https://developer.apple.com/app-store/review/guidelines/

## rev-reply: Answering a rejection
Reply in App Store Connect (App Review messages). If it is a misunderstanding, explain with steps or a short
screen recording. If the rejection is only about metadata, fix the listing and resubmit without a new build.
If you believe the decision is wrong, you can appeal to the App Review Board.
- Source: Apple, https://developer.apple.com/app-store/review/guidelines/

## rev-resubmit: Resubmit the same submission
After a fix, resubmit the existing review submission: the rejected item has to be marked resolved first, or
App Store Connect refuses with a "not ready to be submitted" error. Rankwell's plan_submit does this.
- Source: Rankwell (observed with the App Store Connect API)
