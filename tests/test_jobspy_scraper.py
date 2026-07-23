"""
Teste pentru JobSpyScraper — jobspy e mock-uit, nicio conexiune reală.
"""

import re
from unittest.mock import patch

import pytest

from scrapers.base import Market
from scrapers.jobspy_scraper import JobSpyScraper


CFG = {
    "scraping": {
        "max_results_per_call": 10,
        "hours_old": 72,
        "jobspy_sites": ["indeed", "linkedin"],
        "markets": {
            "de": {
                "country": "GERMANY", "language": "de", "locations": ["Germany"],
                "remote": False,
                "keyword_groups": [["Python Developer", "Django Developer"]],
                "sources": ["jobspy"],
            },
            "ro_remote": {
                "country": "ROMANIA", "language": "en", "locations": ["Romania"],
                "remote": True,
                "keyword_groups": [["Python Developer"]],
                "sources": ["jobspy"],
            },
        },
    }
}


class FakeDataFrame:
    """Imită doar ce folosește scraperul din pandas: `empty` și `iterrows`."""

    def __init__(self, rows):
        self._rows = rows

    @property
    def empty(self):
        return not self._rows

    def iterrows(self):
        return enumerate(self._rows)


def _market(key="de"):
    return next(m for m in Market.from_config(CFG) if m.key == key)


ROW = {
    "job_url": "https://example.com/job/1",
    "title": "Python Developer",
    "company": "TestCo",
    "site": "indeed",
    "location": "Berlin",
    "description": "Python and Django backend.",
}


class TestNormalizeJob:
    def test_includes_gefunden_am(self, tmp_path):
        scraper = JobSpyScraper(CFG, tmp_path)
        job = scraper._normalize_job(ROW, _market("de"))
        assert job is not None
        assert re.match(r"^\d{4}-\d{2}-\d{2}$", job["gefunden_am"])

    def test_language_comes_from_market(self, tmp_path):
        scraper = JobSpyScraper(CFG, tmp_path)
        assert scraper._normalize_job(ROW, _market("de"))["language"] == "de"
        assert scraper._normalize_job(ROW, _market("ro_remote"))["language"] == "en"

    def test_source_encodes_platform_and_market(self, tmp_path):
        """Col „Quelle" trebuie să spună de unde și din ce piață vine jobul."""
        scraper = JobSpyScraper(CFG, tmp_path)
        job = scraper._normalize_job(ROW, _market("ro_remote"))
        assert job["source"] == "indeed-ro_remote"

    def test_row_without_url_is_dropped(self, tmp_path):
        """Fără URL nu există cheie de upsert în Sheet — jobul e inutil."""
        scraper = JobSpyScraper(CFG, tmp_path)
        assert scraper._normalize_job({**ROW, "job_url": ""}, _market("de")) is None


class TestScrapeMarket:
    def test_passes_country_and_remote_from_market(self, tmp_path):
        scraper = JobSpyScraper(CFG, tmp_path)
        with patch("scrapers.jobspy_scraper.scrape_jobs") as mock:
            mock.return_value = FakeDataFrame([])
            scraper.scrape_market(_market("ro_remote"))

        kwargs = mock.call_args.kwargs
        assert kwargs["country_indeed"] == "ROMANIA"
        assert kwargs["is_remote"] is True
        assert kwargs["location"] == "Romania"
        assert kwargs["hours_old"] == 72

    def test_keyword_group_becomes_boolean_query(self, tmp_path):
        scraper = JobSpyScraper(CFG, tmp_path)
        with patch("scrapers.jobspy_scraper.scrape_jobs") as mock:
            mock.return_value = FakeDataFrame([])
            scraper.scrape_market(_market("de"))

        term = mock.call_args.kwargs["search_term"]
        assert '"Python Developer" OR "Django Developer"' == term

    def test_one_call_per_group_times_site(self, tmp_path):
        """1 grup × 2 site-uri = 2 apeluri, nu keywords × locații."""
        scraper = JobSpyScraper(CFG, tmp_path)
        with patch("scrapers.jobspy_scraper.scrape_jobs") as mock:
            mock.return_value = FakeDataFrame([])
            scraper.scrape_market(_market("de"))

        assert mock.call_count == 2

    def test_returns_normalized_jobs(self, tmp_path):
        scraper = JobSpyScraper(CFG, tmp_path)
        with patch("scrapers.jobspy_scraper.scrape_jobs") as mock:
            mock.return_value = FakeDataFrame([ROW])
            jobs, errors = scraper.scrape_market(_market("de"))

        assert errors == []
        assert all(j["title"] == "Python Developer" for j in jobs)

    def test_failing_site_is_reported_not_raised(self, tmp_path):
        scraper = JobSpyScraper(CFG, tmp_path)

        def boom(**kwargs):
            if kwargs["site_name"] == ["linkedin"]:
                raise RuntimeError("429 Too Many Requests")
            return FakeDataFrame([ROW])

        with patch("scrapers.jobspy_scraper.scrape_jobs", side_effect=boom):
            jobs, errors = scraper.scrape_market(_market("de"))

        assert len(jobs) >= 1                      # indeed a mers
        assert len(errors) == 1                    # linkedin a căzut
        assert "linkedin" in errors[0]
        assert "429" in errors[0]

    def test_missing_jobspy_raises_import_error(self, tmp_path):
        with patch("scrapers.jobspy_scraper.HAS_JOBSPY", False):
            scraper = JobSpyScraper(CFG, tmp_path)
            with pytest.raises(ImportError):
                scraper.scrape_market(_market("de"))
