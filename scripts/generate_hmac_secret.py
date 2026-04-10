#!/usr/bin/env python3
"""Generate a 32-byte HMAC secret and store it in Secret Manager.

Run once during initial setup.

Usage:
  python scripts/generate_hmac_secret.py
"""
from __future__ import annotations

import secrets
import sys

try:
    from google.cloud import secretmanager
except ImportError:
    print("ERROR: pip install google-cloud-secret-manager")
    sys.exit(1)

PROJECT_ID = "silke-hub"
SECRET_ID = "hmac-secret"


def main():
    # Generate a cryptographically secure 32-byte hex secret
    secret_value = secrets.token_hex(32)

    sm_client = secretmanager.SecretManagerServiceClient()
    parent = f"projects/{PROJECT_ID}"
    secret_name = f"{parent}/secrets/{SECRET_ID}"

    try:
        sm_client.get_secret(request={"name": secret_name})
        print(f"Secret '{SECRET_ID}' already exists. Adding new version.")
    except Exception:
        print(f"Creating secret '{SECRET_ID}'...")
        sm_client.create_secret(request={
            "parent": parent,
            "secret_id": SECRET_ID,
            "secret": {"replication": {"automatic": {}}},
        })

    sm_client.add_secret_version(request={
        "parent": secret_name,
        "payload": {"data": secret_value.encode("utf-8")},
    })

    print(f"HMAC secret stored in Secret Manager: {SECRET_ID}")
    print("Do NOT print or log the secret value — it stays in Secret Manager only.")


if __name__ == "__main__":
    main()
