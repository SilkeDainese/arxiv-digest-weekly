# TECH-DEBT.md — arxiv-digest-weekly

Last updated: 2026-04-11

## Open items

### ~~TD-001 — SRI hashes on Firebase CDN scripts~~ CLOSED 2026-04-11
**Resolution:** Sprint 5 eliminated all CDN scripts. The signup page now loads zero
third-party JS — form POSTs directly to the `subscribe` Cloud Function. No Firebase
JS SDK, no SRI hashes needed. Self-hosted fonts (`infra/fonts/*.woff2`) serve via
the same GitHub Pages origin. SRI is a non-issue.

### ~~TD-002 — AI scoring not yet wired~~ CLOSED 2026-04-10
`shared/ai_scorer.py` implements the full Claude → Vertex Gemini → Gemini API → keyword
cascade. Every paper gets `plain_summary`, `highlight_phrase`, `score_tier`.
Secrets needed: `anthropic-api-key`, `gemini-api-key` in Secret Manager.
Currently running on keyword fallback until Silke populates those secrets.

### ~~TD-003 — No duplicate subscriber guard~~ CLOSED 2026-04-11
**Resolution:** Sprint 5 `subscribe` Cloud Function uses SHA-256(email) as the
Firestore doc ID — natural deduplication. Re-signup of unverified email resends
confirmation; re-signup of verified email returns 200 silently. No double docs.

### TD-004 — CET/CEST timezone handling in Cloud Scheduler
**File:** `deploy.sh`
**Issue:** Scheduler is set to UTC times. Saturday 19:00 UTC = 20:00 CET (winter)
but 21:00 CEST (summer), which is an hour late. Monday 06:00 UTC = 07:00 CET (winter)
but 08:00 CEST (summer), which is an hour late.
**Fix:** Set scheduler timezone to `Europe/Copenhagen` directly. gcloud scheduler
supports IANA timezones. Current UTC workaround means summer digests arrive late.
**Priority:** Medium — not wrong, just slightly off in summer.

### TD-005 — No rate limiting on signup endpoint
**File:** `functions/subscribe/main.py`
**Issue:** `subscribe` Cloud Function has no rate limiting — a spammer could
fill the subscribers collection. Silke's decision: defer to Sprint 6 if abuse
appears. Student audience, low risk.
**Fix:** Cloud Armor policy or simple IP counter in Firestore.
**Priority:** Low — Sprint 6 if needed.

### ~~TD-006 — Signup page Firebase config uses placeholder values~~ CLOSED 2026-04-11
**Resolution:** Sprint 5 removed the Firebase JS SDK entirely. The signup page now
POSTs to the `subscribe` Cloud Function — no Firebase config needed in the browser.

## Closed items

### TD-001 — SRI hashes (closed 2026-04-11)
Sprint 5 eliminated CDN scripts entirely. Zero third-party JS loaded.

### TD-002 — AI scoring (closed 2026-04-10)
Full Claude → Vertex Gemini → Gemini API → keyword cascade in `shared/ai_scorer.py`.

### TD-003 — Duplicate subscriber guard (closed 2026-04-11)
SHA-256(email) doc ID = natural deduplication in `subscribe` Cloud Function.

### TD-006 — Firebase config placeholders (closed 2026-04-11)
Firebase JS SDK removed entirely. No config placeholders remain.

---

## New items from Sprint 5 (2026-04-11)

### TD-010 — Two send_digest tests use short plain_summary stubs (pre-existing)
**File:** `tests/test_functions.py` — `TestSendDigestFunction`
**Issue:** `test_sends_email_per_subscriber` and `test_failed_send_logged_as_failed`
use 20-char stub plain_summary values that fail the quality gate (min 40 chars).
These tests broke when Sprint 4 added the quality gate — they were in the 24-test
baseline failure set. The conftest fix in Sprint 5 exposed them as 2 remaining failures.
**Fix:** Update test stubs to use ≥40-char plain_summary values.
**Priority:** Low — cosmetic test debt, production unaffected.

### TD-011 — Firebase Auth authorized domains not updated via CLI
**File:** N/A — Firebase Console manual step
**Issue:** `silkedainese.github.io` should be added to Firebase Auth → Authorized Domains
even though the signup page no longer uses Firebase JS SDK. Belt-and-suspenders for
any future Firebase features.
**Fix:** Firebase Console → Authentication → Settings → Authorized domains → Add `silkedainese.github.io`
**Priority:** Low — signup works without it; Cloud Function CORS handles the domain restriction.

### TD-012 — Unverified subscriber docs never cleaned up
**File:** `functions/subscribe/main.py`
**Issue:** If a student signs up but never clicks the confirmation link, their
`verified:False` doc stays in Firestore indefinitely. Not a GDPR problem (no digest
sent) but creates clutter.
**Fix:** Cloud Scheduler job that deletes docs where `verified:False` AND
`created_at < now() - 7 days`. Defer to Sprint 6.
**Priority:** Low — Sprint 6.

## New items from Sprint 4 (2026-04-10)

### TD-007 — anthropic-api-key and gemini-api-key not yet populated in Secret Manager
**Issue:** AI scorer falls through to keyword fallback until these secrets exist.
Preview email is sending keyword-scored summaries (first 250 chars of abstract).
**Fix:** Silke adds secrets:
  `gcloud secrets create anthropic-api-key --replication-policy=automatic`
  `echo -n "sk-ant-..." | gcloud secrets versions add anthropic-api-key --data-file=-`
  Same for `gemini-api-key`.
**Priority:** High — AI summaries are the whole point of this sprint.

### TD-008 — myfork remote hook fires on arxiv-digest-weekly (no fork exists)
**Issue:** Pre-push hook `arxiv-dual-push.js` requires `git push myfork` but this
repo has only one remote (`origin`). Push to origin succeeds; hook error is noise.
**Fix:** Update hook to skip the dual-push check for repos without a `myfork` remote,
or add an exception for the `arxiv-digest-weekly` repo.
**Priority:** Low — push succeeds, just noisy.

### TD-009 — pkg_resources deprecation warning in Cloud Functions logs
**Issue:** `google` package imports `pkg_resources` (deprecated in Setuptools 81+).
Generates UserWarning noise in every Cloud Function cold start.
**Fix:** Pin `setuptools<81` in requirements or wait for upstream fix in google packages.
**Priority:** Low — cosmetic only.
