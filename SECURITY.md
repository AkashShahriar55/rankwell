# Security policy

Rankwell handles App Store Connect and Google Play keys and can change live store listings, so security
reports get priority.

## Reporting a vulnerability

Please do not open a public issue. Use GitHub's private reporting instead: **Security → Report a
vulnerability** on this repository. Include what you found, how to reproduce it and what an attacker could
do with it.

You will get a reply within seven days. Fixes for confirmed problems ship in a patch release, and the
advisory credits you unless you ask otherwise.

## Supported versions

Only the latest release gets security fixes.

## What Rankwell does with your keys

- The App Store Connect key and the Google Play service account are stored in the macOS Keychain. Environment
  variables can supply them for CI.
- Keys never go into files, logs, the database, MCP results or plan payloads.
- Every change to a store is shown to you and applied only after your approval, and is recorded in an audit
  log.
- Rankwell has no server. Data goes only to Apple and Google.
