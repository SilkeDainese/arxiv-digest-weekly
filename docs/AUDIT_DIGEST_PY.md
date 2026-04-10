# Audit: digest.py feature parity for arxiv-digest-weekly

Audited: `~/Projects/arxiv-digest/digest.py` (~2546 lines)
Date: 2026-04-10
Auditor: Snakes

This audit covers every algorithm/data-processing feature in the old pipeline that
materially affects what the reader sees. Status is assessed against the current state
of `arxiv-digest-weekly` before this sprint.

---

## 1. Scoring cascade

| Feature | Description | Status in weekly | Recommendation |
|---------|-------------|-----------------|----------------|
| Claude primary scoring | `_analyse_with_claude()` — calls Anthropic API, parses JSON response with relevance_score + metadata | Missing | **Port** — fetch `anthropic-api-key` from Secret Manager, fall through on missing/error |
| Gemini fallback (API key) | `_analyse_with_gemini_api()` — calls `google-generativeai` client with `gemini-api-key` secret | Missing | **Port** — fetch `gemini-api-key` from Secret Manager, fall through on missing/error |
| Vertex AI Gemini | `_analyse_with_vertex_gemini()` — ADC auth, no API key, GCP project context | Missing | **Simplify** — Cloud Functions on GCP have ADC automatically; include as second tier after Claude |
| Keyword fallback | `_fallback_analyse()` — pure keyword scoring, no AI | Present (partial) | **Port** — current implementation scores 0–100 but doesn't produce `plain_summary` or `highlight_phrase`, only global relevance score |
| Cascade dispatcher | `analyse_papers()` — tries Claude → Vertex Gemini → Gemini API → keyword, returns `(papers, scoring_method)` | Missing | **Port** — new `ai_scorer.py` module |
| Consecutive-failure bail | 3 consecutive failures → abandon that tier and try next | Missing | **Port** — essential for graceful degradation |
| Credit/billing error detection | Detect "credit balance" in Claude error → immediately cascade, don't retry | Missing | **Port** |

---

## 2. AI rewriting

| Feature | Description | Status in weekly | Recommendation |
|---------|-------------|-----------------|----------------|
| `plain_summary` | 2–3 sentence peer-to-peer summary: lead with result/method, never start with "Researchers/The authors/This paper/A team/Scientists", assume domain knowledge | Missing | **Port** — embed in scoring prompt |
| `highlight_phrase` | Punchy 5–8 word headline for a paper | Missing | **Port** — embed in scoring prompt |
| `why_interesting` | 1–2 sentences on why relevant to this researcher | Missing | **Simplify** — for student digest, drop personalized "why_interesting" (no per-student context); keep field as optional |
| `emoji` | One relevant emoji from AI | Missing | **Drop** — adds noise for student digest, not worth API tokens |
| `kw_tags` | 1–3 short keyword tags | Missing | **Drop** — student digest doesn't render these tags |
| `method_tags` | 1–3 method tags | Missing | **Drop** |
| `is_new_catalog` | Boolean flag for catalog papers | Missing | **Drop** |
| `cite_worthy` | Boolean flag | Missing | **Drop** |
| `new_result` | 2–4 word result tag | Missing | **Drop** |
| `relevance_score` | Integer 1–10 from AI, used for ranking and display | Missing | **Port** — stored as `ai_score`, feeds ranking |
| Score tier tracking | `score_tier` field: "ai" or "keyword" — lets email template show scoring method | Missing | **Port** — add `score_tier` field to each paper |

---

## 3. Title processing

| Feature | Description | Status in weekly | Recommendation |
|---------|-------------|-----------------|----------------|
| LaTeX stripping from titles | `_strip_latex()` — removes `$x$`, `\cmd{x}`, `_{sub}`, etc. | Missing | **Port** — titles with raw LaTeX look broken in email |
| Title shortening | `_short_title(title, max_len=105)` — truncates at word boundary, strips LaTeX first | Present (partial, max_len=100, no LaTeX strip) | **Port** — update existing `_short_title` to strip LaTeX first, widen to 105 |

---

## 4. Abstract processing

| Feature | Description | Status in weekly | Recommendation |
|---------|-------------|-----------------|----------------|
| LaTeX stripping from abstracts | `_strip_latex()` applied before AI sees abstract | Missing | **Port** — pass clean abstract to AI prompts |
| `_one_sentence()` condenser | Returns first sentence-like chunk (≤180 chars) from `plain_summary`, strips LaTeX, collapses comma artifacts | Missing | **Port** — used in card rendering |

---

## 5. Digest modes

| Feature | Description | Status in weekly | Recommendation |
|---------|-------------|-----------------|----------------|
| Highlights mode | max_papers=6, min_score=5 | Not applicable | **Drop** — weekly uses per-subscriber topic scoring, not a global mode |
| In-depth mode | max_papers=15, min_score=2 | Not applicable | **Drop** — same reason |
| Student card mode | `_render_student_paper_card()` — 4-line format: category, DM Serif title, one-sentence summary, meta | Present (partial, renders raw abstract not `plain_summary`) | **Port** — update to render `plain_summary` and `highlight_phrase` |
| Deep-read card | `_render_paper_card()` — full card with score bar, why_interesting box, feedback links | Not applicable | **Drop** — student digest uses student card only |
| 5-min skim card | `_render_skim_card()` | Not applicable | **Drop** |

---

## 6. Ranking and selection

| Feature | Description | Status in weekly | Recommendation |
|---------|-------------|-----------------|----------------|
| Filter by min_score | Drop papers below threshold | Missing | **Port** — for AI-scored papers, drop score < 3 before sending |
| Sort by relevance_score + feedback_bias | Primary sort key | Missing | **Simplify** — sort by `ai_score` (from AI) or `subscriber_score` (keyword) |
| Cap at max_papers | Enforce a hard per-subscriber cap | Present (max_papers=15) | Keep |
| `pre_filter()` | Before AI: take top 30 by keyword+author match, or discovery mode | Not applicable | **Drop** — weekly uses full paper set (volume differs); keyword pre-filter is already `build_personalized_digest()` |
| Category diversification | Not explicit in old pipeline, uses sort only | Not applicable | **Drop** |

---

## 7. Delight / badge logic

| Feature | Description | Status in weekly | Recommendation |
|---------|-------------|-----------------|----------------|
| `detect_delights()` | AU affiliation detection + keyword-based cultural notes, max 2 per email | Missing | **Port (simplified)** — AU affiliation is relevant to students; drop keyword-based delight notes as they require `DELIGHT_KEYWORDS` config |
| AU researcher detection | Checks author affiliation XML for Aarhus University patterns | Missing | **Port** — the arXiv XML has affiliation data; add to `_parse_xml()` |
| `detect_au_researchers()` | Flags `is_au_researcher`, `au_researcher_authors` on each paper | Missing | **Port** — render as a subtle "AU researcher" badge in the student card |
| `is_own_paper` | Self-match against author list | Not applicable | **Drop** — student digest has no single owner |
| Colleague post-it section | Renders colleague papers in a gold post-it grid | Not applicable | **Drop** — not relevant for student digest |
| `research_author` detection | Known researcher boost | Not applicable | **Drop** |
| `feedback_bias` | GitHub issue feedback → keyword preference adjustment | Not applicable | **Drop** — Cloud Functions pipeline, no GitHub integration needed |

---

## 8. Colleague / research_author detection

| Feature | Description | Status in weekly | Recommendation |
|---------|-------------|-----------------|----------------|
| Colleague people match | Author name pattern matching against configured people list | Not applicable | **Drop** |
| Institutional match | Affiliation XML + abstract fallback for institution match | AU affiliation only | **Port (AU only)** — check for Aarhus University in affiliation XML |
| `known_authors` / research_author | Boost scoring for known researchers | Not applicable | **Drop** |
| Colleague author search | Separate arXiv `au:` query for each colleague | Not applicable | **Drop** |

---

## 9. Other material features

| Feature | Description | Status in weekly | Recommendation |
|---------|-------------|-----------------|----------------|
| Scoring method notice banner | Shows "scored by Claude / Gemini / keywords" in email | Missing | **Port (simplified)** — add `score_tier` to paper, render small notice in preview email footer |
| `_score_bar()` | 10-dot relevance visualizer | Not applicable | **Drop** |
| Feedback links | GitHub issue quick-feedback links per card | Not applicable | **Drop** |
| Journal ref display | Shows journal reference if available | Missing | **Drop** — low value for students |
| LaTeX strip applied to AI outputs | Cleans up any LaTeX the AI regenerates in summaries | Missing | **Port** — apply `_strip_latex` to `plain_summary` and `highlight_phrase` after AI returns |
| Markdown fence stripping | Strip ` ```json ``` ` wrappers if AI returns markdown | Missing | **Port** |
| Concurrent paper scoring | `ThreadPoolExecutor(max_workers=5)` — parallelizes API calls | Missing | **Port** — reduces cold-start latency significantly for 20+ papers |

---

## Summary

**Port:** scoring cascade (Claude → Vertex Gemini → Gemini API → keyword), `plain_summary`, `highlight_phrase`, `_strip_latex`, `_short_title` (update), `_one_sentence`, AU affiliation detection, score_tier field, concurrent scoring, consecutive-failure bail, markdown fence stripping.

**Simplify:** keyword fallback (already exists, needs `plain_summary`/`highlight_phrase` fields added), student card render (update to use `plain_summary`).

**Drop:** `why_interesting`, emoji, kw/method/is_new_catalog/cite_worthy/new_result tags, deep-read/skim cards, colleague section, own-paper section, GitHub feedback, `pre_filter` (weekly uses full set), feedback_bias, journal_ref, scoring mode banners (keep only score_tier field), `_score_bar`.

**Secret Manager entries needed:**
- `anthropic-api-key` — Anthropic API key for Claude Haiku scoring
- `gemini-api-key` — Google AI API key for Gemini fallback (Vertex ADC is automatic on GCP)

---

*Full port implemented in `shared/ai_scorer.py` and `shared/quality_gate.py`. Integration wired in `shared/email_builder.py` and `functions/mailer/main.py`.*
