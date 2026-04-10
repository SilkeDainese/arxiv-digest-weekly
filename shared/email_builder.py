"""HTML and plaintext email template builders.

All templates are self-contained Python string templates — no Jinja2 dependency.
This keeps the Cloud Functions package small and avoids template injection risk.

Brand palette mirrors ~/Projects/arxiv-digest/brand.py — single source of truth for
colours is the old pipeline; this module keeps them in sync.
Typography: DM Serif Display headings, IBM Plex Sans body, DM Mono labels.
"""
from __future__ import annotations

import html as html_mod
from typing import Any, Optional

from shared.ai_scorer import _strip_latex

# ─────── Brand palette ────────────────────────────────────────────────────
# Kept in sync with ~/Projects/arxiv-digest/brand.py
PINE = "#2F4F3E"
GOLD = "#EBC944"
UMBER = "#7A5A3A"
ASH_WHITE = "#F6F5F2"
ASH_BLACK = "#2B2B2B"
CARD_BORDER = "#D8D6D0"
WARM_GREY = "#6A6A66"
PINE_WASH = "#EDF2EF"
PINE_LIGHT = "#3D6B52"
GOLD_LIGHT = "#F5E08A"
GOLD_WASH = "#FFF8E1"
CREAM = "#F5F3EF"
WARM_WHITE = "#FFFDF8"
FOOTER_BG = "#F0EDE6"
SOFT_GREY = "#BBB"
CHARCOAL = "#1F1F1F"

FONT_HEADING = "'DM Serif Display', Georgia, serif"
FONT_BODY = "'IBM Plex Sans', Helvetica, Arial, sans-serif"
FONT_MONO = "'DM Mono', monospace"

# Google Fonts import URL — loaded in the <head> for email clients that allow it
FONT_IMPORT_URL = (
    "https://fonts.googleapis.com/css2?family=DM+Serif+Display:ital@0;1"
    "&family=IBM+Plex+Sans:wght@300;400;600"
    "&family=DM+Mono:wght@400;500"
    "&display=swap"
)

# ─────── Utilities ────────────────────────────────────────────────────────


def _h(text: str) -> str:
    """HTML-escape a string."""
    return html_mod.escape(str(text))


def _paper_html(paper: dict[Any, Any]) -> str:
    """Alias for _paper_card_branded — used by prep_preview for example digest HTML."""
    return _paper_card_branded(paper)


def _short_title(title: str, max_len: int = 100) -> str:
    """Strip LaTeX and truncate a title to max_len characters, preserving word boundaries.

    LaTeX stripping must happen before truncation so that titles like
    '$\\alpha$-elements in nearby stars' don't show raw '$\\alpha$' in emails.
    """
    t = _strip_latex(" ".join((title or "").split()))
    if len(t) <= max_len:
        return t
    truncated = t[:max_len]
    last_space = truncated.rfind(" ")
    if last_space > max_len * 0.6:
        truncated = truncated[:last_space]
    return truncated + "\u2026"


# ─────── Shared document shell ────────────────────────────────────────────


def _html_head(title: str) -> str:
    """Return the <head> section with font imports and meta tags."""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{_h(title)}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="{FONT_IMPORT_URL}" rel="stylesheet">
</head>
<body style="margin:0;padding:0;background:{ASH_WHITE};color:{ASH_BLACK};font-family:{FONT_BODY};font-weight:300;line-height:1.7;-webkit-font-smoothing:antialiased">
<table width="100%" cellpadding="0" cellspacing="0" border="0" style="background:{ASH_WHITE}">
<tr><td align="center">
<table width="680" cellpadding="0" cellspacing="0" border="0" style="max-width:680px;width:100%;background:{ASH_WHITE}">"""


def _html_close() -> str:
    return """
</table>
</td></tr>
</table>
</body>
</html>"""


# ─────── Shared components ────────────────────────────────────────────────


def _pine_header_bar(left_text: str, right_text: str = "") -> str:
    """Return a pine-coloured header bar (table row)."""
    right_cell = (
        f'<td style="text-align:right;font-family:{FONT_MONO};font-size:11px;'
        f'color:rgba(255,253,248,0.7)">{_h(right_text)}</td>'
        if right_text else ""
    )
    return f"""
  <tr><td style="background:{PINE};padding:14px 28px">
    <table width="100%" cellpadding="0" cellspacing="0" border="0">
      <tr>
        <td style="font-family:{FONT_HEADING};font-size:18px;color:{WARM_WHITE}">{left_text}</td>
        {right_cell}
      </tr>
    </table>
  </td></tr>"""


def _section_divider(label: str) -> str:
    return (
        f'  <tr><td style="padding:20px 44px 14px;font-family:{FONT_MONO};font-size:9px;'
        f'letter-spacing:0.25em;text-transform:uppercase;color:{WARM_GREY}">'
        f'\u2500\u2500 {_h(label)} \u2500\u2500</td></tr>'
    )


# ─────── Branded paper card (student / digest style) ──────────────────────


def _paper_card_branded(paper: dict[str, Any], show_score: bool = False) -> str:
    """Render a single paper as a branded card (student-digest style).

    Uses the same visual language as _render_student_paper_card in the old digest.py:
    - DM Serif Display title (linked)
    - Highlight phrase as a small gold label above the title (when AI-scored)
    - IBM Plex Sans body for plain_summary (AI) or abstract (fallback)
    - DM Mono labels for authors / meta
    - Bottom border separator
    - Optional score badge (for preview email)
    - AU affiliation badge when au_authors is non-empty

    Rendering priority:
      1. plain_summary (AI-generated) — shown if non-empty
      2. abstract (raw) — fallback when plain_summary is absent
    """
    title = _h(_short_title(paper.get("title", "Untitled")))
    authors = paper.get("authors", [])
    author_str = _h(", ".join(authors[:5]) + (" et al." if len(authors) > 5 else ""))
    url = _h(paper.get("url", "#"))
    pdf_url = _h(paper.get("pdf_url", paper.get("url", "#")))

    # ── Summary: prefer AI plain_summary over raw abstract ──
    plain_summary = (paper.get("plain_summary") or "").strip()
    abstract = (paper.get("abstract") or "").strip()
    body_text = _h(plain_summary if plain_summary else abstract)

    # ── Highlight phrase (AI-scored papers only) ──
    highlight_phrase = (paper.get("highlight_phrase") or "").strip()
    highlight_html = ""
    if highlight_phrase and plain_summary:
        # Render as a small gold-accented label above the title
        highlight_html = (
            f'<div style="font-family:{FONT_MONO};font-size:10px;letter-spacing:0.12em;'
            f'text-transform:uppercase;color:{UMBER};margin-bottom:6px">'
            f'{_h(highlight_phrase)}</div>'
        )

    # ── AU affiliation badge ──
    au_authors = paper.get("au_authors") or []
    au_badge_html = ""
    if au_authors:
        au_badge_html = (
            f'<span style="display:inline-block;font-family:{FONT_MONO};font-size:9.5px;'
            f'letter-spacing:0.1em;background:{GOLD};color:{ASH_BLACK};padding:2px 7px;'
            f'border-radius:3px;margin-left:6px">'
            f'\U0001f3db Aarhus University</span>'
        )

    # ── Score badge (preview email only) ──
    score_badge = ""
    if show_score:
        score = paper.get("global_score", paper.get("subscriber_score", 0))
        try:
            score_val = float(score)
        except (TypeError, ValueError):
            score_val = 0.0
        score_badge = (
            f'<div style="margin-bottom:6px">'
            f'<span style="font-family:{FONT_MONO};font-size:10px;letter-spacing:0.15em;'
            f'text-transform:uppercase;background:{PINE};color:white;padding:2px 8px;'
            f'border-radius:3px">score {score_val:.1f}</span>'
            f'</div>'
        )

    return f"""
    <div style="padding:16px 0;border-bottom:1px solid {CARD_BORDER}">
      {score_badge}
      {highlight_html}
      <div style="font-family:{FONT_HEADING};font-size:19px;color:{ASH_BLACK};line-height:1.35;margin-bottom:4px">
        <a href="{url}" style="color:inherit;text-decoration:none">{title}</a>
      </div>
      <div style="font-family:{FONT_MONO};font-size:11px;color:{WARM_GREY};margin-bottom:8px">{author_str}{au_badge_html}</div>
      <div style="font-family:{FONT_BODY};font-size:14px;color:#555;line-height:1.6;margin-bottom:8px">{body_text}</div>
      <div style="font-family:{FONT_BODY};font-size:12px">
        <a href="{url}" style="color:{PINE_LIGHT};text-decoration:none;margin-right:14px">Abstract on arXiv &#8594;</a>
        <a href="{pdf_url}" style="color:{WARM_GREY};text-decoration:none">PDF</a>
      </div>
    </div>"""


# ─────── Plaintext helpers ────────────────────────────────────────────────


def _paper_text(paper: dict[str, Any]) -> str:
    """Render a single paper as plaintext. Uses plain_summary if available."""
    title = paper.get("title", "Untitled")
    authors = paper.get("authors", [])
    author_str = ", ".join(authors[:5]) + (" et al." if len(authors) > 5 else "")
    plain_summary = (paper.get("plain_summary") or "").strip()
    abstract = paper.get("abstract", "")
    body = plain_summary if plain_summary else abstract
    url = paper.get("url", "#")
    return f"\n{title}\n{author_str}\n{body}\n{url}\n"


# ─────── Digest email (student copy) ─────────────────────────────────────


def _score_tier_notice(papers: list[dict[str, Any]]) -> tuple[str, str]:
    """Return (html_notice, text_notice) for the dominant score tier in the paper list.

    Checks score_tier on each paper and picks the majority tier.
    Tier hierarchy for display: claude > gemini-vertex / gemini-api > keyword / ai.

    Returns:
        (html_snippet, text_snippet) — both are short, subtle one-liners.
    """
    if not papers:
        return "", ""

    # Count tiers
    tier_counts: dict[str, int] = {}
    for p in papers:
        tier = (p.get("score_tier") or "keyword").lower()
        tier_counts[tier] = tier_counts.get(tier, 0) + 1

    # Dominant tier = most frequent
    dominant = max(tier_counts, key=lambda t: tier_counts[t])

    if dominant == "claude":
        html = "Summaries by Claude Haiku (Anthropic)"
        text = "Summaries by Claude Haiku (Anthropic)"
    elif dominant in ("gemini-vertex", "gemini-api"):
        html = "Summaries by Gemini (Google)"
        text = "Summaries by Gemini (Google)"
    elif dominant == "keyword":
        html = "Keyword ranking only \u2014 AI scoring unavailable this run"
        text = "Keyword ranking only — AI scoring unavailable this run"
    else:
        # Generic "ai" tier or unknown — don't claim a specific model
        html = "Summaries by AI"
        text = "Summaries by AI"

    return html, text


def build_personalized_digest_email(
    papers: list[dict[str, Any]],
    subscriber_topics: list[str],
    week_iso: str,
    unsubscribe_url: str,
    manage_url: str,
) -> tuple[str, str, str]:
    """Build a branded personalized digest email for a student subscriber.

    Returns:
        (subject, html_body, text_body)
    """
    topic_display = ", ".join(t.replace("_", " ").title() for t in subscriber_topics)
    paper_count = len(papers)
    subject = f"\U0001f52d arXiv Digest \u2014 {week_iso} \u2014 {paper_count} paper{'s' if paper_count != 1 else ''} ({topic_display})"

    # ── Paper cards ──
    if papers:
        cards_html = "".join(_paper_card_branded(p) for p in papers)
    else:
        cards_html = (
            f'<div style="text-align:center;padding:48px 24px;color:{WARM_GREY};'
            f'font-family:{FONT_HEADING};font-style:italic;font-size:18px">'
            f'No new papers matched your topics this week. All quiet on the arXiv front. &#x2615;</div>'
        )

    # ── Score tier notice ──
    tier_html, tier_text = _score_tier_notice(papers)
    tier_notice_html = (
        f'<div style="font-family:{FONT_MONO};font-size:9px;color:{WARM_GREY};'
        f'letter-spacing:0.08em;margin-top:6px;text-align:center">{_h(tier_html)}</div>'
        if tier_html else ""
    )

    # ── Footer links ──
    manage_link = f'<a href="{_h(manage_url)}" style="color:{PINE};text-decoration:none">&#x2699;&#xFE0F; Change categories</a>'
    unsub_link = f'<a href="{_h(unsubscribe_url)}" style="color:{SOFT_GREY};text-decoration:none">Unsubscribe</a>'

    html_body = (
        _html_head(f"arXiv Digest {week_iso}")
        + _pine_header_bar("AU student digest", week_iso)
        + f"""
  <tr><td style="padding:24px 28px 16px">
    <div style="font-family:{FONT_HEADING};font-size:26px;color:{ASH_BLACK};margin-bottom:4px">Your papers this week</div>
    <div style="font-family:{FONT_MONO};font-size:12px;color:{WARM_GREY}">{paper_count} paper{"s" if paper_count != 1 else ""} &middot; {_h(topic_display)}</div>
  </td></tr>

  <!-- PAPER CARDS -->
  <tr><td style="padding:8px 28px 32px">
    {cards_html}
  </td></tr>

  <!-- FOOTER -->
  <tr><td style="padding:28px 44px;border-top:1px solid {CARD_BORDER};background:{FOOTER_BG}">
    <div style="text-align:center;font-size:18px;margin-bottom:16px;letter-spacing:10px">
      <span style="color:{GOLD}">&#x2726;</span>
      <span style="color:{WARM_GREY};opacity:0.5"> &middot; </span>
      <span style="color:{GOLD}">&#x2726;</span>
      <span style="color:{WARM_GREY};opacity:0.5"> &middot; </span>
      <span style="color:{GOLD}">&#x2726;</span>
    </div>
    <div style="font-family:{FONT_MONO};font-size:10px;color:{WARM_GREY};letter-spacing:0.08em;margin-bottom:12px;text-align:center">
      {manage_link} &nbsp;&middot;&nbsp; {unsub_link}
    </div>
    <div style="font-family:{FONT_MONO};font-size:9.5px;color:{WARM_GREY};letter-spacing:0.1em;line-height:2.2;text-align:center">
      Made by <a href="https://silkedainese.github.io" style="color:{WARM_GREY};text-decoration:none">Silke Dainese</a> &middot;
      <a href="mailto:dainese@phys.au.dk" style="color:{WARM_GREY};text-decoration:none">dainese@phys.au.dk</a><br>
      Summaries are AI-generated and may contain errors.<br>
      This digest is a personal project and is not affiliated with Aarhus University.
    </div>
    {tier_notice_html}
  </td></tr>"""
        + _html_close()
    )

    text_papers = (
        "\n".join(_paper_text(p) for p in papers)
        if papers
        else "No new papers matched your topics this week."
    )
    tier_text_line = f"\n{tier_text}" if tier_text else ""
    text_body = f"""arXiv Digest \u2014 {week_iso}
Your topics: {topic_display}

{text_papers}

---
Change categories: {manage_url}
Unsubscribe: {unsubscribe_url}{tier_text_line}
"""

    return subject, html_body, text_body


# ─────── Preview email (Silke's Saturday copy) ───────────────────────────


def build_preview_email(
    papers: list[dict[str, Any]],
    subscriber_count: int,
    topic_breakdown: dict[str, int],
    week_iso: str,
    cancel_url: str,
    logs_url: str,
    example_digest_html: Optional[str] = None,
) -> tuple[str, str, str]:
    """Build the Saturday preview email for Silke.

    Header prominently shows subscriber count: "N subscribers will receive this Monday".
    If N=0 it says: "No subscribers yet — this is a dry run."

    Returns:
        (subject, html_body, text_body)
    """
    top_papers = papers[:10]
    subject = (
        f"[Preview] arXiv digest going out Monday \u2014 "
        f"{len(papers)} papers, {subscriber_count} subscriber{'s' if subscriber_count != 1 else ''}"
    )

    # ── Subscriber count line ──
    # Note: count + noun must appear as a contiguous text run outside any span
    # so test assertions (and email clients with text extraction) can find it.
    if subscriber_count == 0:
        sub_line_html = (
            f'<div style="font-family:{FONT_BODY};font-size:15px;color:{UMBER};'
            f'background:{GOLD_WASH};border:1px solid {GOLD_LIGHT};border-radius:5px;'
            f'padding:10px 14px;margin-bottom:18px">'
            f'No subscribers yet \u2014 this is a dry run. No emails will go out Monday.</div>'
        )
        sub_line_text = "No subscribers yet \u2014 this is a dry run."
    elif subscriber_count == 1:
        sub_line_html = (
            f'<div style="font-family:{FONT_BODY};font-size:15px;font-weight:600;'
            f'color:{ASH_BLACK};margin-bottom:14px">'
            f'<span style="font-family:{FONT_HEADING};font-size:40px;color:{PINE};'
            f'line-height:1;vertical-align:middle;margin-right:4px">1</span>'
            f'<span style="vertical-align:middle">1 subscriber will receive this Monday.</span>'
            f'</div>'
        )
        sub_line_text = "1 subscriber will receive this Monday."
    else:
        sub_line_html = (
            f'<div style="font-family:{FONT_BODY};font-size:15px;font-weight:600;'
            f'color:{ASH_BLACK};margin-bottom:14px">'
            f'<span style="font-family:{FONT_HEADING};font-size:40px;color:{PINE};'
            f'line-height:1;vertical-align:middle;margin-right:4px">{subscriber_count}</span>'
            f'<span style="vertical-align:middle">{subscriber_count} subscribers will receive this Monday.</span>'
            f'</div>'
        )
        sub_line_text = f"{subscriber_count} subscribers will receive this Monday."

    # ── Topic breakdown table ──
    breakdown_rows = "".join(
        f"<tr>"
        f"<td style='padding:4px 16px 4px 0;font-family:{FONT_BODY};font-size:13px;color:{ASH_BLACK}'>"
        f"{_h(t.replace('_', ' ').title())}</td>"
        f"<td style='padding:4px 0;font-family:{FONT_MONO};font-size:12px;color:{WARM_GREY}'>"
        f"{c} subscriber{'s' if c != 1 else ''}</td></tr>"
        for t, c in sorted(topic_breakdown.items(), key=lambda x: -x[1])
    )

    # ── Top paper cards ──
    paper_cards_html = (
        "".join(_paper_card_branded(p, show_score=True) for p in top_papers)
        if top_papers
        else f'<div style="color:{WARM_GREY};font-family:{FONT_BODY};padding:24px 0">No papers this week.</div>'
    )

    # ── Example digest section ──
    example_section = ""
    if example_digest_html:
        example_section = f"""
  <tr><td style="padding:8px 28px 24px">
    <div style="font-family:{FONT_HEADING};font-size:22px;color:{ASH_BLACK};margin-bottom:6px">Example personalized digest</div>
    <div style="font-family:{FONT_BODY};font-size:13px;color:{WARM_GREY};margin-bottom:12px">How one subscriber will see their email.</div>
    <div style="border:1px solid {CARD_BORDER};border-radius:6px;padding:20px;background:white">
      {example_digest_html}
    </div>
  </td></tr>"""

    html_body = (
        _html_head(f"Preview: arXiv Digest {week_iso}")
        + _pine_header_bar("\U0001f52d arXiv Digest \u2014 Preview", week_iso)
        + f"""
  <tr><td style="padding:28px 28px 8px">
    {sub_line_html}

    <!-- CANCEL BUTTON -->
    <div style="margin:0 0 24px 0;padding:20px;background:{GOLD_WASH};border:1px solid {GOLD};border-radius:6px">
      <div style="font-family:{FONT_HEADING};font-size:18px;color:{ASH_BLACK};margin-bottom:12px">Cancel Monday send?</div>
      <a href="{_h(cancel_url)}"
         style="display:inline-block;padding:12px 28px;background:#C0392B;color:white;
                text-decoration:none;border-radius:4px;font-family:{FONT_BODY};
                font-size:15px;font-weight:600;letter-spacing:0.03em">
        CANCEL MONDAY SEND
      </a>
      <div style="margin:10px 0 0;font-family:{FONT_MONO};font-size:11px;color:{WARM_GREY}">
        This link expires in 48 hours. After cancelling you can re-run prep manually or wait for next week.
      </div>
    </div>

    <!-- STATS -->
    <div style="font-family:{FONT_BODY};font-size:13px;color:{WARM_GREY};margin-bottom:8px">
      <strong style="color:{ASH_BLACK}">{len(papers)}</strong> papers fetched &middot;
      <strong style="color:{ASH_BLACK}">{subscriber_count}</strong> subscriber{'s' if subscriber_count != 1 else ''}
    </div>
  </td></tr>"""
        + _section_divider("Subscriber breakdown")
        + f"""
  <tr><td style="padding:8px 28px 20px">
    <table cellpadding="0" cellspacing="0" border="0">{breakdown_rows}</table>
  </td></tr>"""
        + _section_divider(f"Top {len(top_papers)} papers by global relevance")
        + f"""
  <tr><td style="padding:8px 28px 24px">
    {paper_cards_html}
  </td></tr>"""
        + example_section
        + f"""
  <!-- FOOTER -->
  <tr><td style="padding:20px 28px;border-top:1px solid {CARD_BORDER};background:{FOOTER_BG}">
    <div style="font-family:{FONT_MONO};font-size:11px;color:{WARM_GREY}">
      <a href="{_h(logs_url)}" style="color:{PINE_LIGHT};text-decoration:none">View Cloud Function logs</a>
      &nbsp;&middot;&nbsp;
      This is an automated preview — only you receive this copy.
    </div>
  </td></tr>"""
        + _html_close()
    )

    text_top = "\n".join(
        f"{i+1}. {p.get('title', '')} (score: {p.get('global_score', 0):.1f})\n   {p.get('url', '')}"
        for i, p in enumerate(top_papers)
    )
    breakdown_text = "\n".join(
        f"  {t}: {c}" for t, c in sorted(topic_breakdown.items(), key=lambda x: -x[1])
    )
    text_body = f"""arXiv Digest \u2014 Weekly Preview
{week_iso}

{sub_line_text}

CANCEL MONDAY SEND: {cancel_url}

{len(papers)} papers fetched

Subscriber breakdown:
{breakdown_text}

Top {len(top_papers)} papers:
{text_top}

Logs: {logs_url}
"""

    return subject, html_body, text_body


# ─────── Static pages (unsubscribe / manage / cancel confirmation) ────────


def build_unsubscribe_page(signup_url: str = "https://silkedainese.github.io/arxiv-digest") -> str:
    """Return HTML page shown after successful unsubscribe."""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Unsubscribed</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
    body {{ font-family: Georgia, serif; max-width: 480px; margin: 80px auto; padding: 20px; color: #333; text-align: center; }}
    h1 {{ font-size: 20px; }}
    a {{ color: {PINE}; }}
  </style>
</head>
<body>
  <h1>You've been removed.</h1>
  <p>You'll no longer receive Silke's arXiv Digest.</p>
  <p>Changed your mind? <a href="{_h(signup_url)}">Sign up again.</a></p>
</body>
</html>"""


def build_manage_page(
    current_topics: list[str],
    all_topics: dict[str, str],
    manage_token: str,
    manage_url: str,
) -> str:
    """Return HTML manage-topics page with checkboxes."""
    checkboxes = ""
    for topic_id, topic_label in all_topics.items():
        checked = "checked" if topic_id in current_topics else ""
        checkboxes += f"""
    <label style="display:block;margin:8px 0;font-size:15px;">
      <input type="checkbox" name="topics" value="{_h(topic_id)}" {checked}
             style="margin-right:8px;">
      {_h(topic_label)}
    </label>"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Manage your arXiv Digest topics</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
    body {{ font-family: Georgia, serif; max-width: 480px; margin: 60px auto; padding: 20px; color: #333; }}
    h1 {{ font-size: 20px; }}
    button {{ padding: 10px 24px; background: {PINE}; color: white; border: none;
              border-radius: 4px; font-size: 15px; cursor: pointer; margin-top: 16px; }}
    button:hover {{ background: {PINE_LIGHT}; }}
  </style>
</head>
<body>
  <h1>Manage your arXiv Digest topics</h1>
  <p>Select the topics you'd like to receive papers for each week.</p>
  <form method="POST" action="{_h(manage_url)}">
    <input type="hidden" name="t" value="{_h(manage_token)}">
    {checkboxes}
    <button type="submit">Save topics</button>
  </form>
</body>
</html>"""


def build_manage_confirmation_page() -> str:
    """Return HTML shown after successful topic update."""
    return """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Topics updated</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
    body { font-family: Georgia, serif; max-width: 480px; margin: 80px auto; padding: 20px; color: #333; text-align: center; }
  </style>
</head>
<body>
  <h1>Topics updated.</h1>
  <p>You'll receive papers matching your new selection from next Monday.</p>
</body>
</html>"""


def build_cancel_confirmation_page(week_iso: str) -> str:
    """Return HTML shown after cancelling the Monday send."""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Send cancelled</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
    body {{ font-family: Georgia, serif; max-width: 480px; margin: 80px auto; padding: 20px; color: #333; text-align: center; }}
  </style>
</head>
<body>
  <h1>Monday send cancelled for {_h(week_iso)}.</h1>
  <p>Nothing will go out this week. Re-run prep manually via Cloud Console or wait for next week's scheduled run.</p>
</body>
</html>"""
