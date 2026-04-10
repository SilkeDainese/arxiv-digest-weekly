#!/usr/bin/env python3
"""One-time setup: authorize Gmail OAuth and store refresh token in Secret Manager.

Run this ONCE after creating the OAuth client in GCP Console.
It opens a browser, you log into arxivdigestau@gmail.com, and this script
captures the refresh token and stores it in Secret Manager.

Prerequisites:
  1. GCP project configured: gcloud config set project silke-hub
  2. OAuth client created in GCP Console (APIs & Services → Credentials)
     - Application type: Desktop app
     - Download the client_secret JSON
  3. Gmail API enabled: gcloud services enable gmail.googleapis.com

Usage:
  python scripts/setup_gmail_oauth.py --client-secret /path/to/client_secret.json

What this stores in Secret Manager:
  - gmail-oauth-refresh-token
  - gmail-oauth-client-id
  - gmail-oauth-client-secret
"""
from __future__ import annotations

import argparse
import json
import sys
import webbrowser
from pathlib import Path
from urllib.parse import urlencode, urlparse, parse_qs

# Requires google-auth-oauthlib for the full OAuth dance.
# Only needed for this one-time setup — not a runtime dependency.
try:
    from google_auth_oauthlib.flow import InstalledAppFlow
except ImportError:
    print("ERROR: Install google-auth-oauthlib first:")
    print("  pip install google-auth-oauthlib google-cloud-secret-manager")
    sys.exit(1)

try:
    from google.cloud import secretmanager
except ImportError:
    print("ERROR: Install google-cloud-secret-manager:")
    print("  pip install google-cloud-secret-manager")
    sys.exit(1)

PROJECT_ID = "silke-hub"

# Gmail scopes needed: compose + send only (principle of least privilege)
SCOPES = ["https://www.googleapis.com/auth/gmail.send"]


def store_secret(sm_client, project_id: str, secret_id: str, value: str) -> None:
    """Create or update a secret in Secret Manager."""
    parent = f"projects/{project_id}"
    secret_name = f"{parent}/secrets/{secret_id}"

    # Try to get the secret; create if missing
    try:
        sm_client.get_secret(request={"name": secret_name})
        print(f"  Secret {secret_id} exists, adding new version...")
    except Exception:
        print(f"  Creating secret {secret_id}...")
        sm_client.create_secret(request={
            "parent": parent,
            "secret_id": secret_id,
            "secret": {"replication": {"automatic": {}}},
        })

    # Add a new version
    sm_client.add_secret_version(request={
        "parent": secret_name,
        "payload": {"data": value.encode("utf-8")},
    })
    print(f"  Stored: {secret_id}")


def main():
    parser = argparse.ArgumentParser(description="Gmail OAuth setup for arxiv-digest-weekly")
    parser.add_argument(
        "--client-secret",
        required=True,
        help="Path to the client_secret JSON downloaded from GCP Console",
    )
    parser.add_argument(
        "--project",
        default=PROJECT_ID,
        help=f"GCP project ID (default: {PROJECT_ID})",
    )
    args = parser.parse_args()

    client_secret_path = Path(args.client_secret)
    if not client_secret_path.exists():
        print(f"ERROR: client secret file not found: {client_secret_path}")
        sys.exit(1)

    # Load client credentials (do NOT store these in git)
    with open(client_secret_path) as f:
        client_data = json.load(f)

    # Support both "installed" and "web" credential types
    cred_data = client_data.get("installed") or client_data.get("web")
    if not cred_data:
        print("ERROR: Unrecognized client secret format. Expected 'installed' or 'web' key.")
        sys.exit(1)

    client_id = cred_data["client_id"]
    client_secret = cred_data["client_secret"]

    print(f"\narXiv Digest Weekly — Gmail OAuth Setup")
    print(f"Project: {args.project}")
    print(f"Sender account: arxivdigestau@gmail.com\n")
    print("A browser window will open. Log in as arxivdigestau@gmail.com and grant access.\n")

    # Run the OAuth flow
    flow = InstalledAppFlow.from_client_secrets_file(
        str(client_secret_path),
        scopes=SCOPES,
    )
    credentials = flow.run_local_server(
        port=8080,
        authorization_prompt_message="Opening browser for authorization...",
        success_message="Authorization complete! You can close this window.",
        open_browser=True,
    )

    refresh_token = credentials.refresh_token
    if not refresh_token:
        print("\nERROR: No refresh token received.")
        print("Make sure 'access_type=offline' is set and you're not already authorized.")
        print("Revoke access at https://myaccount.google.com/permissions and try again.")
        sys.exit(1)

    print(f"\nAuthorization successful! Storing credentials in Secret Manager...")

    sm_client = secretmanager.SecretManagerServiceClient()

    store_secret(sm_client, args.project, "gmail-oauth-refresh-token", refresh_token)
    store_secret(sm_client, args.project, "gmail-oauth-client-id", client_id)
    store_secret(sm_client, args.project, "gmail-oauth-client-secret", client_secret)

    print(f"\nAll three secrets stored successfully in project '{args.project}':")
    print("  - gmail-oauth-refresh-token")
    print("  - gmail-oauth-client-id")
    print("  - gmail-oauth-client-secret")
    print("\nNext: run deploy.sh to deploy the Cloud Functions.")
    print("\nSECURITY: Do NOT commit the client_secret JSON file. Add it to .gitignore.")


if __name__ == "__main__":
    main()
