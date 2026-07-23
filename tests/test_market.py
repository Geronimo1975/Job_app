"""
Teste pentru conceptul de piață și normalizarea comună a jobului.
"""

import json

import pytest

from scrapers.base import Market, make_job, save_jobs


CFG = {
    "scraping": {
        "markets": {
            "de": {
                "country": "GERMANY",
                "language": "de",
                "locations": ["Germany"],
                "remote": False,
                "keyword_groups": [["Python Developer", "Django Developer"]],
                "sources": ["jobspy", "career_pages"],
            },
            "ro_remote": {
                "country": "ROMANIA",
                "language": "en",
                "locations": ["Romania"],
                "remote": True,
                "keyword_groups": [["Python Developer"], ["AI Engineer"]],
                "sources": ["jobspy", "remote_boards", "ro_boards"],
                "best_effort_sources": ["ro_boards"],
            },
        }
    }
}


class TestMarketFromConfig:
    def test_parses_all_markets(self):
        markets = Market.from_config(CFG)
        assert {m.key for m in markets} == {"de", "ro_remote"}

    def test_parses_fields(self):
        ro = next(m for m in Market.from_config(CFG) if m.key == "ro_remote")
        assert ro.country == "ROMANIA"
        assert ro.language == "en"
        assert ro.remote is True
        assert ro.keyword_groups == [["Python Developer"], ["AI Engineer"]]
        assert "ro_boards" in ro.sources

    def test_best_effort_defaults_to_empty(self):
        de = next(m for m in Market.from_config(CFG) if m.key == "de")
        assert de.best_effort_sources == ()

    def test_best_effort_is_parsed(self):
        ro = next(m for m in Market.from_config(CFG) if m.key == "ro_remote")
        assert ro.best_effort_sources == ("ro_boards",)

    def test_missing_markets_block_returns_empty(self):
        assert Market.from_config({"scraping": {}}) == []

    def test_market_is_immutable(self):
        de = next(m for m in Market.from_config(CFG) if m.key == "de")
        with pytest.raises(Exception):
            de.country = "FRANCE"

    def test_market_list_fields_are_mutable_in_place(self):
        """Frozen blochează reasignarea, dar listele interne rămân mutabile.

        Documentat explicit ca să nu fie o surpriză: `Market` nu e cu adevărat
        imutabil și nu e hashable (are câmpuri listă). Consumatorii nu trebuie
        să-l pună în seturi/dict-uri și nici să presupună că `locations` nu se
        poate schimba sub ei.
        """
        de = next(m for m in Market.from_config(CFG) if m.key == "de")
        before = len(de.locations)
        de.locations.append("Berlin")
        assert len(de.locations) == before + 1


class TestMakeJob:
    @pytest.fixture
    def market(self):
        return next(m for m in Market.from_config(CFG) if m.key == "ro_remote")

    def test_sets_language_from_market(self, market):
        job = make_job(
            source="remoteok", platform="remoteok", title="Python Dev",
            company="ACME", location="Remote", url="https://x.com/1",
            description="desc", market=market,
        )
        assert job["language"] == "en"

    def test_sets_market_key(self, market):
        job = make_job(
            source="remoteok", platform="remoteok", title="T", company="C",
            location="L", url="https://x.com/1", description="d", market=market,
        )
        assert job["market"] == "ro_remote"

    def test_gefunden_am_is_iso_date(self, market):
        job = make_job(
            source="remoteok", platform="remoteok", title="T", company="C",
            location="L", url="https://x.com/1", description="d", market=market,
        )
        assert len(job["gefunden_am"]) == 10
        assert job["gefunden_am"][4] == "-"

    def test_status_is_new(self, market):
        """`new` NU e o stare din col M; n8n_mapping îl filtrează la push."""
        job = make_job(
            source="remoteok", platform="remoteok", title="T", company="C",
            location="L", url="https://x.com/1", description="d", market=market,
        )
        assert job["status"] == "new"

    def test_description_is_truncated(self, market):
        job = make_job(
            source="remoteok", platform="remoteok", title="T", company="C",
            location="L", url="https://x.com/1", description="x" * 9000,
            market=market,
        )
        assert len(job["description"]) == 5000

    def test_strips_whitespace(self, market):
        job = make_job(
            source="remoteok", platform="remoteok", title="  Python Dev \n",
            company=" ACME ", location="L", url=" https://x.com/1 ",
            description="d", market=market,
        )
        assert job["title"] == "Python Dev"
        assert job["company"] == "ACME"
        assert job["url"] == "https://x.com/1"


class TestSaveJobs:
    def test_writes_json(self, tmp_path, ):
        market = next(m for m in Market.from_config(CFG) if m.key == "de")
        job = make_job(
            source="jobspy", platform="indeed", title="T", company="C",
            location="L", url="https://x.com/1", description="d", market=market,
        )
        path = save_jobs([job], tmp_path / "sub" / "jobs.json")

        assert path.exists()
        assert json.loads(path.read_text(encoding="utf-8"))[0]["title"] == "T"

    def test_overwrites_instead_of_appending(self, tmp_path):
        """Persistența istorică e treaba lui store/seen.py, nu a acestui fișier."""
        market = next(m for m in Market.from_config(CFG) if m.key == "de")
        path = tmp_path / "jobs.json"
        for title in ("A", "B"):
            job = make_job(
                source="jobspy", platform="indeed", title=title, company="C",
                location="L", url="https://x.com/1", description="d", market=market,
            )
            save_jobs([job], path)

        data = json.loads(path.read_text(encoding="utf-8"))
        assert len(data) == 1
        assert data[0]["title"] == "B"
