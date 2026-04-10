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

import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from typing import Any

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


def _fetch_xml(url: str) -> str | None:
    """Fetch XML from arXiv API. Returns None on error."""
    try:
        with urllib.request.urlopen(url, timeout=30) as resp:
            return resp.read().decode("utf-8")
    except (urllib.error.URLError, OSError) as exc:
        # Log the category/url, not any user data
        print(f"[arxiv_fetcher] HTTP error fetching {url[:80]}: {exc}")
        return None


def _parse_xml(xml_data: str, cutoff: datetime) -> list[dict[str, Any]]:
    """Parse arXiv Atom feed, filtering to papers submitted after cutoff."""
    ns = {"atom": "http://www.w3.org/2005/Atom"}
    try:
        root = ET.fromstring(xml_data)
    except ET.ParseError as exc:
        print(f"[arxiv_fetcher] XML parse error: {exc}")
        return []

    papers = []
    for entry in root.findall("atom:entry", ns):
        published_str = (entry.findtext("atom:published", "", ns) or "").strip()
        try:
            published = datetime.fromisoformat(published_str.replace("Z", "+00:00"))
        except ValueError:
            continue

        if published < cutoff:
            continue

        arxiv_id_raw = (entry.findtext("atom:id", "", ns) or "").strip()
        arxiv_id = arxiv_id_raw.split("/abs/")[-1] if "/abs/" in arxiv_id_raw else arxiv_id_raw

        title = (entry.findtext("atom:title", "", ns) or "").strip().replace("\n", " ")
        abstract = (entry.findtext("atom:summary", "", ns) or "").strip().replace("\n", " ")
        authors = [
            (a.findtext("atom:name", "", ns) or "").strip()
            for a in entry.findall("atom:author", ns)
        ]

        papers.append({
            "id": arxiv_id,
            "title": title,
            "abstract": abstract,
            "authors": authors,
            "published": published.isoformat(),
            "url": f"https://arxiv.org/abs/{arxiv_id}",
            "pdf_url": f"https://arxiv.org/pdf/{arxiv_id}",
        })

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


def build_personalized_digest(
    papers: list[dict[str, Any]],
    subscriber_topics: list[str],
    max_papers: int = 15,
) -> list[dict[str, Any]]:
    """Filter and rank papers for a specific subscriber's topic list.

    Args:
        papers: Full weekly paper list (from fetch_weekly_papers).
        subscriber_topics: List of topic IDs the subscriber selected.
        max_papers: Maximum papers to include (default 15).

    Returns:
        Sorted list of papers with 'subscriber_score' field added,
        limited to max_papers, score > 0 only.
    """
    scored = []
    for paper in papers:
        score = score_paper_for_topics(paper, subscriber_topics)
        if score > 0:
            p = dict(paper)
            p["subscriber_score"] = score
            scored.append(p)

    scored.sort(key=lambda p: p["subscriber_score"], reverse=True)
    return scored[:max_papers]
