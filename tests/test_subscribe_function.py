"""Tests for the subscribe Cloud Function.

Covers:
  - Valid email → doc created, email sent, 200 ok
  - Invalid email format → 400 error
  - Missing email → 400 error
  - Duplicate unverified signup → resend email, 200 ok
  - Duplicate verified signup → silent 200 ok (no double-send)
  - Firestore write failure → 500 error
  - Gmail send failure → 500 error
  - CORS preflight → 204 no content
  - Wrong HTTP method → 405
"""
from __future__ import annotations

import hashlib
from unittest.mock import MagicMock, patch, ANY

import pytest

# The subscribe function imports are path-sensitive; we test via direct import
# after patching the shared dependencies.

SECRET = "test-hmac-secret-32bytes-padded!!"
TEST_EMAIL = "student@phys.au.dk"
EMAIL_HASH = hashlib.sha256(TEST_EMAIL.encode()).hexdigest()


def _make_request(method="POST", json_body=None, origin="https://silkedainese.github.io"):
    req = MagicMock()
    req.method = method
    req.headers = {"Origin": origin}
    req.get_json = MagicMock(return_value=json_body)
    req.args = {}
    return req


def _non_existing_doc():
    doc = MagicMock()
    doc.exists = False
    return doc


def _existing_unverified_doc():
    doc = MagicMock()
    doc.exists = True
    doc.to_dict.return_value = {"email": TEST_EMAIL, "verified": False, "verify_token_hash": "abc"}
    return doc


def _existing_verified_doc():
    doc = MagicMock()
    doc.exists = True
    doc.to_dict.return_value = {"email": TEST_EMAIL, "verified": True}
    return doc


class TestSubscribeHappyPath:
    def test_valid_email_returns_ok(self):
        with (
            patch("functions.subscribe.main.get_hmac_secret", return_value=SECRET),
            patch("functions.subscribe.main.subscribers_col") as mock_col,
            patch("functions.subscribe.main.send_message"),
            patch("functions.subscribe.main.build_message", return_value=MagicMock()),
        ):
            doc_ref = MagicMock()
            doc_ref.get.return_value = _non_existing_doc()
            mock_col.return_value.document.return_value = doc_ref

            from functions.subscribe.main import subscribe
            req = _make_request(json_body={"email": TEST_EMAIL})
            body, status, headers = subscribe(req)

            assert status == 200
            assert body["ok"] is True

    def test_valid_email_creates_firestore_doc(self):
        with (
            patch("functions.subscribe.main.get_hmac_secret", return_value=SECRET),
            patch("functions.subscribe.main.subscribers_col") as mock_col,
            patch("functions.subscribe.main.send_message"),
            patch("functions.subscribe.main.build_message", return_value=MagicMock()),
        ):
            doc_ref = MagicMock()
            doc_ref.get.return_value = _non_existing_doc()
            mock_col.return_value.document.return_value = doc_ref

            from functions.subscribe.main import subscribe
            req = _make_request(json_body={"email": TEST_EMAIL})
            subscribe(req)

            # Doc created at correct hash path
            mock_col.return_value.document.assert_called_with(EMAIL_HASH)
            doc_ref.set.assert_called_once()
            call_args = doc_ref.set.call_args[0][0]
            assert call_args["email"] == TEST_EMAIL
            assert call_args["verified"] is False
            assert call_args["source"] == "signup_v1"
            assert "verify_token_hash" in call_args

    def test_valid_email_sends_confirmation(self):
        with (
            patch("functions.subscribe.main.get_hmac_secret", return_value=SECRET),
            patch("functions.subscribe.main.subscribers_col") as mock_col,
            patch("functions.subscribe.main.send_message") as mock_send,
            patch("functions.subscribe.main.build_message", return_value=MagicMock()) as mock_build,
        ):
            doc_ref = MagicMock()
            doc_ref.get.return_value = _non_existing_doc()
            mock_col.return_value.document.return_value = doc_ref

            from functions.subscribe.main import subscribe
            req = _make_request(json_body={"email": TEST_EMAIL})
            subscribe(req)

            mock_build.assert_called_once()
            mock_send.assert_called_once()
            # Subject contains "Confirm"
            call_kwargs = mock_build.call_args[1]
            assert "Confirm" in call_kwargs["subject"]
            assert TEST_EMAIL == call_kwargs["to_email"]

    def test_email_normalized_to_lowercase(self):
        with (
            patch("functions.subscribe.main.get_hmac_secret", return_value=SECRET),
            patch("functions.subscribe.main.subscribers_col") as mock_col,
            patch("functions.subscribe.main.send_message"),
            patch("functions.subscribe.main.build_message", return_value=MagicMock()),
        ):
            doc_ref = MagicMock()
            doc_ref.get.return_value = _non_existing_doc()
            mock_col.return_value.document.return_value = doc_ref

            from functions.subscribe.main import subscribe
            req = _make_request(json_body={"email": "STUDENT@PHYS.AU.DK"})
            subscribe(req)

            call_args = doc_ref.set.call_args[0][0]
            assert call_args["email"] == "student@phys.au.dk"


class TestSubscribeValidation:
    def test_missing_email_returns_400(self):
        from functions.subscribe.main import subscribe
        req = _make_request(json_body={})
        body, status, _ = subscribe(req)
        assert status == 400
        assert "error" in body

    def test_empty_email_returns_400(self):
        from functions.subscribe.main import subscribe
        req = _make_request(json_body={"email": ""})
        body, status, _ = subscribe(req)
        assert status == 400

    def test_no_at_sign_returns_400(self):
        from functions.subscribe.main import subscribe
        req = _make_request(json_body={"email": "notanemail"})
        body, status, _ = subscribe(req)
        assert status == 400

    def test_no_domain_returns_400(self):
        from functions.subscribe.main import subscribe
        req = _make_request(json_body={"email": "user@"})
        body, status, _ = subscribe(req)
        assert status == 400

    def test_too_long_email_returns_400(self):
        from functions.subscribe.main import subscribe
        long_email = "a" * 250 + "@example.com"
        req = _make_request(json_body={"email": long_email})
        body, status, _ = subscribe(req)
        assert status == 400


class TestSubscribeDuplicates:
    def test_already_verified_returns_ok_silently(self):
        with (
            patch("functions.subscribe.main.subscribers_col") as mock_col,
            patch("functions.subscribe.main.get_hmac_secret", return_value=SECRET),
            patch("functions.subscribe.main.send_message") as mock_send,
        ):
            doc_ref = MagicMock()
            doc_ref.get.return_value = _existing_verified_doc()
            mock_col.return_value.document.return_value = doc_ref

            from functions.subscribe.main import subscribe
            req = _make_request(json_body={"email": TEST_EMAIL})
            body, status, _ = subscribe(req)

            assert status == 200
            assert body["ok"] is True
            mock_send.assert_not_called()  # no email for already-verified

    def test_unverified_resend_returns_ok(self):
        with (
            patch("functions.subscribe.main.subscribers_col") as mock_col,
            patch("functions.subscribe.main.get_hmac_secret", return_value=SECRET),
            patch("functions.subscribe.main.send_message") as mock_send,
            patch("functions.subscribe.main.build_message", return_value=MagicMock()),
        ):
            doc_ref = MagicMock()
            doc_ref.get.return_value = _existing_unverified_doc()
            mock_col.return_value.document.return_value = doc_ref

            from functions.subscribe.main import subscribe
            req = _make_request(json_body={"email": TEST_EMAIL})
            body, status, _ = subscribe(req)

            assert status == 200
            assert body["ok"] is True
            mock_send.assert_called_once()  # confirmation email resent


class TestSubscribeErrorPaths:
    def test_firestore_write_failure_returns_500(self):
        with (
            patch("functions.subscribe.main.get_hmac_secret", return_value=SECRET),
            patch("functions.subscribe.main.subscribers_col") as mock_col,
        ):
            doc_ref = MagicMock()
            doc_ref.get.return_value = _non_existing_doc()
            doc_ref.set.side_effect = Exception("Firestore unavailable")
            mock_col.return_value.document.return_value = doc_ref

            from functions.subscribe.main import subscribe
            req = _make_request(json_body={"email": TEST_EMAIL})
            body, status, _ = subscribe(req)

            assert status == 500
            assert "error" in body

    def test_gmail_send_failure_returns_500(self):
        with (
            patch("functions.subscribe.main.get_hmac_secret", return_value=SECRET),
            patch("functions.subscribe.main.subscribers_col") as mock_col,
            patch("functions.subscribe.main.build_message", return_value=MagicMock()),
            patch("functions.subscribe.main.send_message", side_effect=Exception("Gmail down")),
        ):
            doc_ref = MagicMock()
            doc_ref.get.return_value = _non_existing_doc()
            mock_col.return_value.document.return_value = doc_ref

            from functions.subscribe.main import subscribe
            req = _make_request(json_body={"email": TEST_EMAIL})
            body, status, _ = subscribe(req)

            assert status == 500

    def test_options_preflight_returns_204(self):
        from functions.subscribe.main import subscribe
        req = _make_request(method="OPTIONS")
        _, status, headers = subscribe(req)
        assert status == 204
        assert "Access-Control-Allow-Origin" in headers

    def test_get_method_returns_405(self):
        from functions.subscribe.main import subscribe
        req = _make_request(method="GET")
        _, status, _ = subscribe(req)
        assert status == 405
