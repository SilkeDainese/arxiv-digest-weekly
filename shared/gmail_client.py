"""Gmail API client using OAuth2 refresh token.

Authentication flow:
  1. refresh token + client credentials stored in Secret Manager
  2. On each cold start, exchange refresh token for access token
  3. Access token used for Gmail API calls via requests (no heavy SDK needed)

This avoids SMTP app passwords entirely — all auth via OAuth2.
"""
from __future__ import annotations

import base64
import logging
import time
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional

import requests

from shared.secrets import (
    get_gmail_client_id,
    get_gmail_client_secret,
    get_gmail_refresh_token,
)

logger = logging.getLogger(__name__)

GMAIL_SENDER = "arxivdigestau@gmail.com"
GMAIL_SENDER_NAME = "Arxiv Digest"
TOKEN_URL = "https://oauth2.googleapis.com/token"
GMAIL_SEND_URL = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"

# Module-level access token cache (valid per cold start)
_access_token: Optional[str] = None
_token_expiry: float = 0.0


def _refresh_access_token() -> str:
    """Exchange the refresh token for a fresh access token."""
    global _access_token, _token_expiry

    resp = requests.post(TOKEN_URL, data={
        "client_id": get_gmail_client_id(),
        "client_secret": get_gmail_client_secret(),
        "refresh_token": get_gmail_refresh_token(),
        "grant_type": "refresh_token",
    }, timeout=15)

    if resp.status_code != 200:
        raise GmailAuthError(
            f"Failed to refresh access token: HTTP {resp.status_code} — {resp.text[:200]}"
        )

    data = resp.json()
    _access_token = data["access_token"]
    _token_expiry = time.time() + data.get("expires_in", 3600) - 60  # 60s buffer

    logger.info("Gmail access token refreshed, expires in ~%ds", data.get("expires_in", 3600))
    return _access_token


def _get_access_token() -> str:
    """Return a valid access token, refreshing if needed."""
    global _access_token, _token_expiry
    if _access_token is None or time.time() >= _token_expiry:
        return _refresh_access_token()
    return _access_token


def build_message(
    to_email: str,
    subject: str,
    html_body: str,
    text_body: str,
    unsubscribe_url: Optional[str] = None,
    manage_url: Optional[str] = None,
) -> MIMEMultipart:
    """Build a MIME email message with HTML + plaintext parts.

    Always includes List-Unsubscribe headers when unsubscribe_url is provided.
    """
    msg = MIMEMultipart("alternative")
    msg["From"] = f"{GMAIL_SENDER_NAME} <{GMAIL_SENDER}>"
    msg["To"] = to_email
    msg["Subject"] = subject

    if unsubscribe_url:
        msg["List-Unsubscribe"] = f"<{unsubscribe_url}>, <mailto:{GMAIL_SENDER}?subject=unsubscribe>"
        msg["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"

    # Order: text first, then HTML (email clients prefer the last part)
    msg.attach(MIMEText(text_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    return msg


def send_message(msg: MIMEMultipart) -> None:
    """Send a built MIME message via Gmail API.

    Args:
        msg: MIME message built by build_message().

    Raises:
        GmailSendError: If the API call fails.
    """
    raw_bytes = msg.as_bytes()
    raw_b64 = base64.urlsafe_b64encode(raw_bytes).decode("ascii")

    access_token = _get_access_token()

    resp = requests.post(
        GMAIL_SEND_URL,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        },
        json={"raw": raw_b64},
        timeout=30,
    )

    if resp.status_code not in (200, 201):
        # Log recipient domain only — no full email in logs
        to_domain = msg.get("To", "").split("@")[-1] if "@" in msg.get("To", "") else "unknown"
        raise GmailSendError(
            f"Gmail API error sending to @{to_domain}: HTTP {resp.status_code} — {resp.text[:200]}"
        )


class GmailAuthError(Exception):
    """Raised when OAuth token refresh fails."""


class GmailSendError(Exception):
    """Raised when the Gmail API rejects a send request."""
