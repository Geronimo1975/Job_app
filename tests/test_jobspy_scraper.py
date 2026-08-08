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
        """Piață remote: pe Indeed, `is_remote` cade în favoarea search_term.

        Istoric: testul asertase `hours_old == 72` pe ambele site-uri, ceea ce
        cimenta bug-ul upstream (Indeed ignoră unul din filtre când le primește
        împreună). Apoi a asertat `is_remote is True` + `hours_old` renunțat —
        dar `is_remote` era exact filtrul care anula termenul de căutare, deci
        acum el cade, iar `hours_old` redevine folosibil.
        """
        scraper = JobSpyScraper(CFG, tmp_path)
        with patch("scrapers.jobspy_scraper.scrape_jobs") as mock:
            mock.return_value = FakeDataFrame([])
            scraper.scrape_market(_market("ro_remote"))

        indeed = _kwargs_for(mock, "indeed")
        assert indeed["country_indeed"] == "ROMANIA"
        assert indeed["location"] == "Romania"
        # Remote se filtrează local; upstream primește doar termenul de căutare.
        assert "is_remote" not in indeed
        assert indeed["hours_old"] == 72
        # Pe LinkedIn nu există conflict: ambele filtre rămân.
        linkedin = _kwargs_for(mock, "linkedin")
        assert linkedin["hours_old"] == 72
        assert linkedin["is_remote"] is True

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

    def test_remote_search_drops_is_remote_not_the_search_term(self, tmp_path):
        """`is_remote` costă termenul de căutare, deci el e cel care cade.

        Verificat live (2026-08-06, UAE): aceeași interogare booleană întoarce
        cu `is_remote=True` joburi fără nicio legătură („Senior Real Estate
        Broker" pentru „AI Developer"), iar toate cele 4 grupuri de keywords
        primesc identic aceleași 50 de rânduri — semnul că Indeed ignoră
        `search_term`. Fără `is_remote`, aceeași interogare întoarce „AI
        Engineer / Agentic AI Developer", „AI Automation Engineer".
        """
        scraper = JobSpyScraper(CFG, tmp_path)
        with patch("scrapers.jobspy_scraper.scrape_jobs") as mock:
            mock.return_value = FakeDataFrame([])
            jobs, errors = scraper.scrape_query(_query(remote=True, hours_old=72))

        indeed = _kwargs_for(mock, "indeed")
        assert "is_remote" not in indeed
        assert indeed["search_term"] == '"Python Developer"'
        assert any(e.startswith(WARNING_PREFIX) and "is_remote" in e for e in errors)

    def test_dropping_is_remote_gives_hours_old_back(self, tmp_path):
        """Fără `is_remote` și fără `job_type`, `hours_old` nu mai are conflict."""
        scraper = JobSpyScraper(CFG, tmp_path)
        with patch("scrapers.jobspy_scraper.scrape_jobs") as mock:
            mock.return_value = FakeDataFrame([])
            jobs, errors = scraper.scrape_query(_query(remote=True, hours_old=72))

        assert _kwargs_for(mock, "indeed")["hours_old"] == 72
        assert not any("hours_old" in e for e in errors)

    def test_job_type_still_drops_hours_old_on_indeed(self, tmp_path):
        """`job_type` rămâne — el nu anulează search_term, deci hours_old cade."""
        scraper = JobSpyScraper(CFG, tmp_path)
        with patch("scrapers.jobspy_scraper.scrape_jobs") as mock:
            mock.return_value = FakeDataFrame([])
            jobs, errors = scraper.scrape_query(
                _query(remote=True, job_type="parttime", hours_old=72))

        indeed = _kwargs_for(mock, "indeed")
        assert indeed["job_type"] == "parttime"
        assert "hours_old" not in indeed
        assert any(e.startswith(WARNING_PREFIX) and "hours_old" in e for e in errors)

    def test_is_remote_survives_when_there_is_no_search_term(self, tmp_path):
        """Fără termen de căutare nu există ce să anuleze — filtrul rămâne util."""
        scraper = JobSpyScraper(CFG, tmp_path)
        with patch("scrapers.jobspy_scraper.scrape_jobs") as mock:
            mock.return_value = FakeDataFrame([])
            scraper.scrape_query(_query(remote=True, keyword_groups=[[]]))

        assert _kwargs_for(mock, "indeed")["is_remote"] is True

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

    def test_linkedin_keeps_is_remote(self, tmp_path):
        """Doar Indeed pierde search_term pe is_remote — LinkedIn îl păstrează."""
        scraper = JobSpyScraper(CFG, tmp_path)
        with patch("scrapers.jobspy_scraper.scrape_jobs") as mock:
            mock.return_value = FakeDataFrame([])
            scraper.scrape_query(_query(sites=["linkedin"], remote=True))

        assert _kwargs_for(mock, "linkedin")["is_remote"] is True


class TestRemoteFilteredLocally:
    """Când `is_remote` nu mai ajunge la jobspy (vezi `_sanitize`), filtrul
    cerut de piață trebuie aplicat pe rândurile întoarse — altfel o căutare
    remote ar aduce și joburi on-site."""

    ONSITE = {**ROW, "job_url": "https://example.com/job/onsite",
              "title": "Python Developer", "location": "Dubai", "is_remote": False}
    REMOTE_FLAG = {**ROW, "job_url": "https://example.com/job/flag",
                   "title": "Python Developer", "location": "Dubai", "is_remote": True}
    REMOTE_TITLE = {**ROW, "job_url": "https://example.com/job/title",
                    "title": "Python Developer (Remote)", "location": "Dubai",
                    "is_remote": False}

    def _run(self, tmp_path, rows, **over):
        scraper = JobSpyScraper(CFG, tmp_path)
        with patch("scrapers.jobspy_scraper.scrape_jobs") as mock:
            mock.return_value = FakeDataFrame(rows)
            jobs, _ = scraper.scrape_query(_query(**over))
        return jobs

    def test_onsite_rows_are_dropped_on_indeed_remote_search(self, tmp_path):
        jobs = self._run(tmp_path, [self.ONSITE, self.REMOTE_FLAG], remote=True)
        assert [j["url"] for j in jobs] == ["https://example.com/job/flag"]

    def test_remote_written_in_the_title_counts(self, tmp_path):
        """Indeed nu setează mereu `is_remote`; „(Remote)" în titlu e neechivoc."""
        jobs = self._run(tmp_path, [self.REMOTE_TITLE], remote=True)
        assert len(jobs) == 1

    def test_non_remote_search_keeps_everything(self, tmp_path):
        jobs = self._run(tmp_path, [self.ONSITE, self.REMOTE_FLAG], remote=False)
        assert len(jobs) == 2

    def test_no_local_filter_when_is_remote_reached_jobspy(self, tmp_path):
        """Pe LinkedIn filtrul l-a aplicat deja upstream — a doua filtrare pe
        un câmp pe care LinkedIn nu-l completează ar goli rezultatele."""
        jobs = self._run(tmp_path, [self.ONSITE], sites=["linkedin"], remote=True)
        assert len(jobs) == 1


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


class TestEmailIsAnApplicationAddress:
    """Anunțurile conțin adrese care NU primesc candidaturi: clauze legale
    („reasonable accommodations", „pay transparency"), presă, protecția
    datelor. Măsurat live 2026-08-06: pe UAE/RO/CH, majoritatea adreselor
    extrase erau exact astea — o candidatură trimisă acolo e mai rea decât
    lipsa adresei, fiindcă arată ca spam."""

    def _mail(self, tmp_path, emails):
        scraper = JobSpyScraper(CFG, tmp_path)
        return scraper._normalize_job({**ROW, "emails": emails}, _q("de"))["email_hr"]

    def test_boilerplate_address_is_never_used(self, tmp_path):
        assert self._mail(tmp_path, "reasonable.accommodations@genesys.com") == ""

    def test_privacy_address_is_never_used(self, tmp_path):
        assert self._mail(tmp_path, "privacy@appdirect.com") == ""

    def test_pay_transparency_address_is_never_used(self, tmp_path):
        assert self._mail(tmp_path, "paytransparency@clickhouse.com") == ""

    def test_real_address_wins_over_boilerplate_even_if_it_comes_later(self, tmp_path):
        emails = "reasonable.accommodations@genesys.com, recruiting@genesys.com"
        assert self._mail(tmp_path, emails) == "recruiting@genesys.com"

    def test_application_address_beats_a_neutral_one_that_comes_first(self, tmp_path):
        """`info@` e plauzibilă, dar `bewerbung@` e adresa care chiar primește
        candidaturi — ordinea din text nu trebuie să decidă."""
        assert self._mail(tmp_path, "info@testco.de, bewerbung@testco.de") \
            == "bewerbung@testco.de"

    def test_talent_acquisition_counts_as_application_address(self, tmp_path):
        assert self._mail(tmp_path, "info@x.com, talent.acquisition@servicenow.com") \
            == "talent.acquisition@servicenow.com"

    def test_neutral_address_is_still_used_when_nothing_better_exists(self, tmp_path):
        assert self._mail(tmp_path, "amelie@ecoza.de") == "amelie@ecoza.de"

    def test_hr_matches_only_as_a_whole_token(self, tmp_path):
        """`hr` e prea scurt pentru potrivire pe subșir: „chris@" îl conține."""
        assert self._mail(tmp_path, "chris@testco.de") == "chris@testco.de"

    def test_truncated_address_is_rejected(self, tmp_path):
        """Local part care începe cu punctuație = adresă tăiată de parser.

        Observate live 2026-08-06: `_Acquisition@eon.com` (din
        „Talent_Acquisition@") și `_mb@oracle.com`. Sintactic sunt valide, dar
        ambele ar da bounce — iar un bounce e exact eroarea pe care
        `require_email` trebuie s-o prevină.
        """
        assert self._mail(tmp_path, "_Acquisition@eon.com") == ""
        assert self._mail(tmp_path, ".mb@oracle.com") == ""

    def test_truncated_address_does_not_hide_a_good_one(self, tmp_path):
        assert self._mail(tmp_path, "_Acquisition@eon.com, karriere@eon.com") \
            == "karriere@eon.com"

    def test_dei_address_is_not_an_application_address(self, tmp_path):
        """`inclusion@`/`diversity@` sunt aceeași categorie ca
        `reasonable.accommodations@`: text standard, nu destinatar."""
        assert self._mail(tmp_path, "inclusion@kraken.tech") == ""
        assert self._mail(tmp_path, "diversity@x.com") == ""

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
