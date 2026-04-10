"""arXiv paper fetching and scoring for the weekly digest.

Fetches papers by arXiv category and/or keyword search,
scores them against a subscriber's topic list, and returns
ranked results.

Adapted from ~/Projects/arxiv-digest/digest.py (Silke S. Dainese, 2025)
with modifications for the Cloud Functions architecture:
  - No config.yaml dependency (topics passed directly)
  - No SMTP/email logic (handled separately)
  - No AI scoring (keyword-only for reliability and cost)
  - Returns structured dicts ready for Firestore storage
"""
from __future__ import annotations

import logging
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger(__name__)

# A minimal valid Atom feed used in tests for a known-clean XML input.
_ATOM_FEED_WITH_CATEGORY = """\
<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:arxiv="http://arxiv.org/schemas/atom">
  <entry>
    <id>http://arxiv.org/abs/2501.12345v1</id>
    <published>2026-04-07T00:00:00Z</published>
    <title>Stellar evolution in close binary systems</title>
    <summary>Long enough abstract to pass any filter. Radial velocity measurements
    of binary stars reveal mass transfer patterns consistent with stellar evolution models.</summary>
    <author><name>Smith J</name></author>
    <arxiv:primary_category xmlns:arxiv="http://arxiv.org/schemas/atom"
      term="astro-ph.SR" scheme="http://arxiv.org/schemas/atom"/>
  </entry>
</feed>
"""

# arXiv categories relevant to AU astronomy students
STUDENT_CATEGORIES = [
    "astro-ph.EP",   # Earth and Planetary Astrophysics
    "astro-ph.SR",   # Solar and Stellar Astrophysics
    "astro-ph.GA",   # Astrophysics of Galaxies
    "astro-ph.CO",   # Cosmology and Nongalactic Astrophysics
    "astro-ph.HE",   # High Energy Astrophysical Phenomena
    "astro-ph.IM",   # Instrumentation and Methods
]

# Topic → keyword mapping used for scoring
TOPIC_KEYWORDS: dict[str, list[str]] = {
    "stars": [
        "stellar", "star formation", "main sequence", "giant star", "dwarf star",
        "spectroscopy", "radial velocity", "rotation", "magnetic activity",
        "chromosphere", "photosphere", "convection zone", "stellar evolution",
        "binary star", "mass transfer",
    ],
    "exoplanets": [
        "exoplanet", "transiting planet", "hot Jupiter", "super-Earth",
        "habitable zone", "atmospheric characterization", "TESS", "Kepler",
        "transit spectroscopy", "radial velocity", "planetary system",
        "orbital dynamics", "disk-planet interaction",
    ],
    "galaxies": [
        "galaxy formation", "galactic structure", "Milky Way", "spiral galaxy",
        "elliptical galaxy", "AGN", "active galactic nucleus", "quasar",
        "interstellar medium", "ISM", "star formation rate", "metallicity",
        "dark matter halo", "galaxy cluster",
    ],
    "cosmology": [
        "cosmological model", "dark energy", "dark matter", "CMB",
        "cosmic microwave background", "large scale structure", "Hubble constant",
        "gravitational lensing", "baryon acoustic", "inflation", "sigma8",
    ],
    "high_energy": [
        "neutron star", "black hole", "pulsar", "magnetar", "gamma-ray burst",
        "X-ray binary", "gravitational wave", "LIGO", "Virgo", "supernovae",
        "supernova remnant", "accretion disk", "relativistic jet",
    ],
    "instrumentation": [
        "spectrograph", "photometer", "telescope design", "detector",
        "CCD", "adaptive optics", "interferometry", "VLBI", "survey",
        "data reduction", "calibration", "pipeline", "instrument design",
    ],
    "solar_helio": [
        "solar wind", "solar flare", "coronal mass ejection", "heliosphere",
        "sunspot", "solar cycle", "corona", "chromosphere", "solar activity",
        "space weather",
    ],
    "methods_ml": [
        "machine learning", "neural network", "deep learning", "classification",
        "Gaussian process", "Bayesian inference", "MCMC", "clustering",
        "dimensionality reduction", "random forest", "convolutional",
    ],
}

# Number of papers to fetch per category per run
RESULTS_PER_CATEGORY = 100
# Look-back window: papers from this many days ago
DAYS_BACK = 7


def _build_arxiv_url(category: str) -> str:
    params = {
        "search_query": f"cat:{category}",
        "start": 0,
        "max_results": RESULTS_PER_CATEGORY,
        "sortBy": "submittedDate",
        "sortOrder": "descending",
    }
    return "https://export.arxiv.org/api/query?" + urllib.parse.urlencode(params)


_USER_AGENT = "arxiv-digest-weekly/1.0 (mailto:silke.dainese@gmail.com)"


def _fetch_xml(url: str) -> str | None:
    """Fetch XML from arXiv API with a compliant User-Agent. Returns None on error.

    arXiv ToS requires a descriptive User-Agent identifying the client and
    providing a contact address so they can reach out if there are issues.

    HTTP 429 (rate limited) is retried up to 3 times with exponential backoff:
      attempt 1 → wait 10s, attempt 2 → wait 20s, attempt 3 → return None.
    Other HTTP errors and network errors are not retried.
    """
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    _MAX_RETRIES = 3
    for attempt in range(_MAX_RETRIES):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            if exc.code == 429 and attempt < _MAX_RETRIES - 1:
                wait = 10 * (2 ** attempt)   # 10s, 20s
                print(f"[arxiv_fetcher] Rate limited (429) on {url[:80]} — retrying in {wait}s (attempt {attempt + 1}/{_MAX_RETRIES - 1})")
                time.sleep(wait)
                continue
            # 429 after all retries exhausted, or non-429 HTTP error
            if exc.code == 429:
                print(f"[arxiv_fetcher] Rate limited (429) on {url[:80]} — giving up after {_MAX_RETRIES} attempts")
            else:
                print(f"[arxiv_fetcher] HTTP error {exc.code} fetching {url[:80]}: {exc}")
            return None
        except (urllib.error.URLError, OSError) as exc:
            print(f"[arxiv_fetcher] Network error fetching {url[:80]}: {exc}")
            return None
    return None


# AU affiliation patterns (case-insensitive match)
_AU_AFFILIATION_PATTERNS = [
    "aarhus university",
    "aarhus universitet",
    "phys.au.dk",
    "au.dk",
    "department of physics and astronomy, aarhus",
]


def _is_au_affiliation(affiliation: str) -> bool:
    """Return True if the affiliation string matches Aarhus University."""
    aff_lower = affiliation.lower()
    return any(pat in aff_lower for pat in _AU_AFFILIATION_PATTERNS)


def _parse_xml(xml_data: str, cutoff: datetime) -> list[dict[str, Any]]:
    """Parse arXiv Atom feed, filtering to papers submitted after cutoff.

    Extracts arxiv:primary_category into the 'category' field so every paper
    shows its real sub-category (e.g. "astro-ph.SR") rather than falling back
    to the generic "astro-ph" query category.

    Also extracts AU affiliation data: au_authors is a list of author names
    whose arxiv:affiliation element matches Aarhus University patterns.

    Malformed entry tracking: if >= 3 entries are malformed (or all entries
    are malformed), logs a WARNING about possible arXiv API format change.
    """
    ns = {"atom": "http://www.w3.org/2005/Atom"}
    # arXiv-specific XML namespace for primary_category and affiliations
    ARXIV_NS = "http://arxiv.org/schemas/atom"
    NS_ARXIV = {"arxiv": ARXIV_NS}

    try:
        root = ET.fromstring(xml_data)
    except ET.ParseError as exc:
        print(f"[arxiv_fetcher] XML parse error: {exc}")
        return []

    all_entries = root.findall("atom:entry", ns)
    total_entries = len(all_entries)
    malformed_count = 0
    papers = []

    for entry in all_entries:
        published_str = (entry.findtext("atom:published", "", ns) or "").strip()
        try:
            published = datetime.fromisoformat(published_str.replace("Z", "+00:00"))
        except ValueError:
            malformed_count += 1
            continue

        if published < cutoff:
            continue

        arxiv_id_raw = (entry.findtext("atom:id", "", ns) or "").strip()
        arxiv_id = arxiv_id_raw.split("/abs/")[-1] if "/abs/" in arxiv_id_raw else arxiv_id_raw

        # An entry without a usable arXiv ID is malformed — skip it.
        if not arxiv_id:
            malformed_count += 1
            continue

        title = (entry.findtext("atom:title", "", ns) or "").strip().replace("\n", " ")
        abstract = (entry.findtext("atom:summary", "", ns) or "").strip().replace("\n", " ")
        authors = [
            (a.findtext("atom:name", "", ns) or "").strip()
            for a in entry.findall("atom:author", ns)
        ]

        # Extract the paper's actual primary category (e.g. "astro-ph.SR").
        # The reference implementation (~/Projects/arxiv-digest/digest.py) uses
        # the {http://arxiv.org/schemas/atom}primary_category element for this.
        primary_cat_el = entry.find(f"{{{ARXIV_NS}}}primary_category")
        category = (
            primary_cat_el.get("term", "")
            if primary_cat_el is not None
            else ""
        )

        # AU affiliation parsing — per-author arxiv:affiliation elements.
        # au_authors is a list of author names affiliated with Aarhus University.
        au_authors: list[str] = []
        for author_el in entry.findall("atom:author", ns):
            author_name = (author_el.findtext("atom:name", "", ns) or "").strip()
            affs = [
                aff_el.text or ""
                for aff_el in author_el.findall(f"{{{ARXIV_NS}}}affiliation")
                if aff_el.text
            ]
            if author_name and any(_is_au_affiliation(aff) for aff in affs):
                au_authors.append(author_name)

        papers.append({
            "id": arxiv_id,
            "title": title,
            "abstract": abstract,
            "authors": authors,
            "published": published.isoformat(),
            "url": f"https://arxiv.org/abs/{arxiv_id}",
            "pdf_url": f"https://arxiv.org/pdf/{arxiv_id}",
            "category": category,
            "au_authors": au_authors,
        })

    # Malformed entry warning: flag possible arXiv API format change.
    # Threshold: >= 3 malformed, or all entries were malformed.
    if total_entries > 0 and (
        malformed_count >= 3 or (malformed_count > 0 and malformed_count == total_entries)
    ):
        logger.warning(
            "[arxiv_fetcher] %d of %d entries malformed — arXiv API format may have changed",
            malformed_count,
            total_entries,
        )

    return papers


def score_paper_for_topics(paper: dict[str, Any], topics: list[str]) -> float:
    """Score a paper against a list of topic strings.

    Returns a score 0.0–100.0. Higher = more relevant.
    Checks title (2x weight) and abstract (1x weight).
    """
    text_title = paper.get("title", "").lower()
    text_abstract = paper.get("abstract", "").lower()

    total_weight = 0.0
    hit_weight = 0.0

    for topic in topics:
        keywords = TOPIC_KEYWORDS.get(topic, [topic.lower().split("_")])
        for kw in keywords:
            total_weight += 3.0  # 2 for title + 1 for abstract slots
            if kw.lower() in text_title:
                hit_weight += 2.0
            if kw.lower() in text_abstract:
                hit_weight += 1.0

    if total_weight == 0:
        return 0.0

    return round(100.0 * hit_weight / total_weight, 1)


def fetch_weekly_papers() -> list[dict[str, Any]]:
    """Fetch all papers from the past DAYS_BACK days across all student categories.

    Returns a deduplicated list of paper dicts with no per-subscriber scoring.
    Scoring happens later in build_personalized_digest().
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=DAYS_BACK)
    seen_ids: set[str] = set()
    all_papers: list[dict[str, Any]] = []

    for i, category in enumerate(STUDENT_CATEGORIES):
        if i > 0:
            time.sleep(3)  # arXiv rate-limiting etiquette

        url = _build_arxiv_url(category)
        xml_data = _fetch_xml(url)
        if xml_data is None:
            continue

        papers = _parse_xml(xml_data, cutoff)
        for p in papers:
            if p["id"] not in seen_ids:
                seen_ids.add(p["id"])
                all_papers.append(p)

    return all_papers


def score_papers_for_all_topics(papers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Add a global relevance score to each paper across all student topics.

    The global score is the max score across all topics. Papers are returned
    sorted descending by global score.
    """
    all_topics = list(TOPIC_KEYWORDS.keys())
    for paper in papers:
        paper["global_score"] = score_paper_for_topics(paper, all_topics)

    return sorted(papers, key=lambda p: p["global_score"], reverse=True)


# Max papers sent to AI scoring — caps token usage and avoids Cloud Function timeout.
# Papers are sorted descending by global_score; zero-score papers are excluded first.
_AI_PREFILTER_TOP_N = 50


def pre_filter_for_ai(papers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return the top N papers by global_score for AI scoring.

    Mirrors intent of digest.py::pre_filter() — select the best-scoring papers
    before expensive AI API calls to cap token usage and function runtime.

    Rules:
      - Papers with global_score == 0 are excluded entirely.
      - Remaining papers are sorted descending by global_score (stable sort).
      - At most _AI_PREFILTER_TOP_N (50) are returned.
      - If fewer than 50 non-zero papers exist, all of them are returned.

    Args:
        papers: Globally scored paper list (output of score_papers_for_all_topics).

    Returns:
        Filtered, sorted list — at most 50 papers with global_score > 0.
    """
    nonzero = [p for p in papers if p.get("global_score", 0) > 0]
    nonzero.sort(key=lambda p: p.get("global_score", 0), reverse=True)
    return nonzero[:_AI_PREFILTER_TOP_N]


_AI_SCORE_FLOOR = 3.0  # Papers with ai_score below this are dropped (AI-scored only)


def build_personalized_digest(
    papers: list[dict[str, Any]],
    subscriber_topics: list[str],
    max_papers: int = 15,
) -> list[dict[str, Any]]:
    """Filter and rank papers for a specific subscriber's topic list.

    Args:
        papers: Full weekly paper list (from fetch_weekly_papers), which has
                already been run through score_papers_with_ai() so papers may
                carry 'ai_score' and 'score_tier' fields.
        subscriber_topics: List of topic IDs the subscriber selected.
        max_papers: Maximum papers to include (default 15).

    Returns:
        Sorted list of papers with 'subscriber_score' field added,
        limited to max_papers.

    Ranking rules:
      1. If ANY paper in the scored list has an 'ai_score' field, sort by
         ai_score descending (subscriber_score as tiebreaker).
      2. Otherwise fall back to subscriber_score descending.

    Filtering rules:
      - AI-scored papers (score_tier == 'ai') with ai_score < _AI_SCORE_FLOOR
        are dropped.
      - Keyword-only papers (no ai_score set, or score_tier == 'keyword')
        require subscriber_score > 0 (existing behaviour preserved).
    """
    scored = []
    for paper in papers:
        score = score_paper_for_topics(paper, subscriber_topics)
        if score <= 0:
            # Zero subscriber relevance — skip regardless of ai_score
            continue
        p = dict(paper)
        p["subscriber_score"] = score

        # Apply AI score floor: drop AI-scored papers rated below the floor
        if p.get("score_tier") == "ai" and "ai_score" in p:
            if float(p["ai_score"]) < _AI_SCORE_FLOOR:
                continue

        scored.append(p)

    # Determine sort key: ai_score if ANY paper has it, else subscriber_score
    use_ai_sort = any("ai_score" in p for p in scored)

    if use_ai_sort:
        scored.sort(
            key=lambda p: (float(p.get("ai_score", 0)), p["subscriber_score"]),
            reverse=True,
        )
    else:
        scored.sort(key=lambda p: p["subscriber_score"], reverse=True)

    return scored[:max_papers]
