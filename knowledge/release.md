# Release

Getting a version from build to the App Store, and using releases for ASO.

## rel-checklist: Before you submit
The build is uploaded and processed; export compliance is answered; App Privacy and the age rating
questionnaire are current; screenshots exist for every required size in every localization; review notes
and a demo account are filled in; support and privacy policy URLs work; new in-app purchases are attached to
the version. `release_check` reports what is still missing.
- Source: Apple, https://developer.apple.com/help/app-store-connect/reference/app-information/

## rel-export-compliance: Answer the encryption question once
Add ITSAppUsesNonExemptEncryption to Info.plist (NO when the app uses only standard HTTPS and system
encryption) so builds do not wait for the export compliance question.
- Source: practitioner consensus

## rel-review-notes: Write review notes for a stranger
Say in two or three lines what the app does, where the main feature is, what the demo account is, and
anything that needs hardware or a location. Good notes prevent most 2.1 back-and-forth.
- Source: Apple, https://developer.apple.com/app-store/review/guidelines/

## rel-review-time: Plan for one to three days of review
Most submissions are reviewed within a day or two, but a rejection and resubmission can add days. Do not
promise a launch date without a buffer; release manually when timing matters.
- Source: practitioner consensus

## rel-manual-release: Release manually when the date matters
Choose manual release to publish an approved version at a chosen moment (a campaign, an event, a listing
change you want to measure from a clean date).
- Source: Apple, https://developer.apple.com/help/app-store-connect/reference/app-information/

## rel-phased: Phased release for risky updates
Phased release spreads an update to automatic updaters over seven days and can be paused if a problem shows.
New downloads always get the new version, so listing changes go live for everyone on release.
- Source: practitioner consensus

## rel-metadata-with-version: Bundle listing changes with a release
Name, subtitle, keywords, description and screenshots ship with a version. Decide the listing change before
submitting, record it (aso_note decision) and open an experiment with its baseline, so the next check knows
what changed and when.
- Source: Rankwell practice

## rel-cadence: Update regularly
Regular updates give chances to test metadata, answer reviews with fixes and keep the app looking cared for.
Each update is also a moment to reconsider the keyword set.
- Source: practitioner consensus

## rel-first-iap: First in-app purchase ships with a version
The first in-app purchase or subscription of each type must be submitted together with an app version; later
ones can go on their own.
- Source: Apple, https://developer.apple.com/help/app-store-connect/release-notes/

## rel-expedited: Expedited review is for emergencies
For a critical bug fix or a time-bound event you can request an expedited review. Use it rarely.
- Source: practitioner consensus
