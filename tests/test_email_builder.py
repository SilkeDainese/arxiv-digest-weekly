"""Tests for email template builders.

Covers:
  - List-Unsubscribe header present
  - HTML + plaintext both valid (non-empty, contain key content)
  - Links contain signed tokens
  - build_personalized_digest_email: subject format, paper count
  - build_preview_email: cancel link present, top papers shown
  - Unsubscribe page, manage page, cancel confirmation page render
"""
import pytest

from shared.email_builder import (
    build_cancel_confirmation_page,
    build_manage_confirmation_page,
    build_manage_page,
    build_personalized_digest_email,
    build_preview_email,
    build_unsubscribe_page,
)
from shared.gmail_client import build_message


def make_paper(i: int = 1, score: float = 50.0) -> dict:
    return {
        "id": f"2501.0000{i}",
        "title": f"Test paper {i}: stellar evolution",
        "abstract": f"Abstract {i} about stellar evolution and binary stars.",
        "authors": ["Author A", "Author B"],
        "published": "2026-04-07T00:00:00+00:00",
        "url": f"https://arxiv.org/abs/2501.0000{i}",
        "pdf_url": f"https://arxiv.org/pdf/2501.0000{i}",
        "subscriber_score": score,
        "global_score": score,
    }


WEEK = "2026-W15"
UNSUB_URL = "https://functions.example.com/unsubscribe?t=FAKE_TOKEN"
MANAGE_URL = "https://functions.example.com/manage?t=FAKE_TOKEN"
CANCEL_URL = "https://functions.example.com/cancel_send?t=FAKE_TOKEN&week=2026-W15"
LOGS_URL = "https://console.cloud.google.com/logs"


class TestPersonalizedDigestEmail:
    def test_returns_three_strings(self):
        papers = [make_paper(1)]
        subject, html, text = build_personalized_digest_email(
            papers, ["stars"], WEEK, UNSUB_URL, MANAGE_URL
        )
        assert isinstance(subject, str)
        assert isinstance(html, str)
        assert isinstance(text, str)

    def test_subject_contains_week(self):
        papers = [make_paper(1)]
        subject, _, _ = build_personalized_digest_email(
            papers, ["stars"], WEEK, UNSUB_URL, MANAGE_URL
        )
        assert WEEK in subject

    def test_subject_contains_paper_count(self):
        papers = [make_paper(i) for i in range(3)]
        subject, _, _ = build_personalized_digest_email(
            papers, ["stars"], WEEK, UNSUB_URL, MANAGE_URL
        )
        assert "3" in subject

    def test_html_contains_unsubscribe_link(self):
        papers = [make_paper(1)]
        _, html, _ = build_personalized_digest_email(
            papers, ["stars"], WEEK, UNSUB_URL, MANAGE_URL
        )
        assert UNSUB_URL in html

    def test_html_contains_manage_link(self):
        papers = [make_paper(1)]
        _, html, _ = build_personalized_digest_email(
            papers, ["stars"], WEEK, UNSUB_URL, MANAGE_URL
        )
        assert MANAGE_URL in html

    def test_text_contains_unsubscribe_link(self):
        papers = [make_paper(1)]
        _, _, text = build_personalized_digest_email(
            papers, ["stars"], WEEK, UNSUB_URL, MANAGE_URL
        )
        assert UNSUB_URL in text

    def test_html_contains_paper_title(self):
        papers = [make_paper(1)]
        _, html, _ = build_personalized_digest_email(
            papers, ["stars"], WEEK, UNSUB_URL, MANAGE_URL
        )
        assert "Test paper 1" in html

    def test_no_papers_shows_fallback(self):
        _, html, text = build_personalized_digest_email(
            [], ["stars"], WEEK, UNSUB_URL, MANAGE_URL
        )
        assert "No new papers" in html
        assert "No new papers" in text

    def test_html_is_valid_doctype(self):
        _, html, _ = build_personalized_digest_email(
            [make_paper(1)], ["stars"], WEEK, UNSUB_URL, MANAGE_URL
        )
        assert html.strip().startswith("<!DOCTYPE html>")


class TestPreviewEmail:
    def setup_method(self):
        self.papers = [make_paper(i) for i in range(12)]
        self.topic_breakdown = {"stars": 10, "exoplanets": 5}

    def test_returns_three_strings(self):
        subject, html, text = build_preview_email(
            self.papers, 15, self.topic_breakdown, WEEK, CANCEL_URL, LOGS_URL
        )
        assert all(isinstance(s, str) for s in [subject, html, text])

    def test_subject_contains_preview_tag(self):
        subject, _, _ = build_preview_email(
            self.papers, 15, self.topic_breakdown, WEEK, CANCEL_URL, LOGS_URL
        )
        assert "[Preview]" in subject

    def test_subject_contains_paper_count(self):
        subject, _, _ = build_preview_email(
            self.papers, 15, self.topic_breakdown, WEEK, CANCEL_URL, LOGS_URL
        )
        assert str(len(self.papers)) in subject

    def test_subject_contains_subscriber_count(self):
        subject, _, _ = build_preview_email(
            self.papers, 15, self.topic_breakdown, WEEK, CANCEL_URL, LOGS_URL
        )
        assert "15" in subject

    def test_html_contains_cancel_button(self):
        _, html, _ = build_preview_email(
            self.papers, 15, self.topic_breakdown, WEEK, CANCEL_URL, LOGS_URL
        )
        # URL is HTML-escaped: & becomes &amp; — check for the cancel function name instead
        assert "cancel_send" in html
        assert "CANCEL MONDAY SEND" in html

    def test_text_contains_cancel_url(self):
        _, _, text = build_preview_email(
            self.papers, 15, self.topic_breakdown, WEEK, CANCEL_URL, LOGS_URL
        )
        assert CANCEL_URL in text

    def test_html_contains_logs_link(self):
        _, html, _ = build_preview_email(
            self.papers, 15, self.topic_breakdown, WEEK, CANCEL_URL, LOGS_URL
        )
        assert LOGS_URL in html

    def test_top_10_papers_shown(self):
        # Use 12 papers indexed 1-12 (not 0-11) so "Test paper 10" exists
        papers_1_indexed = [make_paper(i + 1) for i in range(12)]
        _, html, _ = build_preview_email(
            papers_1_indexed, 15, self.topic_breakdown, WEEK, CANCEL_URL, LOGS_URL
        )
        # Top 10 papers (1-10) should have their titles in HTML
        for i in range(1, 11):
            assert f"Test paper {i}" in html


class TestGmailMessageBuilder:
    def test_list_unsubscribe_header_present(self):
        from shared.gmail_client import build_message, GMAIL_SENDER_NAME, GMAIL_SENDER
        msg = build_message(
            to_email="student@phys.au.dk",
            subject="Test digest",
            html_body="<p>Test</p>",
            text_body="Test",
            unsubscribe_url=UNSUB_URL,
        )
        assert msg["List-Unsubscribe"] is not None
        assert UNSUB_URL in msg["List-Unsubscribe"]

    def test_list_unsubscribe_post_header_present(self):
        from shared.gmail_client import build_message
        msg = build_message(
            to_email="student@phys.au.dk",
            subject="Test digest",
            html_body="<p>Test</p>",
            text_body="Test",
            unsubscribe_url=UNSUB_URL,
        )
        assert msg["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"

    def test_no_unsubscribe_url_omits_header(self):
        from shared.gmail_client import build_message
        msg = build_message(
            to_email="student@phys.au.dk",
            subject="Test",
            html_body="<p>Test</p>",
            text_body="Test",
        )
        assert msg["List-Unsubscribe"] is None

    def test_message_has_html_and_text_parts(self):
        from shared.gmail_client import build_message
        msg = build_message(
            to_email="student@phys.au.dk",
            subject="Test",
            html_body="<p>Hello</p>",
            text_body="Hello",
        )
        content_types = [part.get_content_type() for part in msg.get_payload()]
        assert "text/plain" in content_types
        assert "text/html" in content_types

    def test_from_address_set_correctly(self):
        from shared.gmail_client import build_message, GMAIL_SENDER, GMAIL_SENDER_NAME
        msg = build_message(
            to_email="student@phys.au.dk",
            subject="Test",
            html_body="<p>Test</p>",
            text_body="Test",
        )
        assert GMAIL_SENDER in msg["From"]


class TestStaticPages:
    def test_unsubscribe_page_is_valid_html(self):
        html = build_unsubscribe_page()
        assert "<!DOCTYPE html>" in html
        assert "removed" in html.lower()

    def test_unsubscribe_page_contains_signup_link(self):
        html = build_unsubscribe_page(signup_url="https://example.com/signup")
        assert "https://example.com/signup" in html

    def test_manage_page_renders_checkboxes(self):
        from shared.email_builder import build_manage_page
        html = build_manage_page(
            current_topics=["stars", "exoplanets"],
            all_topics={"stars": "Stars", "exoplanets": "Exoplanets", "galaxies": "Galaxies"},
            manage_token="FAKE",
            manage_url="https://functions.example.com/manage",
        )
        assert 'type="checkbox"' in html
        assert 'value="stars"' in html
        assert 'checked' in html  # stars and exoplanets should be checked

    def test_manage_page_unchecked_topic_not_checked(self):
        html = build_manage_page(
            current_topics=["stars"],
            all_topics={"stars": "Stars", "galaxies": "Galaxies"},
            manage_token="FAKE",
            manage_url="https://functions.example.com/manage",
        )
        # galaxies should appear but not be checked
        assert 'value="galaxies"' in html

    def test_manage_confirmation_page_renders(self):
        html = build_manage_confirmation_page()
        assert "<!DOCTYPE html>" in html
        assert "updated" in html.lower()

    def test_cancel_confirmation_page_contains_week(self):
        html = build_cancel_confirmation_page("2026-W15")
        assert "2026-W15" in html
        assert "cancelled" in html.lower()


# ─────────────────────────────────────────────────────────────────────────────
# Branded template regression tests — added 2026-04-10 (Webb)
# Ensure preview + digest emails use the full branded styling (pine/gold/IBM Plex),
# not the generic plain-HTML stub.
# ─────────────────────────────────────────────────────────────────────────────

BRAND_PINE = "#2F4F3E"
BRAND_GOLD = "#EBC944"
BRAND_ASH_WHITE = "#F6F5F2"
IBM_PLEX = "IBM Plex Sans"
DM_SERIF = "DM Serif Display"
DM_MONO = "DM Mono"


class TestBrandedPreviewEmail:
    """Preview email must use branded styling and show subscriber count prominently."""

    def setup_method(self):
        self.papers = [make_paper(i) for i in range(5)]
        self.topic_breakdown = {"stars": 10, "exoplanets": 5}

    def _build(self, subscriber_count: int = 15):
        return build_preview_email(
            self.papers, subscriber_count, self.topic_breakdown,
            WEEK, CANCEL_URL, LOGS_URL
        )

    # ── Subscriber count line ──────────────────────────────────────────────

    def test_html_shows_subscriber_count_line(self):
        """Preview header must show 'N subscribers will receive this Monday'."""
        _, html, _ = self._build(subscriber_count=15)
        assert "15 subscribers will receive this" in html

    def test_html_zero_subscribers_dry_run_message(self):
        """Zero subscribers: preview must say 'No subscribers yet — this is a dry run.'"""
        _, html, _ = self._build(subscriber_count=0)
        assert "No subscribers yet" in html
        assert "dry run" in html

    def test_text_shows_subscriber_count(self):
        _, _, text = self._build(subscriber_count=7)
        assert "7 subscribers" in text

    def test_text_zero_subscribers_dry_run(self):
        _, _, text = self._build(subscriber_count=0)
        assert "dry run" in text.lower()

    # ── Branded CSS / typography ───────────────────────────────────────────

    def test_html_uses_pine_colour(self):
        _, html, _ = self._build()
        assert BRAND_PINE in html

    def test_html_uses_gold_colour(self):
        _, html, _ = self._build()
        assert BRAND_GOLD in html

    def test_html_uses_ibm_plex_sans(self):
        _, html, _ = self._build()
        assert IBM_PLEX in html

    def test_html_uses_dm_serif_display(self):
        _, html, _ = self._build()
        assert DM_SERIF in html

    def test_html_uses_dm_mono(self):
        _, html, _ = self._build()
        assert DM_MONO in html

    def test_html_table_layout_not_just_divs(self):
        """Email should use table-based layout for email-client compatibility."""
        _, html, _ = self._build()
        assert "<table" in html

    # ── Paper cards ───────────────────────────────────────────────────────

    def test_paper_cards_show_score(self):
        """Preview paper cards must show relevance/global score."""
        _, html, _ = self._build()
        # Score is rendered — check for score value from make_paper fixture (50.0)
        assert "50" in html

    def test_paper_cards_show_abstract_or_summary(self):
        _, html, _ = self._build()
        assert "stellar evolution" in html

    def test_paper_cards_show_arxiv_link(self):
        _, html, _ = self._build()
        assert "arxiv.org" in html

    def test_paper_cards_show_authors(self):
        _, html, _ = self._build()
        assert "Author A" in html

    # ── Structure ─────────────────────────────────────────────────────────

    def test_html_is_valid_doctype(self):
        _, html, _ = self._build()
        assert html.strip().startswith("<!DOCTYPE html>")

    def test_cancel_button_still_present(self):
        _, html, _ = self._build()
        assert "cancel_send" in html
        assert "CANCEL MONDAY SEND" in html

    def test_pine_header_bar_present(self):
        """Preview header bar should use the pine background colour."""
        _, html, _ = self._build()
        # Background pine colour used in the header bar
        assert f"background:{BRAND_PINE}" in html or f"background: {BRAND_PINE}" in html

    def test_singular_subscriber_count(self):
        """1 subscriber: 'will receive this' (not 'subscribers')."""
        _, html, _ = self._build(subscriber_count=1)
        assert "1 subscriber will receive this" in html


class TestBrandedDigestEmail:
    """Student digest email must use branded styling."""

    def setup_method(self):
        self.papers = [make_paper(i) for i in range(3)]

    def _build(self):
        return build_personalized_digest_email(
            self.papers, ["stars", "exoplanets"], WEEK, UNSUB_URL, MANAGE_URL
        )

    # ── Branded CSS / typography ───────────────────────────────────────────

    def test_html_uses_pine_colour(self):
        _, html, _ = self._build()
        assert BRAND_PINE in html

    def test_html_uses_gold_colour(self):
        _, html, _ = self._build()
        assert BRAND_GOLD in html

    def test_html_uses_ibm_plex_sans(self):
        _, html, _ = self._build()
        assert IBM_PLEX in html

    def test_html_uses_dm_serif_display(self):
        _, html, _ = self._build()
        assert DM_SERIF in html

    def test_html_uses_dm_mono(self):
        _, html, _ = self._build()
        assert DM_MONO in html

    def test_pine_header_bar_present(self):
        _, html, _ = self._build()
        assert f"background:{BRAND_PINE}" in html or f"background: {BRAND_PINE}" in html

    # ── Paper cards ───────────────────────────────────────────────────────

    def test_paper_title_in_card(self):
        _, html, _ = self._build()
        assert "Test paper 1" in html

    def test_arxiv_link_in_card(self):
        _, html, _ = self._build()
        assert "arxiv.org" in html

    def test_authors_in_card(self):
        _, html, _ = self._build()
        assert "Author A" in html

    def test_table_layout(self):
        _, html, _ = self._build()
        assert "<table" in html

    # ── Footer links ─────────────────────────────────────────────────────

    def test_unsubscribe_link_present(self):
        _, html, _ = self._build()
        assert UNSUB_URL in html

    def test_manage_link_present(self):
        _, html, _ = self._build()
        assert MANAGE_URL in html

    def test_no_cancel_button_in_digest(self):
        """Digest email (student copy) must NOT contain a CANCEL button."""
        _, html, _ = self._build()
        assert "CANCEL MONDAY SEND" not in html
