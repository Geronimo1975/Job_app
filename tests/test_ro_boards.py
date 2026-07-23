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
from unittest.mock import MagicMock, patch

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
            assert job["url"].startswith("https://www.ejobs.ro/")

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

    def test_one_failed_group_does_not_cancel_the_others(self, tmp_path):
        """Contractul central best-effort: grupul căzut nu șterge joburile celorlalte."""
        cfg = {
            "scraping": {
                "markets": {
                    "ro_remote": {
                        "country": "ROMANIA", "language": "en",
                        "locations": ["Romania"], "remote": True,
                        "keyword_groups": [["Python Developer"], ["AI Engineer"]],
                        "sources": ["ro_boards"],
                        "best_effort_sources": ["ro_boards"],
                    }
                }
            }
        }
        market = Market.from_config(cfg)[0]
        html = (FIXTURES / "ejobs_sample.html").read_text(encoding="utf-8")

        def fake_get(url, **kwargs):
            if "python-developer" in url:
                raise RuntimeError("503 Service Unavailable")
            resp = MagicMock()
            resp.raise_for_status.return_value = None
            resp.text = html
            return resp

        with patch("scrapers.ro_boards.httpx.get", side_effect=fake_get):
            jobs, errors = RoBoardsScraper(cfg, tmp_path).scrape_market(market)

        # Eroarea grupului căzut e înregistrată cu context...
        assert len(errors) == 1
        assert "Python Developer" in errors[0] and "503" in errors[0]
        # ...iar grupul sănătos și-a livrat joburile.
        assert len(jobs) >= 1
        assert all(j["platform"] == "ejobs" for j in jobs)

    def test_same_job_from_two_groups_is_deduplicated(self, tmp_path):
        """Două grupuri care întorc aceeași pagină nu dublează joburile."""
        cfg = {
            "scraping": {
                "markets": {
                    "ro_remote": {
                        "country": "ROMANIA", "language": "en",
                        "locations": ["Romania"], "remote": True,
                        "keyword_groups": [["Python Developer"], ["AI Engineer"]],
                        "sources": ["ro_boards"],
                    }
                }
            }
        }
        market = Market.from_config(cfg)[0]
        html = (FIXTURES / "ejobs_sample.html").read_text(encoding="utf-8")

        resp = MagicMock()
        resp.raise_for_status.return_value = None
        resp.text = html

        with patch("scrapers.ro_boards.httpx.get", return_value=resp):
            jobs, errors = RoBoardsScraper(cfg, tmp_path).scrape_market(market)

        assert errors == []
        urls = [j["url"] for j in jobs]
        assert len(urls) == len(set(urls))          # zero dubluri
        # Ambele grupuri au întors aceeași pagină → tot atâtea joburi cât dintr-una.
        single = RoBoardsScraper(cfg, tmp_path)._parse_ejobs(html, market)
        assert len(jobs) == len(single)

    def test_requested_url_is_slugified(self, tmp_path):
        """„Python Developer" trebuie cerut ca .../python-developer."""
        resp = MagicMock()
        resp.raise_for_status.return_value = None
        resp.text = "<html></html>"

        with patch("scrapers.ro_boards.httpx.get", return_value=resp) as mock:
            _scraper(tmp_path).scrape_market(_market())

        called_url = mock.call_args[1]["url"] if "url" in mock.call_args[1] else mock.call_args[0][0]
        assert "python-developer" in called_url
        assert " " not in called_url

    def test_empty_keyword_group_does_not_raise(self, tmp_path):
        """Un grup gol în config nu sparge contractul „nu ridică"."""
        cfg = {
            "scraping": {
                "markets": {
                    "ro_remote": {
                        "country": "ROMANIA", "language": "en",
                        "locations": ["Romania"], "remote": True,
                        "keyword_groups": [[]],
                        "sources": ["ro_boards"],
                    }
                }
            }
        }
        market = Market.from_config(cfg)[0]

        with patch("scrapers.ro_boards.httpx.get") as mock:
            jobs, errors = RoBoardsScraper(cfg, tmp_path).scrape_market(market)

        assert jobs == [] and errors == []
        mock.assert_not_called()
