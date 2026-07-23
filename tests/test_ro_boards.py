"""
Teste pentru board-urile românești — parsare pe fixture salvat, fără rețea.

Sursă best-effort: eJobs/BestJobs nu au API public, iar HTML-ul se schimbă.
Când aceste teste pică pe fixture nou, înseamnă că site-ul s-a schimbat —
e semnalul de reparat parserul, nu un bug de regresie.

Notă despre fixture: pagina eJobs randează server-side doar primele carduri
(restul sunt placeholdere `job-card-wrapper__temp`, fără link). Din cele
40 de carduri, doar 3 au titlu + link → EXPECTED_MIN_JOBS = 3.
"""

from pathlib import Path
from unittest.mock import patch

from scrapers.base import Market
from scrapers.ro_boards import RoBoardsScraper


FIXTURES = Path(__file__).parent / "fixtures"
# Fixture-ul real conține 40 de carduri, dar doar 3 sunt randate complet
# server-side (cu titlu și link); restul sunt placeholdere client-side.
EXPECTED_MIN_JOBS = 3

CFG = {
    "scraping": {
        "markets": {
            "ro_remote": {
                "country": "ROMANIA", "language": "en", "locations": ["Romania"],
                "remote": True,
                "keyword_groups": [["Python Developer"]],
                "sources": ["ro_boards"],
                "best_effort_sources": ["ro_boards"],
            }
        }
    }
}


def _market():
    return Market.from_config(CFG)[0]


def _scraper(tmp_path):
    return RoBoardsScraper(CFG, tmp_path)


class TestParseEjobs:
    def test_extracts_jobs_from_real_fixture(self, tmp_path):
        html_text = (FIXTURES / "ejobs_sample.html").read_text(encoding="utf-8")
        jobs = _scraper(tmp_path)._parse_ejobs(html_text, _market())
        assert len(jobs) >= EXPECTED_MIN_JOBS

    def test_every_job_has_absolute_url(self, tmp_path):
        html_text = (FIXTURES / "ejobs_sample.html").read_text(encoding="utf-8")
        for job in _scraper(tmp_path)._parse_ejobs(html_text, _market()):
            assert job["url"].startswith("http")

    def test_every_job_has_title(self, tmp_path):
        html_text = (FIXTURES / "ejobs_sample.html").read_text(encoding="utf-8")
        for job in _scraper(tmp_path)._parse_ejobs(html_text, _market()):
            assert job["title"]

    def test_company_and_location_extracted(self, tmp_path):
        # Primul card din fixture are firmă și locație cunoscute — verifică
        # distincția '--darker' (firmă) vs. fără modificator (locație).
        html_text = (FIXTURES / "ejobs_sample.html").read_text(encoding="utf-8")
        jobs = _scraper(tmp_path)._parse_ejobs(html_text, _market())
        assert jobs, "fixture-ul trebuie să producă joburi"
        assert jobs[0]["company"] == "CLOUDBASE SOLUTIONS"
        assert "Timișoara" in jobs[0]["location"]

    def test_language_and_market_come_from_market(self, tmp_path):
        html_text = (FIXTURES / "ejobs_sample.html").read_text(encoding="utf-8")
        jobs = _scraper(tmp_path)._parse_ejobs(html_text, _market())
        if jobs:
            assert jobs[0]["language"] == "en"
            assert jobs[0]["market"] == "ro_remote"
            assert jobs[0]["platform"] == "ejobs"

    def test_garbage_html_returns_empty_not_exception(self, tmp_path):
        assert _scraper(tmp_path)._parse_ejobs("<html><body>nimic</body></html>", _market()) == []


class TestScrapeMarket:
    def test_network_failure_is_reported_not_raised(self, tmp_path):
        with patch("scrapers.ro_boards.httpx.get", side_effect=RuntimeError("403 Forbidden")):
            jobs, errors = _scraper(tmp_path).scrape_market(_market())

        assert jobs == []
        assert errors
        assert "403" in errors[0]
