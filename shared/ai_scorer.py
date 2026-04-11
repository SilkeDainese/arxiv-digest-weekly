"""AI scoring cascade for the weekly student digest.

Tier order: Claude (Anthropic) → Vertex AI Gemini (ADC) → Gemini API key → keyword fallback.

Each tier is attempted in order. If a tier fails (missing key, API error,
3 consecutive failures), the next tier is tried. The keyword fallback always
succeeds — it never raises.

Each paper is enriched with:
  plain_summary    — 2-3 sentence peer-to-peer summary
  highlight_phrase — punchy 5-8 word headline
  score_tier       — "ai" or "keyword"

Stateless: no filesystem access. Keys fetched from Secret Manager (via shared/secrets.py)
on first call, cached at module level for the function's lifetime.

Adapted from ~/Projects/arxiv-digest/digest.py (Silke S. Dainese, 2025).
Intent of the prompts is faithfully preserved; implementation adapted for
Cloud Functions (stateless, Secret Manager keys, no config.yaml).
"""
from __future__ import annotations

import concurrent.futures
import json
import logging
import os
import re
import threading
from typing import Any

logger = logging.getLogger(__name__)

# ─────────── constants ──────────────────────────────────────────────────────

# Max workers for concurrent API calls
_MAX_WORKERS = 5
# How many consecutive failures before abandoning a tier
_MAX_CONSECUTIVE_FAILURES = 3

# Sentence openers that indicate author-voice writing — forbidden in plain_summary.
# Used by: prompt builder (instruction to AI), keyword fallback (sentence filter),
# and quality gate (final enforcement).
BANNED_OPENERS: tuple[str, ...] = (
    # Author-voice "We ..." variants
    "we present",
    "we show",
    "we propose",
    "we investigate",
    "we find",
    "we explore",
    "we describe",
    "we analyze",
    "we analyse",
    "we demonstrate",
    "we report",
    "we study",
    "we ",          # catch-all: "We " followed by any verb
    # Contextual author-voice starters
    "in this paper",
    "in this work",
    "here we",
    "this work",
    # Original set
    "researchers",
    "the authors",
    "this paper",
    "a team",
    "scientists",
    "the researchers",
    "authors",
)


def _starts_with_banned_opener(text: str) -> bool:
    """Return True if *text* starts (case-insensitively) with any banned opener."""
    lowered = text.lower().lstrip()
    return any(lowered.startswith(opener) for opener in BANNED_OPENERS)


STUDENT_RESEARCH_CONTEXT = (
    "AU astronomy students — bachelor's, master's, and PhD level. "
    "Topics include stellar astrophysics, exoplanets, galaxies, cosmology, "
    "high-energy astrophysics, instrumentation, and solar/heliospheric physics. "
    "Students benefit from accessible summaries that lead with the key result or method, "
    "explain what is new, and use domain-appropriate language without over-simplifying."
)


# ─────────── LaTeX stripping ─────────────────────────────────────────────────

def _strip_latex(text: str) -> str:
    """Remove inline LaTeX from text (titles, abstracts, AI summaries).

    Handles: $x$ inline math, $$...$$ display math, \\cmd{arg}, bare \\cmd,
    _{sub}, ^{sup}, bare _x / ^x.
    """
    # Display math $$...$$
    text = re.sub(r"\$\$([^$]*)\$\$", r"\1", text)
    # Inline math $...$
    text = re.sub(r"\$([^$]+)\$", r"\1", text)
    # Common symbols
    text = re.sub(r"\\times", "x", text)
    text = re.sub(r"\\odot", "\u2609", text)   # ☉
    text = re.sub(r"\\sim", "~", text)
    text = re.sub(r"\\approx", "~", text)
    text = re.sub(r"\\leq", "<=", text)
    text = re.sub(r"\\geq", ">=", text)
    text = re.sub(r"\\pm", "+/-", text)
    text = re.sub(r"\\cdot", ".", text)
    # \cmd{arg} → arg
    text = re.sub(r"\\[a-zA-Z]+\{([^}]*)\}", r"\1", text)
    # bare \cmd → remove
    text = re.sub(r"\\[a-zA-Z]+", "", text)
    # _{sub} and ^{sup} → sub/sup
    text = re.sub(r"[_^]\{([^}]*)\}", r"\1", text)
    # _x and ^x (single char)
    text = re.sub(r"[_^](\S)", r"\1", text)
    return " ".join(text.split()).strip()


# ─────────── title / summary utilities ───────────────────────────────────────

def _short_title(title: str, max_len: int = 105) -> str:
    """Return a shortened title: LaTeX stripped, truncated at word boundary."""
    t = _strip_latex(" ".join((title or "").split()))
    if len(t) <= max_len:
        return t
    truncated = t[:max_len]
    last_space = truncated.rfind(" ")
    if last_space > max_len // 2:
        truncated = truncated[:last_space]
    return truncated.rstrip() + "..."


def _one_sentence(text: str) -> str:
    """Return the first sentence-like chunk (≤180 chars) from a summary.

    Strips LaTeX, collapses whitespace, cleans up comma artifacts from
    removed math tokens.
    """
    clean = " ".join((text or "").split())
    if not clean:
        return ""
    clean = _strip_latex(clean)
    clean = re.sub(r"\s*,\s*,", ",", clean)   # collapse ",, " from removed math
    clean = re.sub(r"of\s*,", "of", clean)    # clean dangling prepositions
    clean = clean.strip()
    if not clean:
        return ""
    m = re.match(r"^(.+?[.!?])\s", clean)
    sentence = m.group(1) if m else clean
    if len(sentence) > 180:
        sentence = sentence[:177].rstrip() + "..."
    return sentence


def _clean_highlight_phrase(phrase: str) -> str:
    """Strip trailing punctuation from a highlight phrase."""
    return phrase.rstrip(".,;:!?").strip()


def _strip_markdown_fences(text: str) -> str:
    """Remove ```json ... ``` or ``` ... ``` wrappers that some models add."""
    if text.startswith("```"):
        text = re.sub(r"^```[a-z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text)
    return text.strip()


# ─────────── prompt builder ───────────────────────────────────────────────────

def _build_prompt(paper: dict[str, Any]) -> str:
    """Build the scoring + summarisation prompt for Claude / Gemini."""
    clean_title = _strip_latex(paper.get("title", ""))
    clean_abstract = _strip_latex(paper.get("abstract", ""))
    authors = ", ".join((paper.get("authors") or [])[:8])

    return f"""You are helping curate a weekly arXiv digest for astronomy students.

AUDIENCE CONTEXT:
{STUDENT_RESEARCH_CONTEXT}

PAPER:
Title: {clean_title}
Authors: {authors}
Category: {paper.get("category", "astro-ph")}
Abstract: {clean_abstract}

Respond with ONLY a valid JSON object (no markdown, no backticks):
{{
  "relevance_score": <integer 1-10>,
  "plain_summary": "<HARD LIMIT: max 280 characters. 1-2 punchy sentences, or 1 sentence with em-dash clauses. Peer-to-peer shorthand, as one scientist nudging another. Lead with the result or method. Use concrete numbers, technical abbreviations, em-dashes for clause separation. Do NOT mimic the abstract's formal prose. Match the EXAMPLE's length and rhythm exactly. NEVER start with any of these openers: 'We', 'We present', 'We show', 'We propose', 'We investigate', 'We find', 'We explore', 'We describe', 'We analyze', 'We analyse', 'We demonstrate', 'We report', 'We study', 'In this paper', 'In this work', 'Here we', 'This work', 'Researchers', 'The authors', 'This paper', 'A team', 'Scientists', 'Authors', 'The researchers'. Assume domain knowledge. EXAMPLE (match this length and style exactly, ~145 chars): 'New ML approach for stellar Teff from high-res spectra — synthetic MARCS training, recovers within 50K on APOGEE benchmarks. Struggles below [Fe/H] = -2.'>",
  "highlight_phrase": "<punchy 5-8 word headline, no trailing punctuation>"
}}

Score for student interest:
10 = landmark result, would be discussed in seminars
8-9 = directly relevant to active research areas
6-7 = interesting method or result students would want to know
4-5 = tangentially interesting
1-3 = not relevant to astronomy students
"""


# ─────────── key / client accessors (mockable) ───────────────────────────────

def _get_anthropic_key() -> str | None:
    """Fetch Anthropic API key from Secret Manager. Returns None if unavailable."""
    try:
        from shared.secrets import get_secret
        return get_secret("anthropic-api-key")
    except Exception as exc:
        logger.debug("anthropic-api-key not available: %s", exc)
        return None


def _get_gemini_key() -> str | None:
    """Fetch Gemini API key from Secret Manager. Returns None if unavailable."""
    try:
        from shared.secrets import get_secret
        return get_secret("gemini-api-key")
    except Exception as exc:
        logger.debug("gemini-api-key not available: %s", exc)
        return None


def _get_anthropic_client(api_key: str):
    """Return an Anthropic client for the given key."""
    import anthropic
    return anthropic.Anthropic(api_key=api_key)


def _get_gemini_client(api_key: str | None = None):
    """Return a google-generativeai client (Vertex ADC or API key)."""
    try:
        from google import genai as google_genai
    except ImportError:
        return None

    if api_key:
        return google_genai.Client(api_key=api_key)

    # Vertex AI: use ADC (automatic on Cloud Functions)
    gcp_project = os.environ.get("GOOGLE_CLOUD_PROJECT", "silke-hub")
    try:
        return google_genai.Client(
            vertexai=True,
            project=gcp_project,
            location="us-central1",
        )
    except Exception as exc:
        logger.debug("Vertex AI Gemini client init failed: %s", exc)
        return None


# ─────────── per-paper analysis helpers ──────────────────────────────────────

def _parse_ai_response(text: str) -> dict[str, Any] | None:
    """Parse JSON from AI response. Returns None on failure."""
    try:
        clean = _strip_markdown_fences(text)
        data = json.loads(clean)
        if not isinstance(data, dict):
            return None
        return data
    except (json.JSONDecodeError, ValueError):
        return None


def _apply_ai_fields(paper: dict[str, Any], data: dict[str, Any]) -> None:
    """Write AI result fields onto a paper dict in-place."""
    paper["plain_summary"] = _strip_latex(str(data.get("plain_summary", "")).strip())
    paper["highlight_phrase"] = _clean_highlight_phrase(
        _strip_latex(str(data.get("highlight_phrase", "")).strip())
    )
    paper["ai_score"] = int(data.get("relevance_score", 5))
    paper["score_tier"] = "ai"


def _split_sentences(text: str) -> list[str]:
    """Split text into sentences by '. ', '! ', '? ' boundaries.

    Returns a list of stripped sentence strings. Does not modify the text.
    """
    # Split on sentence-ending punctuation followed by whitespace
    parts = re.split(r'(?<=[.!?])\s+', text.strip())
    return [p.strip() for p in parts if p.strip()]


def _apply_keyword_fields(paper: dict[str, Any]) -> None:
    """Fill plain_summary and highlight_phrase from the paper's own data.

    Sentence-level author-voice filter (fail-closed):
      - Check the first 3 sentences of the abstract.
      - Skip any sentence that starts with a banned opener (BANNED_OPENERS).
      - Use the first clean sentence found, trimmed to 250 chars.
      - If no clean sentence is found in the first 3, plain_summary is set to ""
        (empty string), which will be caught and rejected by the quality gate.

    Do NOT rewrite author-voice to third person. Either find a clean sentence
    or produce an empty summary — fail closed.
    """
    abstract = paper.get("abstract", "")
    title = paper.get("title", "")

    # Strip LaTeX and normalise whitespace from the whole abstract
    clean_abstract = _strip_latex(" ".join(abstract.split()))

    sentences = _split_sentences(clean_abstract)
    first_three = sentences[:3]

    chosen = ""
    for sentence in first_three:
        if not _starts_with_banned_opener(sentence):
            # Found a clean sentence — trim to 250 chars at word boundary
            if len(sentence) > 250:
                cut = sentence[:250].rfind(" ")
                sentence = sentence[:cut] + "..." if cut > 150 else sentence[:250] + "..."
            chosen = sentence
            break

    # chosen is "" if all first-3 sentences were author-voice (fail-closed)
    paper["plain_summary"] = chosen
    paper["highlight_phrase"] = _clean_highlight_phrase(_short_title(title, max_len=50))
    paper["ai_score"] = paper.get("global_score", paper.get("subscriber_score", 0))
    paper["score_tier"] = "keyword"


# ─────────── tier implementations ─────────────────────────────────────────────

def _score_with_claude(
    papers: list[dict[str, Any]],
    api_key: str,
) -> list[dict[str, Any]] | None:
    """Score papers via Claude. Returns enriched papers or None on tier failure.

    Tier failure is triggered by:
      - A credit/billing error (immediate cascade)
      - _MAX_CONSECUTIVE_FAILURES or more total failures across the batch

    Individual paper failures fall back to keyword scoring at the paper level
    rather than failing the whole tier.
    """
    try:
        client = _get_anthropic_client(api_key)
    except Exception as exc:
        logger.warning("Anthropic client init failed: %s", exc)
        return None

    results: list[dict[str, Any]] = [None] * len(papers)  # type: ignore[list-item]
    _lock = threading.Lock()
    failure_count = [0]
    tier_failed = [False]

    def _process(idx: int, paper: dict[str, Any]) -> None:
        if tier_failed[0]:
            return
        prompt = _build_prompt(paper)
        try:
            response = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=400,
                messages=[{"role": "user", "content": prompt}],
            )
            text = response.content[0].text.strip()
            data = _parse_ai_response(text)
            if data:
                p = dict(paper)
                _apply_ai_fields(p, data)
                results[idx] = p
                logger.info("Claude scored paper %s: %s/10", paper.get("id", "?"), p.get("ai_score"))
                return
        except Exception as exc:
            err = str(exc).lower()
            if "credit balance" in err or "billing" in err:
                logger.warning("Claude credits exhausted — cascading to next tier")
                with _lock:
                    tier_failed[0] = True
                return
            logger.warning("Claude error for paper %s: %s", paper.get("id", "?"), exc)

        with _lock:
            failure_count[0] += 1
            if failure_count[0] >= _MAX_CONSECUTIVE_FAILURES:
                logger.warning("Claude: %d failures total — cascading", failure_count[0])
                tier_failed[0] = True
        # Paper stays None — keyword fallback applied below

    with concurrent.futures.ThreadPoolExecutor(max_workers=_MAX_WORKERS) as ex:
        futs = [ex.submit(_process, i, p) for i, p in enumerate(papers)]
        for f in concurrent.futures.as_completed(futs):
            f.result()  # re-raise thread exceptions

    if tier_failed[0]:
        return None

    # Per-paper failures get keyword fallback; tier is not failed.
    for i, p in enumerate(results):
        if p is None:
            fallback = dict(papers[i])
            _apply_keyword_fields(fallback)
            results[i] = fallback

    return results  # type: ignore[return-value]


def _score_with_gemini(
    papers: list[dict[str, Any]],
    client,
) -> list[dict[str, Any]] | None:
    """Score papers via Gemini (Vertex ADC or API key). Returns None on tier failure."""
    results: list[dict[str, Any]] = [None] * len(papers)  # type: ignore[list-item]
    _lock = threading.Lock()
    failure_count = [0]
    tier_failed = [False]

    def _process(idx: int, paper: dict[str, Any]) -> None:
        if tier_failed[0]:
            return
        prompt = _build_prompt(paper)
        try:
            response = client.models.generate_content(
                model="gemini-2.5-pro",
                contents=prompt,
            )
            text = response.text.strip()
            data = _parse_ai_response(text)
            if data:
                p = dict(paper)
                _apply_ai_fields(p, data)
                results[idx] = p
                logger.info("Gemini scored paper %s: %s/10", paper.get("id", "?"), p.get("ai_score"))
                return
        except Exception as exc:
            logger.warning("Gemini error for paper %s: %s", paper.get("id", "?"), exc)

        with _lock:
            failure_count[0] += 1
            if failure_count[0] >= _MAX_CONSECUTIVE_FAILURES:
                logger.warning("Gemini: %d failures total — cascading", failure_count[0])
                tier_failed[0] = True

    with concurrent.futures.ThreadPoolExecutor(max_workers=_MAX_WORKERS) as ex:
        futs = [ex.submit(_process, i, p) for i, p in enumerate(papers)]
        for f in concurrent.futures.as_completed(futs):
            f.result()

    if tier_failed[0]:
        return None

    for i, p in enumerate(results):
        if p is None:
            fallback = dict(papers[i])
            _apply_keyword_fields(fallback)
            results[i] = fallback

    return results  # type: ignore[return-value]


def _score_keyword_only(papers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keyword fallback: always succeeds. Generates plain_summary and highlight_phrase
    from the paper's own title and abstract."""
    result = []
    for paper in papers:
        p = dict(paper)
        _apply_keyword_fields(p)
        result.append(p)
    return result


# ─────────── public API ───────────────────────────────────────────────────────

def score_papers_with_ai(papers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Score a list of papers using the AI cascade.

    Tries:
      1. Claude (Anthropic) — if anthropic-api-key secret is available
      2. Vertex AI Gemini (ADC, automatic on Cloud Functions)
      3. Gemini via API key — if gemini-api-key secret is available
      4. Keyword fallback — always succeeds

    Each paper is enriched with:
      plain_summary    — 2-3 sentence peer-to-peer summary
      highlight_phrase — 5-8 word headline
      score_tier       — "ai" or "keyword"
      ai_score         — numeric relevance score (1-10 from AI, 0-100 from keyword)

    Fails gracefully: missing secrets → skip that tier. API errors → try next tier.
    Never raises. Always returns the same number of papers as input.
    """
    if not papers:
        return []

    # ── Tier 1: Claude ───────────────────────────────────────────────────
    anthropic_key = _get_anthropic_key()
    if anthropic_key:
        logger.info("AI scorer: attempting Claude (%d papers)", len(papers))
        try:
            result = _score_with_claude(papers, anthropic_key)
            if result is not None:
                logger.info("AI scorer: Claude succeeded")
                return result
            logger.warning("AI scorer: Claude tier failed, trying Gemini")
        except Exception as exc:
            logger.warning("AI scorer: Claude raised unexpectedly: %s — trying Gemini", exc)

    # ── Tier 2: Vertex AI Gemini (ADC) ───────────────────────────────────
    logger.info("AI scorer: attempting Vertex AI Gemini (%d papers)", len(papers))
    try:
        vertex_client = _get_gemini_client(api_key=None)
        if vertex_client is not None:
            result = _score_with_gemini(papers, vertex_client)
            if result is not None:
                logger.info("AI scorer: Vertex AI Gemini succeeded")
                return result
            logger.warning("AI scorer: Vertex AI Gemini tier failed, trying Gemini API key")
    except Exception as exc:
        logger.warning("AI scorer: Vertex Gemini raised unexpectedly: %s", exc)

    # ── Tier 3: Gemini via API key ────────────────────────────────────────
    gemini_key = _get_gemini_key()
    if gemini_key:
        logger.info("AI scorer: attempting Gemini API key (%d papers)", len(papers))
        try:
            api_client = _get_gemini_client(api_key=gemini_key)
            if api_client is not None:
                result = _score_with_gemini(papers, api_client)
                if result is not None:
                    logger.info("AI scorer: Gemini API key succeeded")
                    return result
        except Exception as exc:
            logger.warning("AI scorer: Gemini API key raised unexpectedly: %s", exc)

    # ── Tier 4: keyword fallback ──────────────────────────────────────────
    logger.info("AI scorer: using keyword fallback (%d papers)", len(papers))
    return _score_keyword_only(papers)
