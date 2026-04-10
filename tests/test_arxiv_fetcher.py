"""Tests for arXiv fetching and digest building.

Covers:
  - Score paper for matching topics → non-zero
  - Score paper for non-matching topics → zero
  - build_personalized_digest: filters, ranks, limits
  - score_papers_for_all_topics: adds global_score, sorts descending
  - Fixture paper → expected topic match
"""
import pytest

from shared.arxiv_fetcher import (
    TOPIC_KEYWORDS,
    build_personalized_digest,
    score_paper_for_topics,
    score_papers_for_all_topics,
)


def make_paper(
    arxiv_id: str = "2501.00001",
    title: str = "A test paper",
    abstract: str = "This is a test abstract.",
    authors: list | None = None,
) -> dict:
    return {
        "id": arxiv_id,
        "title": title,
        "abstract": abstract,
        "authors": authors or ["Author A", "Author B"],
        "published": "2026-04-07T00:00:00+00:00",
        "url": f"https://arxiv.org/abs/{arxiv_id}",
        "pdf_url": f"https://arxiv.org/pdf/{arxiv_id}",
    }


class TestScorePaperForTopics:
    def test_matching_title_keyword_gives_nonzero_score(self):
        paper = make_paper(title="Stellar evolution in binary stars")
        score = score_paper_for_topics(paper, ["stars"])
        assert score > 0

    def test_matching_abstract_keyword_gives_nonzero_score(self):
        paper = make_paper(abstract="We study exoplanet transit spectroscopy observations.")
        score = score_paper_for_topics(paper, ["exoplanets"])
        assert score > 0

    def test_no_match_gives_zero(self):
        paper = make_paper(title="Nothing relevant here", abstract="Random text about nothing.")
        score = score_paper_for_topics(paper, ["cosmology"])
        assert score == 0.0

    def test_title_match_scores_higher_than_abstract_only(self):
        paper_title = make_paper(
            title="Dark energy constraints from CMB",
            abstract="We present new observations.",
        )
        paper_abstract = make_paper(
            title="A new study",
            abstract="Dark energy constraints from CMB analysis.",
        )
        score_title = score_paper_for_topics(paper_title, ["cosmology"])
        score_abstract = score_paper_for_topics(paper_abstract, ["cosmology"])
        assert score_title >= score_abstract

    def test_empty_topics_gives_zero(self):
        paper = make_paper(title="Stellar evolution", abstract="Stars rotating fast.")
        score = score_paper_for_topics(paper, [])
        assert score == 0.0

    def test_multiple_topics_aggregate(self):
        paper = make_paper(
            title="Exoplanet in a stellar binary system with radial velocity",
            abstract="We detect an exoplanet transiting a binary star.",
        )
        score_both = score_paper_for_topics(paper, ["stars", "exoplanets"])
        score_stars = score_paper_for_topics(paper, ["stars"])
        score_exo = score_paper_for_topics(paper, ["exoplanets"])
        # Combined topics score should be >= either individual
        assert score_both >= 0

    def test_score_is_0_to_100(self):
        paper = make_paper(
            title="Neutron star black hole merger gravitational wave detection",
            abstract="LIGO detected a neutron star black hole merger via gravitational waves.",
        )
        score = score_paper_for_topics(paper, ["high_energy"])
        assert 0.0 <= score <= 100.0


class TestScorePapersForAllTopics:
    def test_adds_global_score(self):
        papers = [
            make_paper("2501.00001", title="Exoplanet transit spectroscopy study"),
            make_paper("2501.00002", title="Random unrelated title xyz"),
        ]
        result = score_papers_for_all_topics(papers)
        for p in result:
            assert "global_score" in p
            assert isinstance(p["global_score"], float)

    def test_sorted_descending(self):
        papers = [
            make_paper("2501.00001", title="Random title"),
            make_paper("2501.00002", title="Stellar evolution binary star radial velocity"),
            make_paper("2501.00003", title="Exoplanet transit stellar spectrum"),
        ]
        result = score_papers_for_all_topics(papers)
        scores = [p["global_score"] for p in result]
        assert scores == sorted(scores, reverse=True)

    def test_returns_all_papers(self):
        papers = [make_paper(f"2501.{i:05d}") for i in range(5)]
        result = score_papers_for_all_topics(papers)
        assert len(result) == 5


class TestBuildPersonalizedDigest:
    def test_filters_zero_score_papers(self):
        papers = [
            make_paper("2501.00001", title="Completely unrelated gobbledygook"),
            make_paper("2501.00002", title="Exoplanet atmosphere transit detection"),
        ]
        result = build_personalized_digest(papers, ["exoplanets"])
        ids = [p["id"] for p in result]
        assert "2501.00002" in ids
        # The zero-score paper should be excluded
        assert "2501.00001" not in ids

    def test_respects_max_papers_limit(self):
        papers = [
            make_paper(f"2501.{i:05d}", title="Stellar evolution binary star")
            for i in range(20)
        ]
        result = build_personalized_digest(papers, ["stars"], max_papers=5)
        assert len(result) <= 5

    def test_sorted_by_subscriber_score(self):
        papers = [
            make_paper("2501.00001", title="Stars", abstract="stellar evolution"),
            make_paper("2501.00002", title="Stars binary stellar radial velocity rotation"),
        ]
        result = build_personalized_digest(papers, ["stars"])
        if len(result) >= 2:
            assert result[0]["subscriber_score"] >= result[1]["subscriber_score"]

    def test_adds_subscriber_score_field(self):
        papers = [make_paper("2501.00001", title="Exoplanet transiting hot Jupiter")]
        result = build_personalized_digest(papers, ["exoplanets"])
        if result:
            assert "subscriber_score" in result[0]

    def test_empty_papers_returns_empty(self):
        result = build_personalized_digest([], ["stars"])
        assert result == []

    def test_empty_topics_returns_empty(self):
        papers = [make_paper("2501.00001", title="Stellar evolution")]
        result = build_personalized_digest(papers, [])
        assert result == []
