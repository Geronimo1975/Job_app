"""
Teste pentru ScraperAgent — toate sursele mock-uite, fără rețea.
"""

from unittest.mock import MagicMock, patch

import pytest

from agents.base_agent import AgentStatus
from agents.scraper_agent import ScraperAgent
from scrapers.jobspy_scraper import WARNING_PREFIX


def _job(url, title="Python Developer", market="de"):
    return {
        "url": url, "title": title, "company": "TestCo",
        "market": market, "language": "de", "status": "new",
    }


@pytest.fixture
def agent(tmp_path):
    """ScraperAgent cu toate sursele mock-uite și config de test."""
    cfg = {
        "scraping": {
            "markets": {
                "de": {
                    "country": "GERMANY", "language": "de", "locations": ["Germany"],
                    "remote": False, "keyword_groups": [["Python Developer"]],
                    "sources": ["jobspy", "career_pages"],
                    "best_effort_sources": ["linkedin"],
                },
                "ro_remote": {
                    "country": "ROMANIA", "language": "en", "locations": ["Romania"],
                    "remote": True, "keyword_groups": [["Python Developer"]],
                    "sources": ["jobspy", "remote_boards", "ro_boards"],
                    "best_effort_sources": ["ro_boards"],
                },
            }
        },
        "output": {"logs_dir": "output/logs", "log_level": "CRITICAL"},
    }

    with patch("agents.scraper_agent.JobSpyScraper") as js, \
         patch("agents.scraper_agent.RemoteBoardsScraper") as rb, \
         patch("agents.scraper_agent.RoBoardsScraper") as ro, \
         patch("agents.scraper_agent.CareerPageScraper") as cp:
        js.return_value = MagicMock()
        rb.return_value = MagicMock()
        ro.return_value = MagicMock()
        cp.return_value = MagicMock()

        a = ScraperAgent()
        a.config = cfg
        a.logger.setLevel("CRITICAL")
        a.project_root = tmp_path

        # Implicit: totul întoarce gol și fără erori
        a.jobspy.scrape_market.return_value = ([], [])
        a.remote_boards.scrape_market.return_value = ([], [])
        a.ro_boards.scrape_market.return_value = ([], [])
        a.career.scrape.return_value = []
        yield a


class TestMarketSelection:
    def test_runs_all_markets_by_default(self, agent):
        result = agent.run()
        assert set(result.data["markets_used"]) == {"de", "ro_remote"}

    def test_can_restrict_to_one_market(self, agent):
        result = agent.run(markets=["ro_remote"])
        assert result.data["markets_used"] == ["ro_remote"]

    def test_unknown_market_is_ignored(self, agent):
        result = agent.run(markets=["nonexistent"])
        assert result.data["markets_used"] == []

    def test_each_market_gets_its_own_market_object(self, agent):
        agent.run()
        countries = {c.args[0].country for c in agent.jobspy.scrape_market.call_args_list}
        assert countries == {"GERMANY", "ROMANIA"}


class TestSourceRouting:
    def test_remote_boards_only_called_for_market_that_lists_it(self, agent):
        agent.run()
        markets = [c.args[0].key for c in agent.remote_boards.scrape_market.call_args_list]
        assert markets == ["ro_remote"]

    def test_career_pages_only_for_de(self, agent):
        agent.run()
        assert agent.career.scrape.call_count == 1

    def test_sources_override_restricts_execution(self, agent):
        result = agent.run(sources=["jobspy"])
        agent.remote_boards.scrape_market.assert_not_called()
        agent.career.scrape.assert_not_called()
        assert result.data["sources_used"] == ["jobspy"]


class TestAggregation:
    def test_collects_jobs_from_all_sources(self, agent):
        agent.jobspy.scrape_market.return_value = ([_job("https://a.com/1")], [])
        agent.remote_boards.scrape_market.return_value = ([_job("https://b.com/2")], [])
        result = agent.run()
        assert len(result.data["jobs"]) >= 2

    def test_writes_results_file(self, agent, tmp_path):
        agent.jobspy.scrape_market.return_value = ([_job("https://a.com/1")], [])
        agent.run()
        assert (tmp_path / "data" / "jobs_scraped.json").exists()


class TestHonestStatus:
    def test_all_ok_is_success(self, agent):
        agent.jobspy.scrape_market.return_value = ([_job("https://a.com/1")], [])
        assert agent.run().status == AgentStatus.SUCCESS

    def test_partial_when_a_source_reports_errors(self, agent):
        """Erorile întoarse de scraper, nu excepțiile, contează pentru status."""
        agent.jobspy.scrape_market.return_value = ([_job("https://a.com/1")], ["[de/indeed] 429"])
        result = agent.run()
        assert result.status == AgentStatus.PARTIAL
        assert any("429" in e for e in result.errors)

    def test_partial_when_a_source_raises(self, agent):
        agent.jobspy.scrape_market.side_effect = RuntimeError("boom")
        agent.remote_boards.scrape_market.return_value = ([_job("https://b.com/2")], [])
        result = agent.run()
        assert result.status == AgentStatus.PARTIAL
        assert result.data["jobs"]

    def test_failed_when_nothing_returned_and_errors_exist(self, agent):
        agent.jobspy.scrape_market.side_effect = RuntimeError("boom")
        agent.remote_boards.scrape_market.side_effect = RuntimeError("boom")
        agent.ro_boards.scrape_market.side_effect = RuntimeError("boom")
        agent.career.scrape.side_effect = RuntimeError("boom")
        assert agent.run().status == AgentStatus.FAILED

    def test_zero_jobs_without_errors_is_success(self, agent):
        """Zero joburi noi e un rezultat valid, nu o eroare."""
        assert agent.run().status == AgentStatus.SUCCESS

    def test_best_effort_source_failure_does_not_prevent_success(self, agent):
        """ro_boards e best-effort pentru ro_remote: eșecul lui nu strică statusul."""
        agent.jobspy.scrape_market.return_value = ([_job("https://a.com/1")], [])
        agent.ro_boards.scrape_market.side_effect = RuntimeError("403 Forbidden")
        result = agent.run()
        assert result.status == AgentStatus.SUCCESS
        assert any("403" in e for e in result.errors)   # tot se raportează

    def test_best_effort_site_error_does_not_downgrade_status(self, agent):
        """`linkedin` e site jobspy declarat best-effort: eroarea lui etichetată
        [de/linkedin] se raportează, dar nu coboară statusul."""
        agent.jobspy.scrape_market.return_value = (
            [_job("https://a.com/1")], ["[de/linkedin] 429 Too Many Requests"]
        )
        result = agent.run(markets=["de"])
        assert result.status == AgentStatus.SUCCESS
        assert any("linkedin" in e for e in result.errors)

    def test_zero_jobs_with_only_best_effort_errors_is_success(self, agent):
        """Zero joburi + doar erori best-effort NU e FAILED — sursele blocante au rulat curat."""
        agent.jobspy.scrape_market.return_value = ([], ["[de/linkedin] 429"])
        result = agent.run(markets=["de"])
        assert result.status == AgentStatus.SUCCESS


class TestWarningsDoNotLowerStatus:
    def test_warning_is_reported_but_status_stays_success(self, agent):
        """Un filtru renunțat e degradare raportată, nu eșec de sursă."""
        agent.jobspy.scrape_market.return_value = (
            [_job("https://a.co/1")],
            [f"{WARNING_PREFIX} [de/indeed] am renunțat la hours_old"],
        )
        result = agent.run(markets=["de"], sources=["jobspy"])

        assert result.status == AgentStatus.SUCCESS
        assert any(WARNING_PREFIX in e for e in result.errors)

    def test_real_error_still_lowers_status(self, agent):
        agent.jobspy.scrape_market.return_value = (
            [], ["[de/indeed] Python Developer: 500 Server Error"]
        )
        result = agent.run(markets=["de"], sources=["jobspy"])

        assert result.status == AgentStatus.FAILED

    def test_warning_plus_real_error_still_lowers_status(self, agent):
        """Avertismentul nu trebuie să camufleze o eroare reală din același lot."""
        agent.jobspy.scrape_market.return_value = (
            [_job("https://a.co/1")],
            [f"{WARNING_PREFIX} [de/indeed] am renunțat la hours_old",
             "[de/indeed] Python Developer: 500 Server Error"],
        )
        result = agent.run(markets=["de"], sources=["jobspy"])

        assert result.status == AgentStatus.PARTIAL
