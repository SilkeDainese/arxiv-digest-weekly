# arxiv-digest-weekly

Personalized weekly arXiv paper digest for AU astronomy students.
Fetches papers Saturday evening, sends personalized digests Monday morning.
All secrets in GCP Secret Manager — nothing in this repo.

**Sender:** arxivdigestau@gmail.com  
**Project:** silke-hub  
**Region:** europe-west1  

## Architecture

```
Saturday 20:00 CET
  Cloud Scheduler → prep_and_preview
    → fetch arXiv papers (astro-ph.*)
    → score globally
    → store in Firestore /pending_digest/{week_iso}
    → build preview email with cancel button
    → send to silke.dainese@gmail.com

Silke reviews preview. If something looks wrong, clicks "CANCEL MONDAY SEND".

Monday 07:00 CET
  Cloud Scheduler → send_digest
    → check hold flag (exit if set)
    → load /pending_digest/{week_iso}
    → for each subscriber:
        build personalized digest (their topics only)
        send via Gmail API (arxivdigestau@gmail.com)
        write /sent_log entry
        update subscriber last_sent

HTTP endpoints (signed tokens required):
  /unsubscribe?t=<token>   — delete subscriber
  /manage?t=<token>        — update topics
  /cancel_send?t=<token>   — set hold flag (Silke only, 48h window)
```

## One-time setup

**Do this once before running deploy.sh.**

### 1. Confirm GCP project

```bash
gcloud config set project silke-hub
gcloud config get-value project  # should print: silke-hub
```

### 2. Enable required APIs

```bash
gcloud services enable \
  cloudfunctions.googleapis.com \
  cloudscheduler.googleapis.com \
  firestore.googleapis.com \
  secretmanager.googleapis.com \
  gmail.googleapis.com \
  firebase.googleapis.com
```

### 3. Generate HMAC secret

```bash
pip install google-cloud-secret-manager
python scripts/generate_hmac_secret.py
```

### 4. Gmail OAuth setup (requires browser — do this on your Mac)

This is the one step that requires human interaction. It opens a browser and
asks you to log in as `arxivdigestau@gmail.com`.

**First:** create an OAuth 2.0 Desktop app client in GCP Console:
- Go to APIs & Services → Credentials → Create Credentials → OAuth client ID
- Application type: **Desktop app**
- Name: "arXiv Digest Weekly"
- Download the JSON file (e.g. `client_secret_xxx.json`)

**Then run the setup script:**

```bash
pip install google-auth-oauthlib google-cloud-secret-manager
python scripts/setup_gmail_oauth.py --client-secret /path/to/client_secret_xxx.json
```

A browser will open. Log in as `arxivdigestau@gmail.com` and approve access.
The script stores three secrets in Secret Manager:
- `gmail-oauth-refresh-token`
- `gmail-oauth-client-id`
- `gmail-oauth-client-secret`

Do NOT commit the `client_secret_xxx.json` file. It is already in `.gitignore`.

### 5. Deploy

```bash
bash deploy.sh
```

This is idempotent — safe to re-run.

### 6. Signup page

Edit `infra/signup.html` and fill in `FIREBASE_CONFIG` with real values from:
Firebase Console → Project Settings → Your apps → Web app → Firebase SDK snippet

Then copy the page to your website:
```bash
cp infra/signup.html ~/Projects/SilkeDainese.github.io/arxiv-digest/index.html
cd ~/Projects/SilkeDainese.github.io && git add arxiv-digest/ && git commit -m "Add arXiv digest signup page" && git push
```

Add the origin to Firebase authorized domains:
Firebase Console → Authentication → Settings → Authorized domains → Add domain
Domain: `silkedainese.github.io`

## Weekly operation

**Normal week:**
1. Saturday evening: preview email arrives from `arxivdigestau@gmail.com`
2. Review the top 10 papers and the example digest
3. If everything looks fine: do nothing. Digest goes out Monday 07:00 CET automatically.
4. If something looks wrong: click the red "CANCEL MONDAY SEND" button in the email.

**After cancelling:**
- The hold flag is set in Firestore `/pending_digest/{week_iso}.hold_monday_send = true`
- Monday run will see the flag and exit without sending
- To re-run prep manually: `gcloud functions call prep_and_preview --gen2 --region=europe-west1`
- This overwrites the hold flag because it writes a fresh document with `hold_monday_send: false`

**To manually trigger:**
```bash
# Run prep now (Saturday function)
gcloud functions call prep_and_preview --gen2 --region=europe-west1 --data='{}'

# Run the Monday mailer now
gcloud functions call send_digest --gen2 --region=europe-west1 --data='{}'
```

## Debugging

**View logs:**
```bash
# prep_and_preview logs
gcloud functions logs read prep_and_preview --gen2 --region=europe-west1 --limit=50

# send_digest logs
gcloud functions logs read send_digest --gen2 --region=europe-west1 --limit=50
```

**Check sent_log in Firestore:**
Firebase Console → Firestore → sent_log collection

**Check pending_digest:**
Firebase Console → Firestore → pending_digest collection

## IAM / Security

- Cloud Functions run under `arxiv-digest-sa@silke-hub.iam.gserviceaccount.com`
- SA has `roles/datastore.user` (Firestore) and `roles/secretmanager.secretAccessor`
- Nothing else — principle of least privilege
- Signed tokens expire: unsubscribe/manage 90 days, cancel_send 48 hours
- Firestore rules: public create-only on `/subscribers`, everything else denied
- No passwords, no SMTP credentials, no secrets in repo

## Costs

At 20-50 subscribers, expected monthly cost is $0:
- Cloud Functions: well within free tier (2M invocations/month free, you use 2/week)
- Cloud Scheduler: 3 free jobs/month
- Firestore: well within free tier (50K reads/day, you use ~100/week)
- Secret Manager: first 10K accesses/month free
- Gmail API: free

Total: $0/month.

## Running tests

```bash
pip install -r requirements-dev.txt
pip install -r requirements.txt
pytest tests/ -v
```

## Repo structure

```
shared/                 Shared utilities (tokens, Firestore, Gmail, email builder, arxiv fetcher)
functions/
  prep_preview/         Saturday function: fetch + preview
  mailer/               Monday function: personalized digests
  unsub/                Unsubscribe handler
  manage/               Topic management handler
  cancel/               Cancel Monday send handler
scripts/
  setup_gmail_oauth.py  One-time OAuth setup
  generate_hmac_secret.py  One-time HMAC secret generation
infra/
  signup.html           Signup page (deploy to silkedainese.github.io/arxiv-digest)
  firebase.json         Firebase CLI config
tests/                  Pytest test suite
firestore.rules         Firestore security rules
deploy.sh               Idempotent deploy script
```
