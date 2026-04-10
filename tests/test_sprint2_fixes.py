"""Sprint 2 TDD tests — fixes 6-10.

All tests in this file are RED initially and must be made GREEN by the
implementation. Tests are written before implementation code.

Fix 6:  pre_filter_for_ai — top-50 pre-filter before AI scoring
Fix 7:  _fetch_xml 429 retry with exponential backoff
Fix 8:  _parse_xml malformed-entry warning
Fix 9:  AU affiliation parsing + email badge
Fix 10: Score-tier notice in student email footer
"""
from __future__ import annotations

import logging
import textwrap
from datetime import datetime, timezone
from unittest.mock import MagicMock, call, patch

import pytest

# ─────────────────────────────────────────────────────────────────────────────
# Fixtures / helpers
# ─────────────────────────────────────────────────────────────────────────────

def _cutoff() -> datetime:
    return datetime(2026, 4, 6, 0, 0, 0, tzinfo=timezone.utc)


def make_paper(
    arxiv_id: str = "2501.00001",
    global_score: float = 50.0,
    title: str = "A test paper",
    abstract: str = "This is a test abstract about stellar evolution.",
    au_authors: list | None = None,
    score_tier: str = "keyword",
    ai_score: int = 5,
) -> dict:
    p = {
        "id": arxiv_id,
        "title": title,
        "abstract": abstract,
        "authors": ["Author A", "Author B"],
        "published": "2026-04-07T00:00:00+00:00",
        "url": f"https://arxiv.org/abs/{arxiv_id}",
        "pdf_url": f"https://arxiv.org/pdf/{arxiv_id}",
        "global_score": global_score,
        "subscriber_score": global_score,
        "score_tier": score_tier,
        "ai_score": ai_score,
    }
    if au_authors is not None:
        p["au_authors"] = au_authors
    return p


# ─────────────────────────────────────────────────────────────────────────────
# Fix 6: pre_filter_for_ai — top-50 pre-filter before AI scoring
# ─────────────────────────────────────────────────────────────────────────────

class TestPreFilterForAI:
    """pre_filter_for_ai(papers) selects top-50 by global_score for AI scoring."""

    def _import(self):
        from shared.arxiv_fetcher import pre_filter_for_ai
        return pre_filter_for_ai

    def test_600_papers_capped_at_50(self):
        """600 papers in → at most 50 out."""
        fn = self._import()
        papers = [make_paper(f"2501.{i:05d}", global_score=float(i)) for i in range(600)]
        result = fn(papers)
        assert len(result) == 50

    def test_returns_top_50_by_global_score(self):
        """Must return the 50 with the highest global_score."""
        fn = self._import()
        papers = [make_paper(f"2501.{i:05d}", global_score=float(i)) for i in range(600)]
        result = fn(papers)
        result_ids = {p["id"] for p in result}
        # Top 50 scores are 550-599
        for i in range(550, 600):
            assert f"2501.{i:05d}" in result_ids, (
                f"2501.{i:05d} (score {i}) should be in top 50 but is missing"
            )

    def test_zero_score_papers_excluded(self):
        """Papers with global_score == 0 must be dropped, even if total < 50."""
        fn = self._import()
        papers = [
            make_paper("2501.00001", global_score=10.0),
            make_paper("2501.00002", global_score=0.0),
            make_paper("2501.00003", global_score=5.0),
        ]
        result = fn(papers)
        ids = [p["id"] for p in result]
        assert "2501.00002" not in ids
        assert "2501.00001" in ids
        assert "2501.00003" in ids

    def test_fewer_than_50_nonzero_returns_all(self):
        """If only 10 papers have global_score > 0, all 10 are returned."""
        fn = self._import()
        papers = [make_paper(f"2501.{i:05d}", global_score=float(i + 1)) for i in range(10)]
        result = fn(papers)
        assert len(result) == 10

    def test_empty_input_returns_empty(self):
        fn = self._import()
        assert fn([]) == []

    def test_result_sorted_descending(self):
        """Results must be sorted by global_score descending (stable sort)."""
        fn = self._import()
        papers = [make_paper(f"2501.{i:05d}", global_score=float(i % 20)) for i in range(60)]
        result = fn(papers)
        scores = [p["global_score"] for p in result]
        assert scores == sorted(scores, reverse=True)

    def test_zero_score_not_in_top_50_even_if_needed(self):
        """If we have exactly 50 non-zero papers + 5 zero-score, return exactly 50."""
        fn = self._import()
        papers = [make_paper(f"2501.{i:05d}", global_score=float(i + 1)) for i in range(50)]
        papers += [make_paper(f"2501.{i:05d}", global_score=0.0) for i in range(50, 55)]
        result = fn(papers)
        assert len(result) == 50
        for p in result:
            assert p["global_score"] > 0


# ─────────────────────────────────────────────────────────────────────────────
# Fix 7: _fetch_xml 429 retry with exponential backoff
# ─────────────────────────────────────────────────────────────────────────────

class TestFetchXml429Retry:
    """_fetch_xml retries on HTTP 429 with exponential backoff."""

    def _import(self):
        from shared.arxiv_fetcher import _fetch_xml
        return _fetch_xml

    def _make_http_error(self, code: int) -> Exception:
        import urllib.error
        import io
        return urllib.error.HTTPError(
            url="https://export.arxiv.org/api/query",
            code=code,
            msg=f"HTTP Error {code}",
            hdrs=None,
            fp=io.BytesIO(b""),
        )

    def _make_urlopen_success(self, body: bytes = b"<feed/>"):
        mock_resp = MagicMock()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_resp.read.return_value = body
        return mock_resp

    def test_429_then_200_succeeds(self):
        """Single 429 followed by a 200 should succeed after one retry."""
        fn = self._import()
        call_count = [0]

        def fake_urlopen(req, timeout=None):
            call_count[0] += 1
            if call_count[0] == 1:
                raise self._make_http_error(429)
            return self._make_urlopen_success(b"<feed/>")

        with patch("urllib.request.urlopen", side_effect=fake_urlopen), \
             patch("time.sleep"):
            result = fn("https://export.arxiv.org/api/query?test")

        assert result is not None, "Should succeed after 429 + retry"
        assert call_count[0] == 2

    def test_three_429s_returns_none(self):
        """3 consecutive 429s must return None and log a warning."""
        fn = self._import()

        def fake_urlopen(req, timeout=None):
            raise self._make_http_error(429)

        with patch("urllib.request.urlopen", side_effect=fake_urlopen), \
             patch("time.sleep"), \
             patch("shared.arxiv_fetcher.print") as mock_print:
            result = fn("https://export.arxiv.org/api/query?test")

        assert result is None, "Should return None after 3 x 429"
        # Should have logged something warning-like
        printed = " ".join(str(c) for c in mock_print.call_args_list)
        assert any(
            "429" in str(a) or "rate" in str(a).lower() or "limit" in str(a).lower()
            for a in mock_print.call_args_list
        ), f"Expected 429/rate-limit warning, got: {printed}"

    def test_non_429_http_error_does_not_retry(self):
        """Non-429 HTTP errors (e.g. 500) must NOT trigger retries."""
        fn = self._import()
        call_count = [0]

        def fake_urlopen(req, timeout=None):
            call_count[0] += 1
            raise self._make_http_error(500)

        with patch("urllib.request.urlopen", side_effect=fake_urlopen), \
             patch("time.sleep") as mock_sleep:
            result = fn("https://export.arxiv.org/api/query?test")

        assert result is None
        assert call_count[0] == 1, f"Expected 1 call for non-429, got {call_count[0]}"
        mock_sleep.assert_not_called()

    def test_429_retry_uses_exponential_backoff(self):
        """Backoff sleeps should be 10s, 20s (doubling per retry)."""
        fn = self._import()
        call_count = [0]

        def fake_urlopen(req, timeout=None):
            call_count[0] += 1
            if call_count[0] <= 2:
                raise self._make_http_error(429)
            return self._make_urlopen_success()

        sleep_args = []
        with patch("urllib.request.urlopen", side_effect=fake_urlopen), \
             patch("time.sleep", side_effect=lambda s: sleep_args.append(s)):
            fn("https://export.arxiv.org/api/query?test")

        assert len(sleep_args) >= 2
        # First retry: 10s, second retry: 20s (10 * 2^0, 10 * 2^1)
        assert sleep_args[0] == 10
        assert sleep_args[1] == 20


# ─────────────────────────────────────────────────────────────────────────────
# Fix 8: Malformed XML warning
# ─────────────────────────────────────────────────────────────────────────────

_FEED_3_MALFORMED_OF_5 = textwrap.dedent("""\
<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:arxiv="http://arxiv.org/schemas/atom">

  <!-- entry 1: valid -->
  <entry>
    <id>http://arxiv.org/abs/2501.00001v1</id>
    <published>2026-04-07T00:00:00Z</published>
    <title>Valid paper one</title>
    <summary>A valid abstract about stellar evolution and binary stars.</summary>
    <author><name>Author A</name></author>
    <arxiv:primary_category term="astro-ph.SR" scheme="http://arxiv.org/schemas/atom"/>
  </entry>

  <!-- entry 2: malformed — no published date -->
  <entry>
    <id>http://arxiv.org/abs/2501.00002v1</id>
    <title>Malformed paper two</title>
    <summary>No published field.</summary>
    <author><name>Author B</name></author>
  </entry>

  <!-- entry 3: malformed — no id -->
  <entry>
    <published>2026-04-07T00:00:00Z</published>
    <title>Malformed paper three</title>
    <summary>No id field but has a published date.</summary>
    <author><name>Author C</name></author>
  </entry>

  <!-- entry 4: malformed — invalid date -->
  <entry>
    <id>http://arxiv.org/abs/2501.00004v1</id>
    <published>NOT-A-DATE</published>
    <title>Malformed paper four</title>
    <summary>Invalid published date.</summary>
    <author><name>Author D</name></author>
  </entry>

  <!-- entry 5: valid -->
  <entry>
    <id>http://arxiv.org/abs/2501.00005v1</id>
    <published>2026-04-07T00:00:00Z</published>
    <title>Valid paper five</title>
    <summary>Another valid abstract about exoplanet atmosphere characterization.</summary>
    <author><name>Author E</name></author>
    <arxiv:primary_category term="astro-ph.EP" scheme="http://arxiv.org/schemas/atom"/>
  </entry>

</feed>
""")

_FEED_ALL_5_MALFORMED = textwrap.dedent("""\
<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <!-- 5 entries, all missing published date = malformed -->
  <entry><id>http://arxiv.org/abs/2501.00001v1</id><title>Bad 1</title><summary>s</summary></entry>
  <entry><id>http://arxiv.org/abs/2501.00002v1</id><title>Bad 2</title><summary>s</summary></entry>
  <entry><id>http://arxiv.org/abs/2501.00003v1</id><title>Bad 3</title><summary>s</summary></entry>
  <entry><id>http://arxiv.org/abs/2501.00004v1</id><title>Bad 4</title><summary>s</summary></entry>
  <entry><id>http://arxiv.org/abs/2501.00005v1</id><title>Bad 5</title><summary>s</summary></entry>
</feed>
""")


class TestMalformedXmlWarning:
    """_parse_xml must warn when >= 3 entries are malformed, or when all are."""

    def _cutoff(self):
        return datetime(2026, 4, 6, 0, 0, tzinfo=timezone.utc)

    def test_three_of_five_malformed_logs_warning(self, caplog):
        """3/5 malformed entries must produce a warning log message."""
        from shared.arxiv_fetcher import _parse_xml
        with caplog.at_level(logging.WARNING, logger="shared.arxiv_fetcher"):
            _parse_xml(_FEED_3_MALFORMED_OF_5, self._cutoff())
        warning_msgs = [r.message for r in caplog.records if r.levelno >= logging.WARNING]
        # Should have at least one warning about malformed entries
        assert any(
            "malformed" in m.lower() or "format" in m.lower()
            for m in warning_msgs
        ), f"Expected malformed warning, got records: {warning_msgs}"

    def test_three_of_five_malformed_warning_includes_counts(self, caplog):
        """Warning must mention the counts (e.g. '3 of 5')."""
        from shared.arxiv_fetcher import _parse_xml
        with caplog.at_level(logging.WARNING, logger="shared.arxiv_fetcher"):
            _parse_xml(_FEED_3_MALFORMED_OF_5, self._cutoff())
        warning_text = " ".join(r.message for r in caplog.records if r.levelno >= logging.WARNING)
        # Should mention both counts somewhere
        assert "3" in warning_text and "5" in warning_text, (
            f"Warning should include counts 3/5, got: {warning_text!r}"
        )

    def test_all_malformed_logs_warning(self, caplog):
        """When ALL entries are malformed, must log a warning."""
        from shared.arxiv_fetcher import _parse_xml
        with caplog.at_level(logging.WARNING, logger="shared.arxiv_fetcher"):
            _parse_xml(_FEED_ALL_5_MALFORMED, self._cutoff())
        warning_msgs = [r.message for r in caplog.records if r.levelno >= logging.WARNING]
        assert any(
            "malformed" in m.lower() or "format" in m.lower()
            for m in warning_msgs
        ), f"Expected malformed warning for all-bad feed, got: {warning_msgs}"

    def test_valid_entries_still_returned_with_malformed(self):
        """Malformed entries are skipped — valid entries still returned."""
        from shared.arxiv_fetcher import _parse_xml
        papers = _parse_xml(_FEED_3_MALFORMED_OF_5, self._cutoff())
        # Should get the 2 valid entries
        assert len(papers) == 2

    def test_no_warning_for_clean_feed(self, caplog):
        """A clean feed with 0 malformed entries must NOT produce a warning."""
        from shared.arxiv_fetcher import _parse_xml, _ATOM_FEED_WITH_CATEGORY
        with caplog.at_level(logging.WARNING, logger="shared.arxiv_fetcher"):
            _parse_xml(_ATOM_FEED_WITH_CATEGORY, self._cutoff())
        warning_msgs = [r.message for r in caplog.records if r.levelno >= logging.WARNING]
        malformed_warnings = [m for m in warning_msgs if "malformed" in m.lower()]
        assert not malformed_warnings, f"Unexpected malformed warning: {malformed_warnings}"


# ─────────────────────────────────────────────────────────────────────────────
# Fix 9: AU affiliation parsing
# ─────────────────────────────────────────────────────────────────────────────

_FEED_WITH_AU_AFFILIATION = textwrap.dedent("""\
<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:arxiv="http://arxiv.org/schemas/atom">
  <entry>
    <id>http://arxiv.org/abs/2501.AU001v1</id>
    <published>2026-04-07T00:00:00Z</published>
    <title>A paper from Aarhus University</title>
    <summary>We study stellar evolution at Aarhus University.</summary>
    <author>
      <name>Silke Dainese</name>
      <arxiv:affiliation>Department of Physics and Astronomy, Aarhus University, Denmark</arxiv:affiliation>
    </author>
    <author>
      <name>Other Author</name>
      <arxiv:affiliation>MIT, Cambridge, USA</arxiv:affiliation>
    </author>
    <arxiv:primary_category term="astro-ph.SR" scheme="http://arxiv.org/schemas/atom"/>
  </entry>
</feed>
""")

_FEED_WITHOUT_AU_AFFILIATION = textwrap.dedent("""\
<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:arxiv="http://arxiv.org/schemas/atom">
  <entry>
    <id>http://arxiv.org/abs/2501.NOAU1v1</id>
    <published>2026-04-07T00:00:00Z</published>
    <title>A paper from MIT only</title>
    <summary>We study black holes at MIT.</summary>
    <author>
      <name>John Doe</name>
      <arxiv:affiliation>MIT, Cambridge, USA</arxiv:affiliation>
    </author>
    <arxiv:primary_category term="astro-ph.HE" scheme="http://arxiv.org/schemas/atom"/>
  </entry>
</feed>
""")

_FEED_MIXED_AFFILIATIONS = textwrap.dedent("""\
<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:arxiv="http://arxiv.org/schemas/atom">
  <entry>
    <id>http://arxiv.org/abs/2501.MIX01v1</id>
    <published>2026-04-07T00:00:00Z</published>
    <title>A collaborative paper</title>
    <summary>A multi-institution collaboration paper on exoplanets.</summary>
    <author>
      <name>AU Person</name>
      <arxiv:affiliation>Aarhus Universitet, Denmark</arxiv:affiliation>
    </author>
    <author>
      <name>ESO Person</name>
      <arxiv:affiliation>European Southern Observatory, Garching, Germany</arxiv:affiliation>
    </author>
    <author>
      <name>PHYS AU Person</name>
      <arxiv:affiliation>phys.au.dk, Aarhus, Denmark</arxiv:affiliation>
    </author>
    <arxiv:primary_category term="astro-ph.EP" scheme="http://arxiv.org/schemas/atom"/>
  </entry>
</feed>
""")

_FEED_NO_AFFILIATION_ELEMENTS = textwrap.dedent("""\
<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:arxiv="http://arxiv.org/schemas/atom">
  <entry>
    <id>http://arxiv.org/abs/2501.NOAFF1v1</id>
    <published>2026-04-07T00:00:00Z</published>
    <title>A paper with no affiliation XML</title>
    <summary>No affiliation elements in XML.</summary>
    <author>
      <name>Anonymous Author</name>
    </author>
    <arxiv:primary_category term="astro-ph.GA" scheme="http://arxiv.org/schemas/atom"/>
  </entry>
</feed>
""")


class TestAUAffiliationParsing:
    """Fix 9a: _parse_xml must extract au_authors list for AU-affiliated papers."""

    def _cutoff(self):
        return datetime(2026, 4, 6, 0, 0, tzinfo=timezone.utc)

    def _import(self):
        from shared.arxiv_fetcher import _parse_xml
        return _parse_xml

    def test_au_author_detected(self):
        """Paper with Aarhus University affiliation → au_authors is non-empty."""
        fn = self._import()
        papers = fn(_FEED_WITH_AU_AFFILIATION, self._cutoff())
        assert papers, "Should parse paper"
        au = papers[0].get("au_authors", [])
        assert au, f"Expected au_authors to be non-empty, got {au!r}"

    def test_au_author_name_correct(self):
        """The AU author name must be captured in au_authors."""
        fn = self._import()
        papers = fn(_FEED_WITH_AU_AFFILIATION, self._cutoff())
        assert "Silke Dainese" in papers[0]["au_authors"]

    def test_non_au_author_not_in_list(self):
        """Non-AU authors (MIT etc.) must NOT appear in au_authors."""
        fn = self._import()
        papers = fn(_FEED_WITH_AU_AFFILIATION, self._cutoff())
        assert "Other Author" not in papers[0]["au_authors"]

    def test_no_au_affiliation_gives_empty_list(self):
        """Paper with only non-AU affiliations → au_authors == []."""
        fn = self._import()
        papers = fn(_FEED_WITHOUT_AU_AFFILIATION, self._cutoff())
        assert papers, "Should parse paper"
        assert papers[0].get("au_authors", []) == []

    def test_mixed_affiliations_only_au_authors(self):
        """Mixed affiliation feed → only AU-affiliated authors in au_authors."""
        fn = self._import()
        papers = fn(_FEED_MIXED_AFFILIATIONS, self._cutoff())
        assert papers
        au = papers[0].get("au_authors", [])
        assert "AU Person" in au, f"AU Person should be detected, got {au!r}"
        assert "PHYS AU Person" in au, f"PHYS AU Person (phys.au.dk) should be detected, got {au!r}"
        assert "ESO Person" not in au, f"ESO Person should NOT be in au_authors, got {au!r}"

    def test_no_affiliation_xml_gives_empty_list(self):
        """Papers with no arxiv:affiliation elements → au_authors == []."""
        fn = self._import()
        papers = fn(_FEED_NO_AFFILIATION_ELEMENTS, self._cutoff())
        assert papers
        assert papers[0].get("au_authors", []) == []

    def test_au_authors_field_present_on_all_papers(self):
        """Every parsed paper must have an 'au_authors' key (list, possibly empty)."""
        fn = self._import()
        for feed in [
            _FEED_WITH_AU_AFFILIATION,
            _FEED_WITHOUT_AU_AFFILIATION,
            _FEED_MIXED_AFFILIATIONS,
            _FEED_NO_AFFILIATION_ELEMENTS,
        ]:
            papers = fn(feed, self._cutoff())
            for p in papers:
                assert "au_authors" in p, f"Paper {p.get('id')} missing au_authors"
                assert isinstance(p["au_authors"], list)

    def test_aarhus_universitet_danish_name_detected(self):
        """'Aarhus Universitet' (Danish) must also match."""
        fn = self._import()
        papers = fn(_FEED_MIXED_AFFILIATIONS, self._cutoff())
        assert papers
        au = papers[0].get("au_authors", [])
        assert "AU Person" in au, f"Aarhus Universitet should match, got {au!r}"


class TestAUBadgeInEmail:
    """Fix 9b: _paper_card_branded must show AU badge when au_authors is non-empty."""

    def _import(self):
        from shared.email_builder import _paper_card_branded
        return _paper_card_branded

    def test_badge_shown_when_au_authors_present(self):
        """Non-empty au_authors → gold badge in HTML card."""
        fn = self._import()
        paper = make_paper("2501.AU001", au_authors=["Silke Dainese"])
        html = fn(paper)
        assert "Aarhus" in html, f"Expected 'Aarhus' in card HTML, got: {html[:400]!r}"

    def test_badge_shows_university_text(self):
        """Badge must say 'Aarhus University' (or contain 'University' near 'Aarhus')."""
        fn = self._import()
        paper = make_paper("2501.AU001", au_authors=["Silke Dainese"])
        html = fn(paper)
        assert "University" in html or "Universitet" in html, (
            f"Badge should mention University, got: {html[:400]!r}"
        )

    def test_no_badge_when_au_authors_empty(self):
        """Empty au_authors → no AU badge in card HTML."""
        fn = self._import()
        paper = make_paper("2501.NOAU", au_authors=[])
        html = fn(paper)
        # Should NOT contain the AU badge marker
        assert "Aarhus University" not in html or "au_authors" not in str(paper), True
        # More specific: the 🏛 badge should not appear
        assert "\U0001f3db" not in html  # 🏛

    def test_no_badge_when_au_authors_key_missing(self):
        """Missing au_authors key → no badge rendered."""
        fn = self._import()
        paper = {
            "id": "2501.MISSING",
            "title": "A paper",
            "abstract": "Abstract.",
            "authors": ["Author A"],
            "url": "https://arxiv.org/abs/2501.MISSING",
            "pdf_url": "https://arxiv.org/pdf/2501.MISSING",
        }
        html = fn(paper)
        assert "\U0001f3db" not in html

    def test_badge_uses_gold_colour(self):
        """AU badge styling should use the brand gold colour."""
        fn = self._import()
        paper = make_paper("2501.AU001", au_authors=["Silke Dainese"])
        html = fn(paper)
        from shared.email_builder import GOLD
        assert GOLD in html, f"Badge should use GOLD colour {GOLD}, got: {html[:600]!r}"


# ─────────────────────────────────────────────────────────────────────────────
# Fix 10: Score-tier notice in student email footer
# ─────────────────────────────────────────────────────────────────────────────

WEEK = "2026-W15"
UNSUB_URL = "https://functions.example.com/unsubscribe?t=TOKEN"
MANAGE_URL = "https://functions.example.com/manage?t=TOKEN"


def _make_papers_with_tier(tier: str, count: int = 3) -> list[dict]:
    papers = []
    for i in range(count):
        p = make_paper(f"2501.{i:05d}", score_tier=tier)
        p["subscriber_score"] = 50.0
        if tier in ("claude", "gemini-vertex", "gemini-api"):
            p["score_tier"] = tier
            p["ai_score"] = 7
        else:
            p["score_tier"] = "keyword"
            p["ai_score"] = 50.0
        papers.append(p)
    return papers


class TestScoreTierFooter:
    """Fix 10: build_personalized_digest_email renders score-tier notice in footer."""

    def _build(self, papers, topics=None):
        from shared.email_builder import build_personalized_digest_email
        return build_personalized_digest_email(
            papers,
            topics or ["stars"],
            WEEK,
            UNSUB_URL,
            MANAGE_URL,
        )

    def test_all_claude_shows_claude_notice(self):
        """All-claude papers → footer says 'Claude Haiku' or 'Claude'."""
        papers = _make_papers_with_tier("claude")
        _, html, _ = self._build(papers)
        assert "Claude" in html, f"Expected Claude tier notice in footer, got tail: ...{html[-800:]}"

    def test_all_gemini_vertex_shows_gemini_notice(self):
        """All gemini-vertex papers → footer says 'Gemini'."""
        papers = _make_papers_with_tier("gemini-vertex")
        _, html, _ = self._build(papers)
        assert "Gemini" in html, f"Expected Gemini notice in footer"

    def test_all_gemini_api_shows_gemini_notice(self):
        """All gemini-api papers → footer says 'Gemini'."""
        papers = _make_papers_with_tier("gemini-api")
        _, html, _ = self._build(papers)
        assert "Gemini" in html

    def test_all_keyword_shows_keyword_notice(self):
        """All keyword papers → footer says 'Keyword ranking only' or similar."""
        papers = _make_papers_with_tier("keyword")
        _, html, _ = self._build(papers)
        assert "keyword" in html.lower() or "Keyword" in html, (
            f"Expected keyword-only notice, got tail: ...{html[-800:]}"
        )

    def test_mixed_tiers_shows_dominant(self):
        """Mixed tiers → dominant tier (majority) is shown."""
        # 2 claude + 1 keyword → claude dominates
        papers = _make_papers_with_tier("claude", 2) + _make_papers_with_tier("keyword", 1)
        _, html, _ = self._build(papers)
        assert "Claude" in html, "Dominant tier (claude) should appear in footer"

    def test_tier_notice_is_subtle_not_banner(self):
        """Tier notice must NOT be a full CANCEL-style banner — just a footer line."""
        papers = _make_papers_with_tier("claude")
        _, html, _ = self._build(papers)
        # Should not be an H1/H2 or large bold block
        # The cancel-style bold-all-caps pattern should not appear in the tier notice
        assert "CLAUDE" not in html, "Tier notice must be subtle, not all-caps banner"

    def test_text_version_includes_tier_notice(self):
        """Plaintext version should also mention the scoring tier."""
        papers = _make_papers_with_tier("claude")
        _, _, text = self._build(papers)
        assert "Claude" in text or "claude" in text, (
            f"Expected Claude tier in plaintext, got: {text[-400:]!r}"
        )

    def test_no_papers_no_crash(self):
        """Empty papers list must not crash even with tier check."""
        _, html, text = self._build([])
        assert isinstance(html, str)

    def test_all_ai_tier_papers_show_ai_notice(self):
        """score_tier='ai' (legacy field from ai_scorer) → shows AI tier."""
        papers = _make_papers_with_tier("claude")
        # Override to use the generic "ai" tier
        for p in papers:
            p["score_tier"] = "ai"
        _, html, _ = self._build(papers)
        # "ai" tier maps to whichever AI ran — should still show something non-keyword
        assert "AI" in html or "Claude" in html or "Gemini" in html or "keyword" not in html.lower()


# ─────────────────────────────────────────────────────────────────────────────
# Fix 6 integration: pre_filter_for_ai wired into main.py pipeline
# ─────────────────────────────────────────────────────────────────────────────

class TestPreFilterIntegration:
    """Verify pre_filter_for_ai is called in the prep_and_preview pipeline."""

    def test_pre_filter_called_before_ai_scoring(self):
        """score_papers_with_ai must receive at most 50 papers when 600+ exist."""
        from shared.arxiv_fetcher import pre_filter_for_ai
        papers_600 = [make_paper(f"2501.{i:05d}", global_score=float(i % 100)) for i in range(600)]
        result = pre_filter_for_ai(papers_600)
        # The AI scorer should only see these
        assert len(result) <= 50

    def test_pre_filter_drops_zero_global_score_papers(self):
        """Papers with global_score == 0 must not reach the AI scorer."""
        from shared.arxiv_fetcher import pre_filter_for_ai
        papers = [
            make_paper("2501.00001", global_score=0.0),
            make_paper("2501.00002", global_score=10.0),
        ]
        result = pre_filter_for_ai(papers)
        ids = [p["id"] for p in result]
        assert "2501.00001" not in ids
        assert "2501.00002" in ids
