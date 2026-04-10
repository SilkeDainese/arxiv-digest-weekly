"""GCP Secret Manager access helpers.

All secrets live in Secret Manager — nothing in environment variables,
nothing in config files, nothing in the repo.

Secrets managed:
  - hmac-secret          — 32-byte random string for token signing
  - gmail-oauth-refresh-token  — refresh token for arxivdigestau@gmail.com
  - gmail-oauth-client-id      — OAuth app client ID
  - gmail-oauth-client-secret  — OAuth app client secret
"""
from __future__ import annotations

import functools
import os
from typing import Optional

# Lazy import so tests can mock without the real SDK installed
_secret_client = None


def _get_client():
    global _secret_client
    if _secret_client is None:
        from google.cloud import secretmanager
        _secret_client = secretmanager.SecretManagerServiceClient()
    return _secret_client


def get_secret(secret_id: str, project_id: Optional[str] = None, version: str = "latest") -> str:
    """Fetch a secret value from GCP Secret Manager.

    Args:
        secret_id: The secret name (e.g. "hmac-secret").
        project_id: GCP project ID. Defaults to GOOGLE_CLOUD_PROJECT env var.
        version: Secret version (default: "latest").

    Returns:
        The secret value as a string.

    Raises:
        RuntimeError: If project_id cannot be determined.
        google.api_core.exceptions.NotFound: If the secret does not exist.
    """
    pid = project_id or os.environ.get("GOOGLE_CLOUD_PROJECT") or os.environ.get("GCLOUD_PROJECT")
    if not pid:
        raise RuntimeError(
            "Cannot determine GCP project ID. Set GOOGLE_CLOUD_PROJECT environment variable."
        )

    client = _get_client()
    name = f"projects/{pid}/secrets/{secret_id}/versions/{version}"
    response = client.access_secret_version(request={"name": name})
    return response.payload.data.decode("utf-8").strip()


# Module-level cached accessors — called once per Function cold start,
# then cached in module scope for subsequent requests.

@functools.lru_cache(maxsize=None)
def get_hmac_secret() -> str:
    return get_secret("hmac-secret")


@functools.lru_cache(maxsize=None)
def get_gmail_refresh_token() -> str:
    return get_secret("gmail-oauth-refresh-token")


@functools.lru_cache(maxsize=None)
def get_gmail_client_id() -> str:
    return get_secret("gmail-oauth-client-id")


@functools.lru_cache(maxsize=None)
def get_gmail_client_secret() -> str:
    return get_secret("gmail-oauth-client-secret")
