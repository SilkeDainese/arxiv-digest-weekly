"""Tests for the subscribe Cloud Function.

Covers:
  - Valid email + topics + max_papers → doc created (verified:True), welcome email sent, 200 ok
  - max_papers defaults to 6 when omitted
  - max_papers is clamped to 3–15
  - Invalid email format → 400 error
  - Missing email → 400 error
  - Missing topics → 400 error
  - Invalid topic ID → 400 error
  - Duplicate signup (existing doc) → silent 200 ok (no email re-sent)
  - Firestore write failure → 500 error
  - Gmail send failure → 200 ok (non-fatal; doc already written)
  - CORS preflight → 204 no content
  - Wrong HTTP method → 405
"""
from __future__ import annotations

import hashlib
from unittest.mock import MagicMock, patch

import pytest

SECRET = "test-hmac-secret-32bytes-padded!!"
TEST_EMAIL = "student@phys.au.dk"
EMAIL_HASH = hashlib.sha256(TEST_EMAIL.encode()).hexdigest()

VALID_TOPICS = ["stars"]
MULTI_TOPICS = ["stars", "exoplanets"]


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


def _existing_doc():
    doc = MagicMock()
    doc.exists = True
    doc.to_dict.return_value = {"email": TEST_EMAIL, "verified": True}
    return doc


class TestSubscribeHappyPath:
    def test_valid_request_returns_ok(self):
        with (
            patch("functions.subscribe.main.subscribers_col") as mock_col,
            patch("functions.subscribe.main.send_message"),
            patch("functions.subscribe.main.build_message", return_value=MagicMock()),
        ):
            doc_ref = MagicMock()
            doc_ref.get.return_value = _non_existing_doc()
            mock_col.return_value.document.return_value = doc_ref

            from functions.subscribe.main import subscribe
            req = _make_request(json_body={"email": TEST_EMAIL, "topics": VALID_TOPICS})
            body, status, headers = subscribe(req)

            assert status == 200
            assert body["ok"] is True

    def test_creates_verified_firestore_doc(self):
        with (
            patch("functions.subscribe.main.subscribers_col") as mock_col,
            patch("functions.subscribe.main.send_message"),
            patch("functions.subscribe.main.build_message", return_value=MagicMock()),
        ):
            doc_ref = MagicMock()
            doc_ref.get.return_value = _non_existing_doc()
            mock_col.return_value.document.return_value = doc_ref

            from functions.subscribe.main import subscribe
            req = _make_request(json_body={"email": TEST_EMAIL, "topics": MULTI_TOPICS, "max_papers": 8})
            subscribe(req)

            mock_col.return_value.document.assert_called_with(EMAIL_HASH)
            doc_ref.set.assert_called_once()
            call_args = doc_ref.set.call_args[0][0]
            assert call_args["email"] == TEST_EMAIL
            assert call_args["topics"] == MULTI_TOPICS
            assert call_args["verified"] is True       # no double opt-in
            assert call_args["max_papers"] == 8
            assert call_args["source"] == "signup_v1"
            assert "verify_token_hash" not in call_args

    def test_max_papers_defaults_to_6(self):
        with (
            patch("functions.subscribe.main.subscribers_col") as mock_col,
            patch("functions.subscribe.main.send_message"),
            patch("functions.subscribe.main.build_message", return_value=MagicMock()),
        ):
            doc_ref = MagicMock()
            doc_ref.get.return_value = _non_existing_doc()
            mock_col.return_value.document.return_value = doc_ref

            from functions.subscribe.main import subscribe
            req = _make_request(json_body={"email": TEST_EMAIL, "topics": VALID_TOPICS})
            subscribe(req)

            call_args = doc_ref.set.call_args[0][0]
            assert call_args["max_papers"] == 6

    def test_max_papers_clamped_to_minimum(self):
        with (
            patch("functions.subscribe.main.subscribers_col") as mock_col,
            patch("functions.subscribe.main.send_message"),
            patch("functions.subscribe.main.build_message", return_value=MagicMock()),
        ):
            doc_ref = MagicMock()
            doc_ref.get.return_value = _non_existing_doc()
            mock_col.return_value.document.return_value = doc_ref

            from functions.subscribe.main import subscribe
            req = _make_request(json_body={"email": TEST_EMAIL, "topics": VALID_TOPICS, "max_papers": 1})
            subscribe(req)

            call_args = doc_ref.set.call_args[0][0]
            assert call_args["max_papers"] == 3

    def test_max_papers_clamped_to_maximum(self):
        with (
            patch("functions.subscribe.main.subscribers_col") as mock_col,
            patch("functions.subscribe.main.send_message"),
            patch("functions.subscribe.main.build_message", return_value=MagicMock()),
        ):
            doc_ref = MagicMock()
            doc_ref.get.return_value = _non_existing_doc()
            mock_col.return_value.document.return_value = doc_ref

            from functions.subscribe.main import subscribe
            req = _make_request(json_body={"email": TEST_EMAIL, "topics": VALID_TOPICS, "max_papers": 99})
            subscribe(req)

            call_args = doc_ref.set.call_args[0][0]
            assert call_args["max_papers"] == 15

    def test_sends_welcome_email(self):
        with (
            patch("functions.subscribe.main.subscribers_col") as mock_col,
            patch("functions.subscribe.main.send_message") as mock_send,
            patch("functions.subscribe.main.build_message", return_value=MagicMock()) as mock_build,
        ):
            doc_ref = MagicMock()
            doc_ref.get.return_value = _non_existing_doc()
            mock_col.return_value.document.return_value = doc_ref

            from functions.subscribe.main import subscribe
            req = _make_request(json_body={"email": TEST_EMAIL, "topics": VALID_TOPICS})
            subscribe(req)

            mock_build.assert_called_once()
            mock_send.assert_called_once()
            call_kwargs = mock_build.call_args[1]
            assert "Confirm" not in call_kwargs["subject"]   # welcome, not verify
            assert "subscribed" in call_kwargs["subject"].lower()
            assert TEST_EMAIL == call_kwargs["to_email"]

    def test_email_normalized_to_lowercase(self):
        with (
            patch("functions.subscribe.main.subscribers_col") as mock_col,
            patch("functions.subscribe.main.send_message"),
            patch("functions.subscribe.main.build_message", return_value=MagicMock()),
        ):
            doc_ref = MagicMock()
            doc_ref.get.return_value = _non_existing_doc()
            mock_col.return_value.document.return_value = doc_ref

            from functions.subscribe.main import subscribe
            req = _make_request(json_body={"email": "STUDENT@PHYS.AU.DK", "topics": VALID_TOPICS})
            subscribe(req)

            call_args = doc_ref.set.call_args[0][0]
            assert call_args["email"] == "student@phys.au.dk"


class TestSubscribeValidation:
    def test_missing_email_returns_400(self):
        from functions.subscribe.main import subscribe
        req = _make_request(json_body={"topics": VALID_TOPICS})
        body, status, _ = subscribe(req)
        assert status == 400
        assert "error" in body

    def test_empty_email_returns_400(self):
        from functions.subscribe.main import subscribe
        req = _make_request(json_body={"email": "", "topics": VALID_TOPICS})
        body, status, _ = subscribe(req)
        assert status == 400

    def test_no_at_sign_returns_400(self):
        from functions.subscribe.main import subscribe
        req = _make_request(json_body={"email": "notanemail", "topics": VALID_TOPICS})
        body, status, _ = subscribe(req)
        assert status == 400

    def test_no_domain_returns_400(self):
        from functions.subscribe.main import subscribe
        req = _make_request(json_body={"email": "user@", "topics": VALID_TOPICS})
        body, status, _ = subscribe(req)
        assert status == 400

    def test_too_long_email_returns_400(self):
        from functions.subscribe.main import subscribe
        long_email = "a" * 250 + "@example.com"
        req = _make_request(json_body={"email": long_email, "topics": VALID_TOPICS})
        body, status, _ = subscribe(req)
        assert status == 400


class TestSubscribeTopicsValidation:
    def test_missing_topics_returns_400(self):
        from functions.subscribe.main import subscribe
        req = _make_request(json_body={"email": TEST_EMAIL})
        body, status, _ = subscribe(req)
        assert status == 400
        assert "error" in body

    def test_empty_topics_list_returns_400(self):
        from functions.subscribe.main import subscribe
        req = _make_request(json_body={"email": TEST_EMAIL, "topics": []})
        body, status, _ = subscribe(req)
        assert status == 400
        assert "error" in body

    def test_invalid_topic_id_returns_400(self):
        from functions.subscribe.main import subscribe
        req = _make_request(json_body={"email": TEST_EMAIL, "topics": ["not_a_real_topic"]})
        body, status, _ = subscribe(req)
        assert status == 400
        assert "error" in body

    def test_partially_invalid_topics_returns_400(self):
        from functions.subscribe.main import subscribe
        req = _make_request(json_body={"email": TEST_EMAIL, "topics": ["stars", "bogus"]})
        body, status, _ = subscribe(req)
        assert status == 400
        assert "error" in body

    def test_all_8_valid_topics_accepted(self):
        all_topics = [
            "stars", "exoplanets", "galaxies", "cosmology",
            "high_energy", "instrumentation", "solar_helio", "methods_ml",
        ]
        with (
            patch("functions.subscribe.main.subscribers_col") as mock_col,
            patch("functions.subscribe.main.send_message"),
            patch("functions.subscribe.main.build_message", return_value=MagicMock()),
        ):
            doc_ref = MagicMock()
            doc_ref.get.return_value = _non_existing_doc()
            mock_col.return_value.document.return_value = doc_ref

            from functions.subscribe.main import subscribe
            req = _make_request(json_body={"email": TEST_EMAIL, "topics": all_topics})
            body, status, _ = subscribe(req)

            assert status == 200
            call_args = doc_ref.set.call_args[0][0]
            assert call_args["topics"] == all_topics

    def test_topics_and_max_papers_stored(self):
        with (
            patch("functions.subscribe.main.subscribers_col") as mock_col,
            patch("functions.subscribe.main.send_message"),
            patch("functions.subscribe.main.build_message", return_value=MagicMock()),
        ):
            doc_ref = MagicMock()
            doc_ref.get.return_value = _non_existing_doc()
            mock_col.return_value.document.return_value = doc_ref

            from functions.subscribe.main import subscribe
            req = _make_request(json_body={"email": TEST_EMAIL, "topics": ["stars", "exoplanets"], "max_papers": 10})
            subscribe(req)

            call_args = doc_ref.set.call_args[0][0]
            assert call_args["topics"] == ["stars", "exoplanets"]
            assert call_args["max_papers"] == 10


class TestSubscribeDuplicates:
    def test_existing_signup_returns_ok_silently(self):
        with (
            patch("functions.subscribe.main.subscribers_col") as mock_col,
            patch("functions.subscribe.main.send_message") as mock_send,
        ):
            doc_ref = MagicMock()
            doc_ref.get.return_value = _existing_doc()
            mock_col.return_value.document.return_value = doc_ref

            from functions.subscribe.main import subscribe
            req = _make_request(json_body={"email": TEST_EMAIL, "topics": VALID_TOPICS})
            body, status, _ = subscribe(req)

            assert status == 200
            assert body["ok"] is True
            mock_send.assert_not_called()


class TestSubscribeErrorPaths:
    def test_firestore_write_failure_returns_500(self):
        with (
            patch("functions.subscribe.main.subscribers_col") as mock_col,
        ):
            doc_ref = MagicMock()
            doc_ref.get.return_value = _non_existing_doc()
            doc_ref.set.side_effect = Exception("Firestore unavailable")
            mock_col.return_value.document.return_value = doc_ref

            from functions.subscribe.main import subscribe
            req = _make_request(json_body={"email": TEST_EMAIL, "topics": VALID_TOPICS})
            body, status, _ = subscribe(req)

            assert status == 500
            assert "error" in body

    def test_gmail_send_failure_returns_200(self):
        """Welcome email failure is non-fatal — doc is written, user sees success."""
        with (
            patch("functions.subscribe.main.subscribers_col") as mock_col,
            patch("functions.subscribe.main.build_message", return_value=MagicMock()),
            patch("functions.subscribe.main.send_message", side_effect=Exception("Gmail down")),
        ):
            doc_ref = MagicMock()
            doc_ref.get.return_value = _non_existing_doc()
            mock_col.return_value.document.return_value = doc_ref

            from functions.subscribe.main import subscribe
            req = _make_request(json_body={"email": TEST_EMAIL, "topics": VALID_TOPICS})
            body, status, _ = subscribe(req)

            assert status == 200
            assert body["ok"] is True

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
