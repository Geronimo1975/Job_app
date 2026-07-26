"""
Teste pentru JobSpyScraper — jobspy e mock-uit, nicio conexiune reală.
"""

import re
from unittest.mock import patch

import pytest

from scrapers.base import Market, SearchQuery
from scrapers.jobspy_scraper import JobSpyScraper, SUPPORTED_SITES, WARNING_PREFIX


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


def _q(key="de"):
    return _market(key).to_query(CFG["scraping"])


def _kwargs_for(mock, site):
    """Kwargs-ii apelului pentru un site anume.

    `mock.call_args` e ultimul apel, iar apelurile rulează în paralel — deci
    ordinea lor nu e deterministă. Filtrarea pe site e singura variantă stabilă.
    """
    for call in mock.call_args_list:
        if call.kwargs.get("site_name") == [site]:
            return call.kwargs
    raise AssertionError(f"niciun apel pentru site-ul {site}")


def _query(**over):
    base = dict(keyword_groups=[["Python Developer"]], sites=["indeed"],
                location="Berlin", country="GERMANY", language="de",
                market_key="de")
    base.update(over)
    return SearchQuery(**base)


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
        job = scraper._normalize_job(ROW, _q("de"))
        assert job is not None
        assert re.match(r"^\d{4}-\d{2}-\d{2}$", job["gefunden_am"])

    def test_language_comes_from_market(self, tmp_path):
        scraper = JobSpyScraper(CFG, tmp_path)
        assert scraper._normalize_job(ROW, _q("de"))["language"] == "de"
        assert scraper._normalize_job(ROW, _q("ro_remote"))["language"] == "en"

    def test_source_encodes_platform_and_market(self, tmp_path):
        """Col „Quelle" trebuie să spună de unde și din ce piață vine jobul."""
        scraper = JobSpyScraper(CFG, tmp_path)
        job = scraper._normalize_job(ROW, _q("ro_remote"))
        assert job["source"] == "indeed-ro_remote"

    def test_row_without_url_is_dropped(self, tmp_path):
        """Fără URL nu există cheie de upsert în Sheet — jobul e inutil."""
        scraper = JobSpyScraper(CFG, tmp_path)
        assert scraper._normalize_job({**ROW, "job_url": ""}, _q("de")) is None


class TestScrapeMarket:
    def test_passes_country_and_remote_from_market(self, tmp_path):
        """Piață remote: pe Indeed, is_remote exclude hours_old.

        Testul asertase înainte `hours_old == 72` pe ambele site-uri, ceea ce
        cimenta bug-ul: upstream ignoră unul din filtre când Indeed le primește
        împreună, deci filtrul de vechime nu se aplica deși codul îl trimitea.
        """
        scraper = JobSpyScraper(CFG, tmp_path)
        with patch("scrapers.jobspy_scraper.scrape_jobs") as mock:
            mock.return_value = FakeDataFrame([])
            scraper.scrape_market(_market("ro_remote"))

        indeed = _kwargs_for(mock, "indeed")
        assert indeed["country_indeed"] == "ROMANIA"
        assert indeed["is_remote"] is True
        assert indeed["location"] == "Romania"
        # Piață remote pe Indeed: is_remote are prioritate, hours_old cade.
        assert "hours_old" not in indeed
        # Pe LinkedIn nu există conflict, deci filtrul de vechime rămâne.
        assert _kwargs_for(mock, "linkedin")["hours_old"] == 72

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


class TestSanitizeIndeed:
    """Upstream: pe Indeed poți folosi DOAR unul din hours_old /
    (job_type & is_remote) / easy_apply. Trimise împreună, un filtru cade
    silențios — exact ce făcea codul pentru piața ro_remote."""

    def test_remote_search_drops_hours_old_on_indeed(self, tmp_path):
        scraper = JobSpyScraper(CFG, tmp_path)
        with patch("scrapers.jobspy_scraper.scrape_jobs") as mock:
            mock.return_value = FakeDataFrame([])
            jobs, errors = scraper.scrape_query(_query(remote=True, hours_old=72))

        assert "hours_old" not in _kwargs_for(mock, "indeed")
        assert _kwargs_for(mock, "indeed")["is_remote"] is True
        assert any(e.startswith(WARNING_PREFIX) and "hours_old" in e for e in errors)

    def test_job_type_also_drops_hours_old_on_indeed(self, tmp_path):
        scraper = JobSpyScraper(CFG, tmp_path)
        with patch("scrapers.jobspy_scraper.scrape_jobs") as mock:
            mock.return_value = FakeDataFrame([])
            scraper.scrape_query(_query(job_type="fulltime", hours_old=72))

        assert "hours_old" not in _kwargs_for(mock, "indeed")
        assert _kwargs_for(mock, "indeed")["job_type"] == "fulltime"

    def test_non_remote_search_keeps_hours_old(self, tmp_path):
        scraper = JobSpyScraper(CFG, tmp_path)
        with patch("scrapers.jobspy_scraper.scrape_jobs") as mock:
            mock.return_value = FakeDataFrame([])
            jobs, errors = scraper.scrape_query(_query(remote=False, hours_old=72))

        assert _kwargs_for(mock, "indeed")["hours_old"] == 72
        assert errors == []

    def test_linkedin_keeps_hours_old_even_when_remote(self, tmp_path):
        """Restricția LinkedIn e hours_old vs easy_apply, nu vs is_remote."""
        scraper = JobSpyScraper(CFG, tmp_path)
        with patch("scrapers.jobspy_scraper.scrape_jobs") as mock:
            mock.return_value = FakeDataFrame([])
            scraper.scrape_query(_query(sites=["linkedin"], remote=True, hours_old=72))

        assert _kwargs_for(mock, "linkedin")["hours_old"] == 72


class TestOptionalParamsAreOmitted:
    def test_empty_optionals_never_reach_jobspy(self, tmp_path):
        scraper = JobSpyScraper(CFG, tmp_path)
        with patch("scrapers.jobspy_scraper.scrape_jobs") as mock:
            mock.return_value = FakeDataFrame([])
            scraper.scrape_query(_query())

        kwargs = _kwargs_for(mock, "indeed")
        assert "job_type" not in kwargs
        assert "distance" not in kwargs
        assert "google_search_term" not in kwargs
        assert "linkedin_fetch_description" not in kwargs

    def test_distance_is_passed_when_set(self, tmp_path):
        scraper = JobSpyScraper(CFG, tmp_path)
        with patch("scrapers.jobspy_scraper.scrape_jobs") as mock:
            mock.return_value = FakeDataFrame([])
            scraper.scrape_query(_query(distance=25))

        assert _kwargs_for(mock, "indeed")["distance"] == 25

    def test_google_search_term_only_for_google(self, tmp_path):
        scraper = JobSpyScraper(CFG, tmp_path)
        with patch("scrapers.jobspy_scraper.scrape_jobs") as mock:
            mock.return_value = FakeDataFrame([])
            scraper.scrape_query(_query(sites=["indeed", "google"],
                                        google_search_term="python jobs berlin"))

        assert _kwargs_for(mock, "google")["google_search_term"] == "python jobs berlin"
        assert "google_search_term" not in _kwargs_for(mock, "indeed")

    def test_linkedin_fetch_description_only_for_linkedin(self, tmp_path):
        scraper = JobSpyScraper(CFG, tmp_path)
        with patch("scrapers.jobspy_scraper.scrape_jobs") as mock:
            mock.return_value = FakeDataFrame([])
            scraper.scrape_query(_query(sites=["indeed", "linkedin"],
                                        linkedin_fetch_description=True))

        assert _kwargs_for(mock, "linkedin")["linkedin_fetch_description"] is True
        assert "linkedin_fetch_description" not in _kwargs_for(mock, "indeed")


class TestNormalizeEnrichment:
    def test_emails_column_fills_email_hr(self, tmp_path):
        scraper = JobSpyScraper(CFG, tmp_path)
        row = {**ROW, "emails": ["jobs@testco.de"]}
        assert scraper._normalize_job(row, _q("de"))["email_hr"] == "jobs@testco.de"

    def test_emails_accepts_plain_string(self, tmp_path):
        scraper = JobSpyScraper(CFG, tmp_path)
        row = {**ROW, "emails": "hr@testco.de"}
        assert scraper._normalize_job(row, _q("de"))["email_hr"] == "hr@testco.de"

    def test_first_valid_email_wins(self, tmp_path):
        scraper = JobSpyScraper(CFG, tmp_path)
        row = {**ROW, "emails": ["nu-e-email", "hr@testco.de"]}
        assert scraper._normalize_job(row, _q("de"))["email_hr"] == "hr@testco.de"

    def test_comma_joined_string_yields_only_the_first_address(self, tmp_path):
        """FORMA REALĂ din producție: upstream face `", ".join(emails)` înainte
        de DataFrame (`jobspy/__init__.py:141-143`), deci celula e un singur
        string cu toate adresele. Fără spargere, tot șirul ajungea în
        `msg["To"]` → un singur `RCPT TO:<a, b, c>` → SMTPRecipientsRefused.
        """
        scraper = JobSpyScraper(CFG, tmp_path)
        row = {**ROW, "emails": "bewerbung@testco.de, info@testco.de, presse@testco.de"}
        assert scraper._normalize_job(row, _q("de"))["email_hr"] == "bewerbung@testco.de"

    def test_semicolon_separated_string_yields_only_the_first_address(self, tmp_path):
        scraper = JobSpyScraper(CFG, tmp_path)
        row = {**ROW, "emails": "bewerbung@testco.de;info@testco.de"}
        assert scraper._normalize_job(row, _q("de"))["email_hr"] == "bewerbung@testco.de"

    def test_whitespace_separated_string_yields_only_the_first_address(self, tmp_path):
        scraper = JobSpyScraper(CFG, tmp_path)
        row = {**ROW, "emails": "bewerbung@testco.de info@testco.de"}
        assert scraper._normalize_job(row, _q("de"))["email_hr"] == "bewerbung@testco.de"

    def test_junk_first_candidate_is_skipped_inside_a_joined_string(self, tmp_path):
        scraper = JobSpyScraper(CFG, tmp_path)
        row = {**ROW, "emails": "nu-e-email, hr@testco.de"}
        assert scraper._normalize_job(row, _q("de"))["email_hr"] == "hr@testco.de"

    def test_missing_emails_leaves_field_empty(self, tmp_path):
        scraper = JobSpyScraper(CFG, tmp_path)
        assert scraper._normalize_job(ROW, _q("de"))["email_hr"] == ""

    def test_nan_never_becomes_the_string_nan(self, tmp_path):
        """NaN din pandas e truthy, deci `str(x or "")` dădea literal "nan"."""
        scraper = JobSpyScraper(CFG, tmp_path)
        nan = float("nan")
        row = {**ROW, "description": nan, "company": nan, "date_posted": nan,
               "emails": nan}
        job = scraper._normalize_job(row, _q("de"))
        assert job["description"] == ""
        assert job["company"] == ""
        assert job["date_posted"] == ""
        assert job["email_hr"] == ""

    def test_raw_carries_extra_signal(self, tmp_path):
        scraper = JobSpyScraper(CFG, tmp_path)
        row = {**ROW, "is_remote": True, "job_type": "fulltime",
               "currency": "EUR", "interval": "yearly",
               "company_url": "https://testco.de"}
        raw = scraper._normalize_job(row, _q("de"))["raw"]
        assert raw["is_remote"] is True
        assert raw["job_type"] == "fulltime"
        assert raw["currency"] == "EUR"
        assert raw["interval"] == "yearly"
        assert raw["company_url"] == "https://testco.de"


class TestUnknownSite:
    def test_unknown_site_is_reported_not_raised(self, tmp_path):
        scraper = JobSpyScraper(CFG, tmp_path)
        with patch("scrapers.jobspy_scraper.scrape_jobs") as mock:
            mock.return_value = FakeDataFrame([])
            jobs, errors = scraper.scrape_query(_query(sites=["indeed", "monster"]))

        assert mock.call_count == 1                      # doar indeed
        assert any("monster" in e for e in errors)

    def test_supported_sites_matches_upstream(self):
        assert set(SUPPORTED_SITES) == {
            "linkedin", "indeed", "zip_recruiter", "glassdoor",
            "google", "bayt", "naukri", "bdjobs",
        }
