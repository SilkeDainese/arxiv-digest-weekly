# TECH-DEBT.md — arxiv-digest-weekly

Last updated: 2026-04-10

## Open items

### TD-001 — SRI hashes on Firebase CDN scripts
**File:** `infra/signup.html`
**Issue:** Firebase JS SDK loaded from `www.gstatic.com` without real SRI hashes.
Placeholders are in place (`sha384-placeholder-...`). WEB-2 compliance requires
actual hashes before the page goes to `silkedainese.github.io`.
**Fix:** Run `openssl dgst -sha384 -binary firebase-app-compat.js | openssl base64 -A`
against the downloaded files, replace the placeholder strings.
**Priority:** Medium — before signup page goes live.

### TD-002 — AI scoring not yet wired (keyword-only for now)
**File:** `shared/arxiv_fetcher.py`
**Issue:** Scoring is keyword-based only. The old `digest.py` had Claude → Gemini → keyword
cascade. For the student digest this is acceptable (volume is high, AI adds cost),
but Silke may want to add LLM scoring for the top-N papers later.
**Fix:** Add optional `score_with_llm(papers, top_n=20)` function gated on
`ENABLE_LLM_SCORING` env var, defaulting off.
**Priority:** Low — keyword scoring is fine for student use.

### TD-003 — No duplicate subscriber guard
**File:** `firestore.rules`, `infra/signup.html`
**Issue:** Firestore rules allow create but don't check for existing email.
A student could sign up twice with the same email and get two digests.
**Fix:** Either: (a) add a signup Cloud Function that checks before writing,
or (b) use email as the Firestore doc ID (requires restructuring the collection).
Option (b) is cleaner but requires a migration for any existing docs.
**Priority:** Low — minor annoyance, not a data leak.

### TD-004 — CET/CEST timezone handling in Cloud Scheduler
**File:** `deploy.sh`
**Issue:** Scheduler is set to UTC times. Saturday 19:00 UTC = 20:00 CET (winter)
but 21:00 CEST (summer), which is an hour late. Monday 06:00 UTC = 07:00 CET (winter)
but 08:00 CEST (summer), which is an hour late.
**Fix:** Set scheduler timezone to `Europe/Copenhagen` directly. gcloud scheduler
supports IANA timezones. Current UTC workaround means summer digests arrive late.
**Priority:** Medium — not wrong, just slightly off in summer.

### TD-005 — No rate limiting on signup endpoint
**File:** `infra/signup.html`, `firestore.rules`
**Issue:** Firestore rules allow unlimited creates from any origin. A spammer
could fill the subscribers collection.
**Fix:** Either: (a) add Cloud Armor policy, or (b) use a signup Cloud Function
with IP-based rate limiting (e.g. via Firebase App Check).
**Priority:** Low — student audience, low abuse risk.

### TD-006 — Signup page Firebase config uses placeholder values
**File:** `infra/signup.html`
**Issue:** `FIREBASE_CONFIG` object contains placeholder strings. Must be filled
in before deployment.
**Fix:** Silke needs to copy real values from Firebase Console → Project Settings.
**Priority:** Blocker for signup page deployment.

## Closed items

None yet — new repo.
