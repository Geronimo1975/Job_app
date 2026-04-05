"""
Teste unitare pentru ScraperAgent — mock httpx/jobspy, fără conexiuni externe.
"""

import json
from datetime import datetime
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from agents.base_agent import AgentStatus
from agents.scraper_agent import ScraperAgent


MOCK_JOBSPY_RESULTS = [
    {
        "title": "Python Developer",
        "company": "GameCo GmbH",
        "location": "Berlin",
        "url": "https://example.com/job/1",
        "description": "Python backend developer needed.",
    },
    {
        "title": "AI Engineer",
        "company": "TechStudio AG",
        "location": "München",
        "url": "https://example.com/job/2",
        "description": "ML Engineer for game AI.",
    },
]

MOCK_CAREER_RESULTS = [
    {
        "id": "career_001",
        "source": "career_page",
        "title": "Game Developer",
        "company": "InnoGames",
        "location": "Hamburg",
        "url": "https://innogames.com/jobs/game-dev",
        "description": "Unity game developer position.",
    },
]


@pytest.fixture
def scraper_agent():
    """ScraperAgent cu scraperele mockate la init."""
    with patch("agents.scraper_agent.JobSpyScraper") as mock_js, \
         patch("agents.scraper_agent.CareerPageScraper") as mock_cp:
        mock_js.return_value = MagicMock()
        mock_cp.return_value = MagicMock()
        agent = ScraperAgent()
        agent.logger.setLevel("CRITICAL")
        yield agent


class TestScraperAgent:
    def test_instantiation(self, scraper_agent):
        assert scraper_agent.name == "ScraperAgent"
        assert scraper_agent.status == AgentStatus.IDLE

    def test_run_both_sources(self, scraper_agent):
        """Testează scraping din ambele surse cu mock."""
        scraper_agent.jobspy.scrape.return_value = MOCK_JOBSPY_RESULTS
        scraper_agent.jobspy.save_results.return_value = Path("/tmp/jobs.json")
        scraper_agent.career.scrape.return_value = MOCK_CAREER_RESULTS
        scraper_agent.career.save_results.return_value = Path("/tmp/career.json")

        result = scraper_agent.run()

        assert result.status == AgentStatus.SUCCESS
        jobs = result.data.get("jobs", [])
        assert len(jobs) == 3  # 2 jobspy + 1 career

    def test_run_jobspy_only(self, scraper_agent):
        """Testează scraping doar din jobspy."""
        scraper_agent.jobspy.scrape.return_value = MOCK_JOBSPY_RESULTS
        scraper_agent.jobspy.save_results.return_value = Path("/tmp/jobs.json")

        result = scraper_agent.run(sources=["jobspy"])

        scraper_agent.jobspy.scrape.assert_called_once()
        scraper_agent.career.scrape.assert_not_called()
        assert len(result.data["jobs"]) == 2

    def test_run_career_pages_only(self, scraper_agent):
        """Testează scraping doar din career pages."""
        scraper_agent.career.scrape.return_value = MOCK_CAREER_RESULTS
        scraper_agent.career.save_results.return_value = Path("/tmp/career.json")

        result = scraper_agent.run(sources=["career_pages"])

        scraper_agent.career.scrape.assert_called_once()
        scraper_agent.jobspy.scrape.assert_not_called()
        assert len(result.data["jobs"]) == 1

    def test_run_with_custom_keywords(self, scraper_agent):
        """Testează scraping cu keywords custom."""
        scraper_agent.jobspy.scrape.return_value = []
        scraper_agent.jobspy.save_results.return_value = Path("/tmp/empty.json")

        result = scraper_agent.run(
            sources=["jobspy"],
            keywords=["Unity Developer"],
            locations=["Hamburg"],
        )

        call_kwargs = scraper_agent.jobspy.scrape.call_args
        assert call_kwargs is not None
        assert call_kwargs.kwargs.get("keywords") == ["Unity Developer"]
        assert call_kwargs.kwargs.get("locations") == ["Hamburg"]

    def test_run_handles_scraper_exception(self, scraper_agent):
        """Testează că erorile din scraper sunt gestionate."""
        scraper_agent.jobspy.scrape.side_effect = Exception("Connection timeout")
        scraper_agent.career.scrape.return_value = MOCK_CAREER_RESULTS
        scraper_agent.career.save_results.return_value = Path("/tmp/career.json")

        result = scraper_agent.run()

        assert result.status == AgentStatus.PARTIAL
        assert len(result.errors) >= 1
        assert len(result.data["jobs"]) == 1  # doar career pages

    def test_run_all_fail(self, scraper_agent):
        """Testează când toate sursele eșuează."""
        scraper_agent.jobspy.scrape.side_effect = Exception("Timeout")
        scraper_agent.career.scrape.side_effect = Exception("404 Not Found")

        result = scraper_agent.run()

        assert result.status == AgentStatus.FAILED
        assert len(result.errors) == 2

    def test_run_no_results(self, scraper_agent):
        """Testează când nu se găsesc joburi."""
        scraper_agent.jobspy.scrape.return_value = []
        scraper_agent.jobspy.save_results.return_value = Path("/tmp/empty.json")
        scraper_agent.career.scrape.return_value = []
        scraper_agent.career.save_results.return_value = Path("/tmp/empty2.json")

        result = scraper_agent.run()

        assert result.status == AgentStatus.SUCCESS
        assert len(result.data["jobs"]) == 0

    def test_result_contains_sources_used(self, scraper_agent):
        """Verifică că rezultatul conține sursele folosite."""
        scraper_agent.jobspy.scrape.return_value = []
        scraper_agent.jobspy.save_results.return_value = Path("/tmp/e.json")

        result = scraper_agent.run(sources=["jobspy"])

        assert result.data["sources_used"] == ["jobspy"]
