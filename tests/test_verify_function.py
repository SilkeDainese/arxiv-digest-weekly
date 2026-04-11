"""Tests for the verify Cloud Function.

Covers:
  - Valid token + unverified doc → marks verified, returns success HTML
  - Valid token + already-verified doc → idempotent success HTML
  - Expired token → 400 error page
  - Invalid/tampered token → 400 error page
  - Missing token → 400 error page
  - Token hash mismatch (superseded) → 400 error page
  - Doc not found → 404 error page
  - Firestore update failure → 500 error page
"""
from __future__ import annotations

import hashlib
import time
from unittest.mock import MagicMock, patch

import pytest

from shared.tokens import generate_token

SECRET = "test-hmac-secret-32bytes-padded!!"
TEST_EMAIL = "student@phys.au.dk"
EMAIL_HASH = hashlib.sha256(TEST_EMAIL.encode()).hexdigest()
PURPOSE_VERIFY = "verify"


def _make_token(email=TEST_EMAIL, ttl=3600, purpose=PURPOSE_VERIFY):
    return generate_token(email, purpose, SECRET, ttl_override=ttl)


def _make_request(token=None):
    req = MagicMock()
    req.args = {"token": token} if token else {}
    return req


def _unverified_doc(token_str):
    token_hash = hashlib.sha256(token_str.encode()).hexdigest()
    doc = MagicMock()
    doc.exists = True
    doc.to_dict.return_value = {
        "email": TEST_EMAIL,
        "verified": False,
        "verify_token_hash": token_hash,
    }
    return doc


def _verified_doc(token_str):
    token_hash = hashlib.sha256(token_str.encode()).hexdigest()
    doc = MagicMock()
    doc.exists = True
    doc.to_dict.return_value = {
        "email": TEST_EMAIL,
        "verified": True,
        "verify_token_hash": token_hash,
    }
    return doc


def _missing_doc():
    doc = MagicMock()
    doc.exists = False
    return doc


class TestVerifyHappyPath:
    def test_valid_token_returns_success_html(self):
        token = _make_token()
        with (
            patch("functions.verify.main.get_hmac_secret", return_value=SECRET),
            patch("functions.verify.main.subscribers_col") as mock_col,
        ):
            doc_ref = MagicMock()
            doc_ref.get.return_value = _unverified_doc(token)
            mock_col.return_value.document.return_value = doc_ref

            from functions.verify.main import verify
            req = _make_request(token=token)
            html, status, headers = verify(req)

            assert status == 200
            assert "text/html" in headers["Content-Type"]
            assert "subscribed" in html.lower()

    def test_valid_token_marks_verified(self):
        token = _make_token()
        with (
            patch("functions.verify.main.get_hmac_secret", return_value=SECRET),
            patch("functions.verify.main.subscribers_col") as mock_col,
        ):
            doc_ref = MagicMock()
            doc_ref.get.return_value = _unverified_doc(token)
            mock_col.return_value.document.return_value = doc_ref

            from functions.verify.main import verify
            req = _make_request(token=token)
            verify(req)

            doc_ref.update.assert_called_once()
            update_args = doc_ref.update.call_args[0][0]
            assert update_args["verified"] is True
            assert "verified_at" in update_args

    def test_already_verified_returns_success_idempotent(self):
        token = _make_token()
        with (
            patch("functions.verify.main.get_hmac_secret", return_value=SECRET),
            patch("functions.verify.main.subscribers_col") as mock_col,
        ):
            doc_ref = MagicMock()
            doc_ref.get.return_value = _verified_doc(token)
            mock_col.return_value.document.return_value = doc_ref

            from functions.verify.main import verify
            req = _make_request(token=token)
            html, status, _ = verify(req)

            assert status == 200
            assert "subscribed" in html.lower()
            doc_ref.update.assert_not_called()  # no second update


class TestVerifyTokenErrors:
    def test_missing_token_returns_400(self):
        from functions.verify.main import verify
        req = _make_request(token=None)
        html, status, _ = verify(req)
        assert status == 400
        assert "token" in html.lower()

    def test_expired_token_returns_400(self):
        token = _make_token(ttl=-10)  # already expired
        with patch("functions.verify.main.get_hmac_secret", return_value=SECRET):
            from functions.verify.main import verify
            req = _make_request(token=token)
            html, status, _ = verify(req)
            assert status == 400
            assert "expired" in html.lower()

    def test_tampered_token_returns_400(self):
        token = _make_token()
        tampered = token[:-5] + "XXXXX"
        with patch("functions.verify.main.get_hmac_secret", return_value=SECRET):
            from functions.verify.main import verify
            req = _make_request(token=tampered)
            html, status, _ = verify(req)
            assert status == 400

    def test_wrong_purpose_token_returns_400(self):
        # Token generated for "unsubscribe" purpose — verify endpoint rejects it
        from shared.tokens import PURPOSE_UNSUBSCRIBE
        token = generate_token(TEST_EMAIL, PURPOSE_UNSUBSCRIBE, SECRET)
        with patch("functions.verify.main.get_hmac_secret", return_value=SECRET):
            from functions.verify.main import verify
            req = _make_request(token=token)
            html, status, _ = verify(req)
            assert status == 400

    def test_token_hash_mismatch_returns_400(self):
        """Token is valid but not the most recently issued one (superseded)."""
        token = _make_token()
        with (
            patch("functions.verify.main.get_hmac_secret", return_value=SECRET),
            patch("functions.verify.main.subscribers_col") as mock_col,
        ):
            doc_ref = MagicMock()
            # Doc stores a different hash (newer token was issued)
            doc_ref.get.return_value = MagicMock(
                exists=True,
                to_dict=MagicMock(return_value={
                    "email": TEST_EMAIL,
                    "verified": False,
                    "verify_token_hash": "differenthashhere",
                }),
            )
            mock_col.return_value.document.return_value = doc_ref

            from functions.verify.main import verify
            req = _make_request(token=token)
            html, status, _ = verify(req)
            assert status == 400
            assert "superseded" in html.lower() or "link" in html.lower()


class TestVerifyFirestoreErrors:
    def test_doc_not_found_returns_404(self):
        token = _make_token()
        with (
            patch("functions.verify.main.get_hmac_secret", return_value=SECRET),
            patch("functions.verify.main.subscribers_col") as mock_col,
        ):
            doc_ref = MagicMock()
            doc_ref.get.return_value = _missing_doc()
            mock_col.return_value.document.return_value = doc_ref

            from functions.verify.main import verify
            req = _make_request(token=token)
            html, status, _ = verify(req)
            assert status == 404

    def test_firestore_update_failure_returns_500(self):
        token = _make_token()
        with (
            patch("functions.verify.main.get_hmac_secret", return_value=SECRET),
            patch("functions.verify.main.subscribers_col") as mock_col,
        ):
            doc_ref = MagicMock()
            doc_ref.get.return_value = _unverified_doc(token)
            doc_ref.update.side_effect = Exception("Firestore write failed")
            mock_col.return_value.document.return_value = doc_ref

            from functions.verify.main import verify
            req = _make_request(token=token)
            html, status, _ = verify(req)
            assert status == 500
