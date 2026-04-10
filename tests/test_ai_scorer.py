"""Tests for the AI scoring cascade and supporting utilities.

TDD — written before implementation. All tests in this file are expected
to fail until shared/ai_scorer.py exists with the right behaviour.

Coverage:
  - _strip_latex: math blocks, inline math, common commands
  - _short_title: LaTeX stripping, char-cap, word-boundary truncation
  - _one_sentence: first sentence extraction, length cap, LaTeX cleaning
  - AI scoring cascade: Claude → Vertex Gemini → Gemini API → keyword fallback
  - plain_summary: present, length, banned-phrase check
  - highlight_phrase: word count 5–8, no trailing punctuation
  - score_tier field: "ai" or "keyword" on every scored paper
  - AU affiliation badge: is_au_researcher flag
  - Integration: build_personalized_digest_email renders plain_summary, not raw abstract
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from shared.ai_scorer import (
    _one_sentence,
    _short_title,
    _strip_latex,
    score_papers_with_ai,
)
from shared.email_builder import build_personalized_digest_email

WEEK = "2026-W15"
UNSUB_URL = "https://functions.example.com/unsubscribe?t=TOKEN"
MANAGE_URL = "https://functions.example.com/manage?t=TOKEN"

BANNED_STARTERS = [
    "Researchers",
    "The authors",
    "This paper",
    "A team",
    "Scientists",
    "The researchers",
    "Authors",
]


def make_paper(
    i: int = 1,
    title: str = "Stellar evolution in binary stars",
    abstract: str = "We present a new method for measuring stellar radii using interferometry. "
    "Our results show a 3% systematic improvement over previous work.",
) -> dict:
    return {
        "id": f"2501.0000{i}",
        "title": title,
        "abstract": abstract,
        "authors": ["Smith J", "Jones A", "Brown K"],
        "published": "2026-04-07",
        "url": f"https://arxiv.org/abs/2501.0000{i}",
        "pdf_url": f"https://arxiv.org/pdf/2501.0000{i}",
        "global_score": 50.0,
        "subscriber_score": 50.0,
    }


# ─────────────────────────────────────────────────────────────────────────────
# _strip_latex
# ─────────────────────────────────────────────────────────────────────────────

class TestStripLatex:
    def test_inline_math_unwrapped(self):
        # $x^2 + y^2$ → math content extracted, then ^ stripped by bare-^x rule
        # The result should not contain $ markers and should contain the variable names
        result = _strip_latex("a $x^2 + y^2$ value")
        assert "$" not in result
        assert "x" in result
        assert "y" in result
        assert "value" in result

    def test_times_replaced(self):
        assert "x" in _strip_latex(r"3 \times 10^8 m/s")

    def test_odot_replaced(self):
        assert "☉" in _strip_latex(r"1.0 \odot")

    def test_cmd_with_arg_unwrapped(self):
        result = _strip_latex(r"\textbf{hello}")
        assert "hello" in result
        assert "textbf" not in result

    def test_bare_cmd_removed(self):
        result = _strip_latex(r"temperature \sim 5000 K")
        assert "sim" not in result

    def test_subscript_braces_unwrapped(self):
        result = _strip_latex(r"T_{eff} = 5000")
        assert "eff" in result
        assert "_" not in result

    def test_superscript_bare_unwrapped(self):
        result = _strip_latex(r"M^2")
        assert result.strip() == "M2"

    def test_plain_text_unchanged(self):
        text = "A measurement of stellar radii."
        assert _strip_latex(text) == text

    def test_double_dollar_block_handled(self):
        # Double-dollar display math: content should be stripped/unwrapped
        result = _strip_latex("mass $$E = mc^2$$ energy")
        # Should not contain raw $$ markers
        assert "$$" not in result


# ─────────────────────────────────────────────────────────────────────────────
# _short_title
# ─────────────────────────────────────────────────────────────────────────────

class TestShortTitle:
    def test_short_title_unchanged(self):
        t = "Short title"
        assert _short_title(t) == t

    def test_long_title_truncated_at_word_boundary(self):
        t = "A " * 60  # way over 105 chars
        result = _short_title(t)
        assert len(result) <= 108  # 105 + "..." = 108

    def test_truncated_title_ends_with_ellipsis(self):
        t = "word " * 30
        result = _short_title(t)
        assert result.endswith("...")

    def test_latex_stripped_from_title(self):
        t = r"Measuring $T_{eff}$ via spectroscopy"
        result = _short_title(t)
        assert "$" not in result
        assert "_" not in result

    def test_max_len_respected(self):
        t = "A" * 200
        result = _short_title(t)
        # Without word boundary the truncation is at max_len
        assert len(result) <= 108


# ─────────────────────────────────────────────────────────────────────────────
# _one_sentence
# ─────────────────────────────────────────────────────────────────────────────

class TestOneSentence:
    def test_returns_first_sentence(self):
        text = "New method for stellar radii. It works well. Third sentence."
        result = _one_sentence(text)
        assert result == "New method for stellar radii."

    def test_caps_at_180_chars(self):
        long = "A" * 200 + "."
        result = _one_sentence(long)
        assert len(result) <= 183  # 180 + "..." is 183

    def test_empty_returns_empty(self):
        assert _one_sentence("") == ""

    def test_strips_latex(self):
        text = r"Measures $T_{eff}$ = 5000 K. Another sentence."
        result = _one_sentence(text)
        assert "$" not in result
        assert "_" not in result

    def test_full_text_returned_when_no_sentence_end(self):
        text = "A result without trailing punctuation"
        result = _one_sentence(text)
        assert "result" in result


# ─────────────────────────────────────────────────────────────────────────────
# Scoring cascade
# ─────────────────────────────────────────────────────────────────────────────

class TestScoringCascade:
    """score_papers_with_ai(papers) → list of papers with added AI fields.

    Tier behaviour:
      - Claude available and succeeds → uses Claude, score_tier = "ai"
      - Claude missing/fails → tries Gemini
      - Gemini fails → keyword fallback, score_tier = "keyword"
      - All missing → keyword fallback
    """

    def _make_claude_response(self, score: int = 8) -> MagicMock:
        """Build a mock Anthropic response object."""
        payload = {
            "relevance_score": score,
            "plain_summary": "Interferometric radii measured for 47 solar-type stars. "
                             "Systematic offset of 3% found vs Gaia DR3 radii.",
            "highlight_phrase": "interferometric radii beat Gaia by 3%",
        }
        mock_resp = MagicMock()
        mock_resp.content = [MagicMock(text=json.dumps(payload))]
        return mock_resp

    def _make_gemini_response(self, score: int = 7) -> MagicMock:
        payload = {
            "relevance_score": score,
            "plain_summary": "Direct radii measurements via CHARA array. "
                             "Benchmarks five interferometric surveys.",
            "highlight_phrase": "CHARA array benchmarks five surveys",
        }
        mock_resp = MagicMock()
        mock_resp.text = json.dumps(payload)
        return mock_resp

    # ── Claude succeeds ───────────────────────────────────────────────────

    def test_claude_used_when_key_present(self):
        papers = [make_paper()]
        mock_client = MagicMock()
        mock_client.messages.create.return_value = self._make_claude_response()

        with patch("shared.ai_scorer._get_anthropic_client", return_value=mock_client), \
             patch("shared.ai_scorer._get_anthropic_key", return_value="sk-fake"):
            result = score_papers_with_ai(papers)

        assert result[0]["score_tier"] == "ai"
        mock_client.messages.create.assert_called()

    def test_claude_result_has_plain_summary(self):
        papers = [make_paper()]
        mock_client = MagicMock()
        mock_client.messages.create.return_value = self._make_claude_response()

        with patch("shared.ai_scorer._get_anthropic_client", return_value=mock_client), \
             patch("shared.ai_scorer._get_anthropic_key", return_value="sk-fake"):
            result = score_papers_with_ai(papers)

        assert result[0].get("plain_summary")
        assert len(result[0]["plain_summary"]) > 10

    def test_claude_result_has_highlight_phrase(self):
        papers = [make_paper()]
        mock_client = MagicMock()
        mock_client.messages.create.return_value = self._make_claude_response()

        with patch("shared.ai_scorer._get_anthropic_client", return_value=mock_client), \
             patch("shared.ai_scorer._get_anthropic_key", return_value="sk-fake"):
            result = score_papers_with_ai(papers)

        phrase = result[0].get("highlight_phrase", "")
        assert phrase
        words = phrase.split()
        assert 5 <= len(words) <= 8, f"Expected 5-8 words, got {len(words)}: {phrase!r}"

    def test_claude_result_score_tier_is_ai(self):
        papers = [make_paper()]
        mock_client = MagicMock()
        mock_client.messages.create.return_value = self._make_claude_response()

        with patch("shared.ai_scorer._get_anthropic_client", return_value=mock_client), \
             patch("shared.ai_scorer._get_anthropic_key", return_value="sk-fake"):
            result = score_papers_with_ai(papers)

        assert result[0]["score_tier"] == "ai"

    # ── Claude fails with credit error → Gemini ───────────────────────────
    # Single per-paper failures don't fail the tier (they get keyword fallback
    # at the paper level). Credit errors and 3-consecutive-failures fail the tier.

    def test_gemini_used_when_claude_credit_exhausted(self):
        """Credit error on Claude immediately cascades to Gemini."""
        papers = [make_paper()]
        mock_claude = MagicMock()
        mock_claude.messages.create.side_effect = Exception("credit balance is too low")

        mock_gemini_client = MagicMock()
        mock_gemini_client.models.generate_content.return_value = self._make_gemini_response()

        with patch("shared.ai_scorer._get_anthropic_key", return_value="sk-fake"), \
             patch("shared.ai_scorer._get_anthropic_client", return_value=mock_claude), \
             patch("shared.ai_scorer._get_gemini_client", return_value=mock_gemini_client):
            result = score_papers_with_ai(papers)

        mock_gemini_client.models.generate_content.assert_called()

    def test_gemini_used_when_claude_3_consecutive_failures(self):
        """3 consecutive Claude failures cascade to Gemini."""
        papers = [make_paper(i) for i in range(1, 4)]  # 3 papers
        mock_claude = MagicMock()
        mock_claude.messages.create.side_effect = Exception("network timeout")

        mock_gemini_client = MagicMock()
        mock_gemini_client.models.generate_content.return_value = self._make_gemini_response()

        with patch("shared.ai_scorer._get_anthropic_key", return_value="sk-fake"), \
             patch("shared.ai_scorer._get_anthropic_client", return_value=mock_claude), \
             patch("shared.ai_scorer._get_gemini_client", return_value=mock_gemini_client):
            result = score_papers_with_ai(papers)

        mock_gemini_client.models.generate_content.assert_called()

    def test_single_claude_failure_uses_keyword_for_that_paper(self):
        """A single Claude failure on one paper results in keyword fallback for that paper
        (not a full tier cascade). The paper still gets plain_summary."""
        papers = [make_paper()]
        mock_claude = MagicMock()
        mock_claude.messages.create.side_effect = Exception("timeout")

        with patch("shared.ai_scorer._get_anthropic_key", return_value="sk-fake"), \
             patch("shared.ai_scorer._get_anthropic_client", return_value=mock_claude), \
             patch("shared.ai_scorer._get_gemini_client", return_value=None):
            result = score_papers_with_ai(papers)

        # Paper gets keyword-level fallback
        assert result[0].get("plain_summary")

    def test_gemini_fallback_still_produces_plain_summary(self):
        """After credit exhaustion on Claude, Gemini produces valid summaries."""
        papers = [make_paper()]
        mock_claude = MagicMock()
        mock_claude.messages.create.side_effect = Exception("credit balance is too low")

        mock_gemini_client = MagicMock()
        mock_gemini_client.models.generate_content.return_value = self._make_gemini_response()

        with patch("shared.ai_scorer._get_anthropic_key", return_value="sk-fake"), \
             patch("shared.ai_scorer._get_anthropic_client", return_value=mock_claude), \
             patch("shared.ai_scorer._get_gemini_client", return_value=mock_gemini_client):
            result = score_papers_with_ai(papers)

        assert result[0].get("plain_summary")

    # ── Both AI tiers fail → keyword fallback ────────────────────────────

    def test_keyword_fallback_when_no_keys(self):
        papers = [make_paper()]
        with patch("shared.ai_scorer._get_anthropic_key", return_value=None), \
             patch("shared.ai_scorer._get_gemini_key", return_value=None), \
             patch("shared.ai_scorer._get_gemini_client", return_value=None):
            result = score_papers_with_ai(papers)

        assert result[0]["score_tier"] == "keyword"

    def test_keyword_fallback_produces_plain_summary(self):
        papers = [make_paper()]
        with patch("shared.ai_scorer._get_anthropic_key", return_value=None), \
             patch("shared.ai_scorer._get_gemini_key", return_value=None), \
             patch("shared.ai_scorer._get_gemini_client", return_value=None):
            result = score_papers_with_ai(papers)

        assert result[0].get("plain_summary")

    def test_keyword_fallback_produces_highlight_phrase(self):
        papers = [make_paper()]
        with patch("shared.ai_scorer._get_anthropic_key", return_value=None), \
             patch("shared.ai_scorer._get_gemini_key", return_value=None), \
             patch("shared.ai_scorer._get_gemini_client", return_value=None):
            result = score_papers_with_ai(papers)

        phrase = result[0].get("highlight_phrase", "")
        assert phrase  # non-empty

    def test_keyword_fallback_score_tier_is_keyword(self):
        papers = [make_paper()]
        with patch("shared.ai_scorer._get_anthropic_key", return_value=None), \
             patch("shared.ai_scorer._get_gemini_key", return_value=None), \
             patch("shared.ai_scorer._get_gemini_client", return_value=None):
            result = score_papers_with_ai(papers)

        assert result[0]["score_tier"] == "keyword"

    # ── Missing API key → fall through without error ──────────────────────

    def test_missing_anthropic_key_does_not_raise(self):
        papers = [make_paper()]
        with patch("shared.ai_scorer._get_anthropic_key", return_value=None), \
             patch("shared.ai_scorer._get_gemini_key", return_value=None), \
             patch("shared.ai_scorer._get_gemini_client", return_value=None):
            # Must not raise even with no keys
            result = score_papers_with_ai(papers)
        assert len(result) == 1

    def test_empty_paper_list_returns_empty(self):
        with patch("shared.ai_scorer._get_anthropic_key", return_value=None), \
             patch("shared.ai_scorer._get_gemini_key", return_value=None), \
             patch("shared.ai_scorer._get_gemini_client", return_value=None):
            result = score_papers_with_ai([])
        assert result == []


# ─────────────────────────────────────────────────────────────────────────────
# plain_summary format checks
# ─────────────────────────────────────────────────────────────────────────────

class TestPlainSummaryFormat:
    """After scoring, plain_summary must conform to style rules."""

    def _score_with_mock_claude(self, summary_text: str) -> dict:
        papers = [make_paper()]
        payload = {
            "relevance_score": 7,
            "plain_summary": summary_text,
            "highlight_phrase": "five word phrase here test",
        }
        mock_client = MagicMock()
        mock_client.messages.create.return_value = MagicMock(
            content=[MagicMock(text=json.dumps(payload))]
        )
        with patch("shared.ai_scorer._get_anthropic_client", return_value=mock_client), \
             patch("shared.ai_scorer._get_anthropic_key", return_value="sk-fake"):
            result = score_papers_with_ai(papers)
        return result[0]

    def test_plain_summary_not_empty(self):
        p = self._score_with_mock_claude("Direct measurement of stellar radii. Results match theory.")
        assert p.get("plain_summary")

    @pytest.mark.parametrize("banned", BANNED_STARTERS)
    def test_plain_summary_not_banned_starter_from_ai(self, banned: str):
        """The scorer should pass through AI summaries that don't start with banned phrases,
        and the prompt instructs the model never to start with them. This test confirms
        the field exists and the scorer doesn't inject banned starters itself."""
        good_summary = "Interferometric radii measured for 47 stars. Results agree with theory."
        p = self._score_with_mock_claude(good_summary)
        # The scorer does not add banned starters itself
        assert not p["plain_summary"].startswith(banned)


# ─────────────────────────────────────────────────────────────────────────────
# highlight_phrase format checks
# ─────────────────────────────────────────────────────────────────────────────

class TestHighlightPhraseFormat:
    def _score(self, phrase: str) -> dict:
        papers = [make_paper()]
        payload = {
            "relevance_score": 7,
            "plain_summary": "A good summary of the result.",
            "highlight_phrase": phrase,
        }
        mock_client = MagicMock()
        mock_client.messages.create.return_value = MagicMock(
            content=[MagicMock(text=json.dumps(payload))]
        )
        with patch("shared.ai_scorer._get_anthropic_client", return_value=mock_client), \
             patch("shared.ai_scorer._get_anthropic_key", return_value="sk-fake"):
            result = score_papers_with_ai(papers)
        return result[0]

    def test_highlight_phrase_is_non_empty(self):
        p = self._score("new method beats old approach")
        assert p.get("highlight_phrase")

    def test_highlight_phrase_no_trailing_punctuation(self):
        # Scorer should strip trailing punctuation from highlight_phrase
        p = self._score("new method beats old approach.")
        phrase = p["highlight_phrase"]
        assert not phrase.endswith((".", ",", ";", ":")), f"Trailing punct in: {phrase!r}"

    def test_keyword_fallback_highlight_phrase_no_trailing_punctuation(self):
        papers = [make_paper(title="Stellar radii measured via interferometry")]
        with patch("shared.ai_scorer._get_anthropic_key", return_value=None), \
             patch("shared.ai_scorer._get_gemini_key", return_value=None), \
             patch("shared.ai_scorer._get_gemini_client", return_value=None):
            result = score_papers_with_ai(papers)
        phrase = result[0]["highlight_phrase"]
        assert not phrase.endswith((".", ",", ";", ":"))


# ─────────────────────────────────────────────────────────────────────────────
# Integration: email builder renders plain_summary not raw abstract
# ─────────────────────────────────────────────────────────────────────────────

class TestEmailBuilderIntegration:
    """After AI scoring, build_personalized_digest_email must render plain_summary
    in the paper card instead of the raw abstract."""

    def _make_scored_paper(self) -> dict:
        p = make_paper(abstract="Raw abstract text that should NOT appear in email.")
        p["plain_summary"] = "AI-generated peer-to-peer summary for the card."
        p["highlight_phrase"] = "six word highlight phrase for card"
        p["score_tier"] = "ai"
        return p

    def test_plain_summary_appears_in_html(self):
        papers = [self._make_scored_paper()]
        _, html, _ = build_personalized_digest_email(
            papers, ["stars"], WEEK, UNSUB_URL, MANAGE_URL
        )
        assert "AI-generated peer-to-peer summary" in html

    def test_raw_abstract_not_in_html_when_summary_present(self):
        papers = [self._make_scored_paper()]
        _, html, _ = build_personalized_digest_email(
            papers, ["stars"], WEEK, UNSUB_URL, MANAGE_URL
        )
        assert "Raw abstract text that should NOT appear in email." not in html

    def test_highlight_phrase_appears_in_html(self):
        papers = [self._make_scored_paper()]
        _, html, _ = build_personalized_digest_email(
            papers, ["stars"], WEEK, UNSUB_URL, MANAGE_URL
        )
        assert "six word highlight phrase for card" in html

    def test_falls_back_to_abstract_when_no_summary(self):
        """If plain_summary is absent, the card must still show something (abstract)."""
        paper = make_paper(abstract="Abstract content shown as fallback.")
        # No plain_summary set
        _, html, _ = build_personalized_digest_email(
            [paper], ["stars"], WEEK, UNSUB_URL, MANAGE_URL
        )
        assert "Abstract content shown as fallback." in html
