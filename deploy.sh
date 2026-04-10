#!/bin/bash
# deploy.sh — Deploy arxiv-digest-weekly to GCP
#
# Deploys all 5 Cloud Functions, applies Firestore rules,
# and creates Cloud Scheduler jobs.
# Idempotent — safe to re-run.
#
# Prerequisites:
#   - gcloud CLI authenticated: gcloud auth login
#   - Project set: gcloud config set project silke-hub
#   - APIs enabled: see README "One-time setup"
#   - Secrets already created in Secret Manager (run setup scripts first)
#
# Usage:
#   bash deploy.sh

set -euo pipefail

# ── Configuration ──────────────────────────────────────────────────────────
PROJECT_ID="silke-hub"
REGION="europe-west1"
RUNTIME="python312"
SERVICE_ACCOUNT="arxiv-digest-sa@${PROJECT_ID}.iam.gserviceaccount.com"

# Memory/timeout per function
DEFAULT_MEMORY="256Mi"
DEFAULT_TIMEOUT="300s"
MAILER_MEMORY="512Mi"
MAILER_TIMEOUT="540s"  # Sending to 50 subscribers takes time

echo "=== arxiv-digest-weekly deploy ==="
echo "Project: ${PROJECT_ID}"
echo "Region:  ${REGION}"
echo ""

# ── Confirm project ────────────────────────────────────────────────────────
ACTIVE_PROJECT=$(gcloud config get-value project 2>/dev/null)
if [ "${ACTIVE_PROJECT}" != "${PROJECT_ID}" ]; then
    echo "ERROR: Active project is '${ACTIVE_PROJECT}', expected '${PROJECT_ID}'"
    echo "Run: gcloud config set project ${PROJECT_ID}"
    exit 1
fi

# ── Service account setup (idempotent) ────────────────────────────────────
echo "--- Setting up service account ---"
gcloud iam service-accounts create arxiv-digest-sa \
    --display-name="arXiv Digest Weekly Functions" \
    --project="${PROJECT_ID}" 2>/dev/null || echo "Service account already exists, continuing."

# Grant Firestore access (specific collections only via conditions not available
# at project level — functions use the SA, Firestore rules handle collection-level)
gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
    --member="serviceAccount:${SERVICE_ACCOUNT}" \
    --role="roles/datastore.user" \
    --condition=None \
    --quiet 2>/dev/null || true

# Grant Secret Manager access
gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
    --member="serviceAccount:${SERVICE_ACCOUNT}" \
    --role="roles/secretmanager.secretAccessor" \
    --condition=None \
    --quiet 2>/dev/null || true

echo "Service account configured."

# ── Apply Firestore rules ──────────────────────────────────────────────────
echo ""
echo "--- Applying Firestore rules ---"
if command -v firebase &>/dev/null; then
    firebase deploy --only firestore:rules --project "${PROJECT_ID}"
    echo "Firestore rules applied."
else
    echo "WARNING: firebase CLI not found. Apply firestore.rules manually:"
    echo "  npm install -g firebase-tools"
    echo "  firebase deploy --only firestore:rules --project ${PROJECT_ID}"
fi

# ── Helper: deploy a single function ──────────────────────────────────────
deploy_function() {
    local name="$1"        # Cloud Function name (as deployed)
    local entry_point="$2" # Python function name in main.py
    local source_dir="$3"  # Directory containing main.py
    local memory="${4:-${DEFAULT_MEMORY}}"
    local timeout="${5:-${DEFAULT_TIMEOUT}}"

    echo ""
    echo "--- Deploying ${name} ---"

    # Copy shared package into function directory for deployment
    cp -r shared "${source_dir}/shared"

    gcloud functions deploy "${name}" \
        --gen2 \
        --runtime="${RUNTIME}" \
        --region="${REGION}" \
        --source="${source_dir}" \
        --entry-point="${entry_point}" \
        --service-account="${SERVICE_ACCOUNT}" \
        --memory="${memory}" \
        --timeout="${timeout}" \
        --set-env-vars="GOOGLE_CLOUD_PROJECT=${PROJECT_ID},FUNCTION_REGION=${REGION}" \
        --allow-unauthenticated \
        --quiet

    # Clean up copied shared package
    rm -rf "${source_dir}/shared"

    echo "${name} deployed."
}

# ── Deploy all functions ───────────────────────────────────────────────────
deploy_function "prep_and_preview"  "prep_and_preview"  "functions/prep_preview"
deploy_function "send_digest"       "send_digest"       "functions/mailer"      "${MAILER_MEMORY}" "${MAILER_TIMEOUT}"
deploy_function "unsubscribe"       "unsubscribe"       "functions/unsub"
deploy_function "manage"            "manage"            "functions/manage"
deploy_function "cancel_send"       "cancel_send"       "functions/cancel"

# ── Cloud Scheduler jobs ───────────────────────────────────────────────────
echo ""
echo "--- Setting up Cloud Scheduler ---"

# Get function URLs
PREP_URL=$(gcloud functions describe prep_and_preview \
    --gen2 --region="${REGION}" --format="value(serviceConfig.uri)" 2>/dev/null)
MAILER_URL=$(gcloud functions describe send_digest \
    --gen2 --region="${REGION}" --format="value(serviceConfig.uri)" 2>/dev/null)

# Saturday 20:00 CET = Saturday 19:00 UTC (winter) or 18:00 UTC (summer)
# Using 19:00 UTC as conservative choice — adjust if needed for BST
gcloud scheduler jobs create http prep-and-preview-weekly \
    --location="${REGION}" \
    --schedule="0 19 * * 6" \
    --uri="${PREP_URL}" \
    --http-method=POST \
    --oidc-service-account-email="${SERVICE_ACCOUNT}" \
    --time-zone="UTC" \
    --description="Saturday 20:00 CET: fetch arXiv papers and send preview to Silke" \
    --quiet 2>/dev/null || \
gcloud scheduler jobs update http prep-and-preview-weekly \
    --location="${REGION}" \
    --schedule="0 19 * * 6" \
    --uri="${PREP_URL}" \
    --http-method=POST \
    --oidc-service-account-email="${SERVICE_ACCOUNT}" \
    --time-zone="UTC" \
    --quiet

echo "Scheduler: prep_and_preview -> Saturday 19:00 UTC"

# Monday 07:00 CET = Monday 06:00 UTC
gcloud scheduler jobs create http send-digest-weekly \
    --location="${REGION}" \
    --schedule="0 6 * * 1" \
    --uri="${MAILER_URL}" \
    --http-method=POST \
    --oidc-service-account-email="${SERVICE_ACCOUNT}" \
    --time-zone="UTC" \
    --description="Monday 07:00 CET: send weekly arxiv digest to subscribers" \
    --quiet 2>/dev/null || \
gcloud scheduler jobs update http send-digest-weekly \
    --location="${REGION}" \
    --schedule="0 6 * * 1" \
    --uri="${MAILER_URL}" \
    --http-method=POST \
    --oidc-service-account-email="${SERVICE_ACCOUNT}" \
    --time-zone="UTC" \
    --quiet

echo "Scheduler: send_digest -> Monday 06:00 UTC"

# ── Summary ────────────────────────────────────────────────────────────────
echo ""
echo "=== Deploy complete ==="
echo ""
echo "Functions deployed:"
echo "  prep_and_preview : ${PREP_URL}"
echo "  send_digest      : ${MAILER_URL}"
gcloud functions describe unsubscribe --gen2 --region="${REGION}" --format="value(serviceConfig.uri)" 2>/dev/null | xargs -I{} echo "  unsubscribe      : {}"
gcloud functions describe manage --gen2 --region="${REGION}" --format="value(serviceConfig.uri)" 2>/dev/null | xargs -I{} echo "  manage           : {}"
gcloud functions describe cancel_send --gen2 --region="${REGION}" --format="value(serviceConfig.uri)" 2>/dev/null | xargs -I{} echo "  cancel_send      : {}"

echo ""
echo "Next steps:"
echo "  1. Test prep_and_preview: gcloud functions call prep_and_preview --gen2 --region=${REGION}"
echo "  2. Check preview email arrives at silke.dainese@gmail.com"
echo "  3. Add signup page origin to Firebase allowed origins"
