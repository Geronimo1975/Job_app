"""Teste pentru fundația scraperelor: SearchQuery, Market.to_query, make_job."""

from scrapers.base import Market, SearchQuery, make_job

CFG = {
    "scraping": {
        "max_results_per_call": 10,
        "hours_old": 72,
        "jobspy_sites": ["indeed", "linkedin"],
        "google_search_term": "",
        "linkedin_fetch_description": False,
        "markets": {
            "de": {
                "country": "GERMANY", "language": "de", "locations": ["Germany"],
                "remote": False,
                "keyword_groups": [["Python Developer", "Django Developer"]],
                "sources": ["jobspy"],
            },
            "ro_remote": {
                "country": "ROMANIA", "language": "en", "locations": [],
                "remote": True,
                "keyword_groups": [["Python Developer"]],
                "sources": ["jobspy"],
            },
        },
    }
}


def _market(key):
    return next(m for m in Market.from_config(CFG) if m.key == key)


class TestMarketToQuery:
    def test_copies_market_identity(self):
        q = _market("de").to_query(CFG["scraping"])
        assert q.market_key == "de"
        assert q.country == "GERMANY"
        assert q.language == "de"
        assert q.remote is False
        assert q.keyword_groups == [["Python Developer", "Django Developer"]]

    def test_reads_sites_and_limits_from_scrape_config(self):
        q = _market("de").to_query(CFG["scraping"])
        assert q.sites == ["indeed", "linkedin"]
        assert q.hours_old == 72
        assert q.results_wanted == 10

    def test_location_falls_back_to_country(self):
        """Piața fără `locations` caută la nivel de țară, ca înainte."""
        assert _market("ro_remote").to_query(CFG["scraping"]).location == "ROMANIA"
        assert _market("de").to_query(CFG["scraping"]).location == "Germany"

    def test_remote_market_keeps_remote_flag(self):
        assert _market("ro_remote").to_query(CFG["scraping"]).remote is True


class TestSearchQueryDefaults:
    def test_optional_filters_default_to_empty(self):
        q = SearchQuery(keyword_groups=[["Python"]], sites=["indeed"],
                        location="Berlin", country="GERMANY", language="de")
        assert q.job_type == ""
        assert q.distance is None
        assert q.google_search_term == ""
        assert q.linkedin_fetch_description is False
        assert q.market_key == "adhoc"


class TestMakeJob:
    def test_market_key_and_language_are_plain_fields(self):
        job = make_job(
            source="indeed-de", platform="indeed", title="Python Developer",
            company="ACME", location="Berlin", url="https://a.co/1",
            description="x", market_key="de", language="de",
        )
        assert job["market"] == "de"
        assert job["language"] == "de"

    def test_raw_defaults_to_empty_dict(self):
        job = make_job(
            source="s", platform="p", title="t", company="c", location="l",
            url="https://a.co/1", description="d", market_key="de", language="de",
        )
        assert job["raw"] == {}

    def test_raw_is_carried_through(self):
        job = make_job(
            source="s", platform="p", title="t", company="c", location="l",
            url="https://a.co/1", description="d", market_key="de", language="de",
            raw={"currency": "EUR"},
        )
        assert job["raw"]["currency"] == "EUR"
