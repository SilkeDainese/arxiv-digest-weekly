"""HTML and plaintext email template builders.

All templates are self-contained Python string templates — no Jinja2 dependency.
This keeps the Cloud Functions package small and avoids template injection risk.
"""
from __future__ import annotations

import html as html_mod
from typing import Any, Optional


def _h(text: str) -> str:
    """HTML-escape a string."""
    return html_mod.escape(str(text))


def _paper_html(paper: dict[str, Any], show_score: bool = False) -> str:
    """Render a single paper as an HTML block."""
    title = _h(paper.get("title", "Untitled"))
    authors = paper.get("authors", [])
    author_str = _h(", ".join(authors[:5]) + (" et al." if len(authors) > 5 else ""))
    abstract = _h(paper.get("abstract", ""))
    url = _h(paper.get("url", "#"))
    pdf_url = _h(paper.get("pdf_url", "#"))

    score_line = ""
    if show_score:
        score = paper.get("subscriber_score", paper.get("global_score", 0))
        score_line = f'<p style="color:#888;font-size:12px;margin:4px 0 0 0;">Relevance score: {score:.1f}</p>'

    return f"""
<div style="margin:24px 0;padding:20px;background:#f9f9f9;border-left:3px solid #1a6fa8;border-radius:4px;">
  <h3 style="margin:0 0 8px 0;font-size:16px;line-height:1.4;">
    <a href="{url}" style="color:#1a6fa8;text-decoration:none;">{title}</a>
  </h3>
  <p style="color:#555;font-size:13px;margin:0 0 10px 0;">{author_str}</p>
  <p style="color:#333;font-size:14px;line-height:1.6;margin:0 0 10px 0;">{abstract}</p>
  {score_line}
  <p style="margin:8px 0 0 0;">
    <a href="{url}" style="color:#1a6fa8;font-size:13px;margin-right:12px;">Abstract</a>
    <a href="{pdf_url}" style="color:#1a6fa8;font-size:13px;">PDF</a>
  </p>
</div>"""


def _paper_text(paper: dict[str, Any]) -> str:
    """Render a single paper as plaintext."""
    title = paper.get("title", "Untitled")
    authors = paper.get("authors", [])
    author_str = ", ".join(authors[:5]) + (" et al." if len(authors) > 5 else "")
    abstract = paper.get("abstract", "")
    url = paper.get("url", "#")
    return f"""
{title}
{author_str}
{abstract}
{url}
"""


def build_personalized_digest_email(
    papers: list[dict[str, Any]],
    subscriber_topics: list[str],
    week_iso: str,
    unsubscribe_url: str,
    manage_url: str,
) -> tuple[str, str, str]:
    """Build a personalized digest email.

    Returns:
        (subject, html_body, text_body)
    """
    topic_display = ", ".join(t.replace("_", " ").title() for t in subscriber_topics)
    paper_count = len(papers)
    subject = f"arXiv Digest — {week_iso} — {paper_count} papers ({topic_display})"

    footer_html = f"""
<div style="margin-top:40px;padding:20px 0;border-top:1px solid #ddd;color:#888;font-size:12px;">
  <p>
    You're receiving this because you signed up for Silke's arXiv Digest.<br>
    Topics: {_h(topic_display)}<br>
    Week: {_h(week_iso)}
  </p>
  <p>
    <a href="{_h(manage_url)}" style="color:#1a6fa8;">Manage your topics</a> &nbsp;|&nbsp;
    <a href="{_h(unsubscribe_url)}" style="color:#888;">Unsubscribe</a>
  </p>
</div>"""

    paper_blocks = "".join(_paper_html(p) for p in papers) if papers else (
        "<p style='color:#888'>No new papers matched your topics this week.</p>"
    )

    html_body = f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"><title>arXiv Digest {_h(week_iso)}</title></head>
<body style="font-family:Georgia,serif;max-width:680px;margin:0 auto;padding:20px;color:#333;">
  <h1 style="font-size:22px;margin-bottom:4px;">arXiv Digest</h1>
  <p style="color:#888;margin-top:0;">{_h(week_iso)} &middot; {paper_count} paper{"s" if paper_count != 1 else ""}</p>
  <p>Your topics this week: <strong>{_h(topic_display)}</strong></p>
  {paper_blocks}
  {footer_html}
</body>
</html>"""

    text_papers = "\n".join(_paper_text(p) for p in papers) if papers else (
        "No new papers matched your topics this week."
    )
    text_body = f"""arXiv Digest — {week_iso}
Your topics: {topic_display}

{text_papers}

---
Manage topics: {manage_url}
Unsubscribe: {unsubscribe_url}
"""

    return subject, html_body, text_body


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

    Returns:
        (subject, html_body, text_body)
    """
    top_papers = papers[:10]
    subject = f"[Preview] arXiv digest going out Monday — {len(papers)} papers, {subscriber_count} subscribers"

    breakdown_rows = "".join(
        f"<tr><td style='padding:4px 12px 4px 0;'>{_h(t.replace('_',' ').title())}</td>"
        f"<td style='padding:4px 0;'>{c} subscriber{'s' if c != 1 else ''}</td></tr>"
        for t, c in sorted(topic_breakdown.items(), key=lambda x: -x[1])
    )

    top_paper_blocks = "".join(_paper_html(p, show_score=True) for p in top_papers)

    example_section = ""
    if example_digest_html:
        example_section = f"""
<h2 style="margin-top:40px;">Example personalized digest</h2>
<p style="color:#888;font-size:13px;">This is how one subscriber will see their email.</p>
<div style="border:1px solid #ddd;padding:20px;border-radius:4px;">
{example_digest_html}
</div>"""

    html_body = f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"><title>Preview: arXiv Digest {_h(week_iso)}</title></head>
<body style="font-family:Georgia,serif;max-width:680px;margin:0 auto;padding:20px;color:#333;">

  <h1 style="font-size:22px;">arXiv Digest — Weekly Preview</h1>
  <p style="color:#888;">{_h(week_iso)}</p>

  <p style="font-size:15px;">
    <strong>{len(papers)}</strong> papers fetched &middot;
    <strong>{subscriber_count}</strong> subscribers will receive a digest Monday 07:00 CET.
  </p>

  <div style="margin:24px 0;padding:20px;background:#fff3cd;border:1px solid #ffc107;border-radius:4px;">
    <p style="margin:0 0 12px 0;font-size:15px;font-weight:bold;">Cancel Monday send?</p>
    <a href="{_h(cancel_url)}"
       style="display:inline-block;padding:12px 24px;background:#dc3545;color:white;
              text-decoration:none;border-radius:4px;font-size:15px;font-weight:bold;">
      CANCEL MONDAY SEND
    </a>
    <p style="margin:12px 0 0 0;font-size:12px;color:#888;">
      This link expires in 48 hours. After cancelling you can re-run prep manually or wait for next week.
    </p>
  </div>

  <h2>Subscriber breakdown</h2>
  <table style="border-collapse:collapse;">{breakdown_rows}</table>

  <h2>Top 10 papers (by global relevance)</h2>
  {top_paper_blocks}

  {example_section}

  <div style="margin-top:40px;padding:16px;background:#f9f9f9;border-radius:4px;">
    <p style="margin:0;font-size:13px;color:#555;">
      <a href="{_h(logs_url)}" style="color:#1a6fa8;">View Cloud Function logs</a>
    </p>
  </div>

</body>
</html>"""

    text_top = "\n".join(
        f"{i+1}. {p.get('title','')} (score: {p.get('global_score',0):.1f})\n   {p.get('url','')}"
        for i, p in enumerate(top_papers)
    )
    text_body = f"""arXiv Digest — Weekly Preview
{week_iso}

{len(papers)} papers | {subscriber_count} subscribers

CANCEL MONDAY SEND: {cancel_url}

Subscriber breakdown:
{chr(10).join(f"  {t}: {c}" for t, c in sorted(topic_breakdown.items(), key=lambda x: -x[1]))}

Top 10 papers:
{text_top}

Logs: {logs_url}
"""

    return subject, html_body, text_body


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
    a {{ color: #1a6fa8; }}
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
    button {{ padding: 10px 24px; background: #1a6fa8; color: white; border: none;
              border-radius: 4px; font-size: 15px; cursor: pointer; margin-top: 16px; }}
    button:hover {{ background: #145a8a; }}
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
