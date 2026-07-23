"""
Teste pentru board-urile remote — se parsează fixture-uri salvate, fără rețea.
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from scrapers.base import Market
from scrapers.remote_boards import RemoteBoardsScraper


FIXTURES = Path(__file__).parent / "fixtures"

CFG = {
    "scraping": {
        "markets": {
            "ro_remote": {
                "country": "ROMANIA", "language": "en", "locations": ["Romania"],
                "remote": True,
                "keyword_groups": [["Python Developer"], ["AI Engineer"]],
                "sources": ["remote_boards"],
            }
        }
    }
}


def _market():
    return Market.from_config(CFG)[0]


def _scraper(tmp_path):
    return RemoteBoardsScraper(CFG, tmp_path)


class TestParseRemoteOK:
    def test_skips_legal_notice_element(self, tmp_path):
        payload = json.loads((FIXTURES / "remoteok_sample.json").read_text())
        jobs = _scraper(tmp_path)._parse_remoteok(payload, _market())
        assert all(j["title"] for j in jobs)
        assert not any("personal use" in j["description"] for j in jobs)

    def test_legal_notice_with_url_is_still_skipped(self, tmp_path):
        """Gardul pe `position` lucrează singur, nu doar prin lipsa URL-ului."""
        payload = [
            {"legal": "Personal use only.", "url": "https://remoteok.com/legal"},
            {
                "id": "9",
                "position": "Python Dev",
                "company": "X",
                "location": "Remote",
                "url": "https://remoteok.com/remote-jobs/9",
                "description": "d",
                "date": "2026-07-20",
            },
        ]
        jobs = _scraper(tmp_path)._parse_remoteok(payload, _market())
        assert len(jobs) == 1
        assert jobs[0]["title"] == "Python Dev"

    def test_drops_entry_without_url(self, tmp_path):
        payload = json.loads((FIXTURES / "remoteok_sample.json").read_text())
        jobs = _scraper(tmp_path)._parse_remoteok(payload, _market())
        assert len(jobs) == 1
        assert jobs[0]["title"] == "Senior Python Developer"

    def test_strips_html_from_description(self, tmp_path):
        payload = json.loads((FIXTURES / "remoteok_sample.json").read_text())
        job = _scraper(tmp_path)._parse_remoteok(payload, _market())[0]
        assert "<p>" not in job["description"]
        assert "Python" in job["description"]

    def test_sets_platform_and_language(self, tmp_path):
        payload = json.loads((FIXTURES / "remoteok_sample.json").read_text())
        job = _scraper(tmp_path)._parse_remoteok(payload, _market())[0]
        assert job["platform"] == "remoteok"
        assert job["language"] == "en"
        assert job["market"] == "ro_remote"


class TestParseRemotive:
    def test_maps_fields(self, tmp_path):
        payload = json.loads((FIXTURES / "remotive_sample.json").read_text())
        job = _scraper(tmp_path)._parse_remotive(payload, _market())[0]
        assert job["title"] == "Django Developer"
        assert job["company"] == "Gamma SRL"
        assert job["location"] == "Romania"
        assert job["url"] == "https://remotive.com/remote-jobs/2001"

    def test_unescapes_html_entities(self, tmp_path):
        payload = json.loads((FIXTURES / "remotive_sample.json").read_text())
        job = _scraper(tmp_path)._parse_remotive(payload, _market())[0]
        assert "&amp;" not in job["description"]
        assert "Django & PostgreSQL" in job["description"]


class TestParseWwr:
    def test_splits_company_from_title(self, tmp_path):
        xml = (FIXTURES / "wwr_sample.xml").read_text()
        job = _scraper(tmp_path)._parse_wwr(xml, _market())[0]
        assert job["company"] == "Delta Inc"
        assert job["title"] == "Backend Engineer (Python)"

    def test_title_without_colon_falls_back_to_full_title(self, tmp_path):
        """Item fără „Firmă: Poziție" — tot titlul devine poziția, firma rămâne goală."""
        xml = (FIXTURES / "wwr_sample.xml").read_text()
        jobs = _scraper(tmp_path)._parse_wwr(xml, _market())
        solo = next(j for j in jobs if j["url"].endswith("/3002"))
        assert solo["title"] == "Standalone Python Engineer"
        assert solo["company"] == ""

    def test_maps_link(self, tmp_path):
        xml = (FIXTURES / "wwr_sample.xml").read_text()
        job = _scraper(tmp_path)._parse_wwr(xml, _market())[0]
        assert job["url"] == "https://weworkremotely.com/remote-jobs/3001"

    def test_malformed_xml_returns_empty(self, tmp_path):
        assert _scraper(tmp_path)._parse_wwr("<rss><nu", _market()) == []

    def test_entity_expansion_attack_returns_empty(self, tmp_path):
        """Un feed ostil („billion laughs") nu trebuie să epuizeze memoria."""
        bomb = """<?xml version="1.0"?>
        <!DOCTYPE rss [
          <!ENTITY a "aaaaaaaaaa">
          <!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;">
          <!ENTITY c "&b;&b;&b;&b;&b;&b;&b;&b;&b;&b;">
        ]>
        <rss><channel><item><title>&c;</title><link>http://x</link></item></channel></rss>"""
        assert _scraper(tmp_path)._parse_wwr(bomb, _market()) == []


class TestScrapeMarket:
    def test_failing_board_is_reported_not_raised(self, tmp_path):
        with patch("scrapers.remote_boards.httpx.get", side_effect=RuntimeError("503")):
            jobs, errors = _scraper(tmp_path).scrape_market(_market())

        assert jobs == []
        assert len(errors) == 3          # remoteok + remotive + wwr
        assert all("503" in e for e in errors)

    def test_one_downed_board_does_not_cancel_the_others(self, tmp_path):
        """Invarianta centrală: un board căzut NU anulează rezultatele celorlalte."""
        remoteok = json.loads((FIXTURES / "remoteok_sample.json").read_text())
        wwr = (FIXTURES / "wwr_sample.xml").read_text()

        def fake_get(url, **kwargs):
            if "remotive" in url:
                raise RuntimeError("503 Service Unavailable")
            resp = MagicMock()
            resp.raise_for_status.return_value = None
            if "remoteok" in url:
                resp.json.return_value = remoteok
            else:
                resp.text = wwr
            return resp

        with patch("scrapers.remote_boards.httpx.get", side_effect=fake_get):
            jobs, errors = _scraper(tmp_path).scrape_market(_market())

        # Eroarea board-ului căzut e înregistrată...
        assert len(errors) == 1
        assert "remotive" in errors[0] and "503" in errors[0]
        # ...iar celelalte două board-uri și-au livrat joburile.
        assert {j["platform"] for j in jobs} == {"remoteok", "weworkremotely"}
        assert len(jobs) >= 2

    def test_successful_boards_return_jobs(self, tmp_path):
        remoteok = json.loads((FIXTURES / "remoteok_sample.json").read_text())
        remotive = json.loads((FIXTURES / "remotive_sample.json").read_text())
        wwr = (FIXTURES / "wwr_sample.xml").read_text()

        def fake_get(url, **kwargs):
            resp = MagicMock()
            resp.raise_for_status.return_value = None
            if "remoteok" in url:
                resp.json.return_value = remoteok
            elif "remotive" in url:
                resp.json.return_value = remotive
            else:
                resp.text = wwr
            return resp

        with patch("scrapers.remote_boards.httpx.get", side_effect=fake_get):
            jobs, errors = _scraper(tmp_path).scrape_market(_market())

        assert errors == []
        assert {j["platform"] for j in jobs} == {"remoteok", "remotive", "weworkremotely"}

    def test_deduplicates_within_board_results(self, tmp_path):
        """Aceeași ofertă întoarsă de două interogări intră o singură dată."""
        remoteok = json.loads((FIXTURES / "remoteok_sample.json").read_text())
        scraper = _scraper(tmp_path)
        jobs = scraper._parse_remoteok(remoteok + remoteok, _market())
        assert len(jobs) == 1
