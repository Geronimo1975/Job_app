"""
Teste pentru board-urile românești — parsare pe fixture salvat, fără rețea.

Sursă best-effort: eJobs/BestJobs nu au API public, iar HTML-ul se schimbă.
Când aceste teste pică pe fixture nou, înseamnă că site-ul s-a schimbat —
e semnalul de reparat parserul, nu un bug de regresie.

Notă despre fixture: din cele 40 de carduri, doar 3 sunt randate complet
server-side (titlu + link); restul sunt placeholdere `job-card-wrapper__temp`
cu titlu și firmă, dar FĂRĂ link. Toate 40 există însă în blocul JSON-LD
`ItemList` din aceeași pagină, cu nume și URL — de acolo se citesc.
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from scrapers.base import Market
from scrapers.ro_boards import RoBoardsScraper


FIXTURES = Path(__file__).parent / "fixtures"
# Toate cele 40 de anunțuri ale paginii, citite din JSON-LD `ItemList` —
# nu doar cele 3 carduri randate server-side.
EXPECTED_MIN_JOBS = 40

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

    def test_placeholder_cards_are_not_lost(self, tmp_path):
        """Cardurile fără link erau sărite, deci se pierdeau 37 din 40 de
        anunțuri per căutare. `ItemList` le are pe toate, cu URL."""
        html_text = (FIXTURES / "ejobs_sample.html").read_text(encoding="utf-8")
        titles = {j["title"] for j in _scraper(tmp_path)._parse_ejobs(html_text, _market())}
        assert "IoT Engineer" in titles                       # era doar placeholder
        assert "Python Software Developer" in titles          # era card randat

    def test_company_comes_from_the_placeholder_card(self, tmp_path):
        """Placeholderul n-are link, dar are titlu + firmă — se folosesc."""
        html_text = (FIXTURES / "ejobs_sample.html").read_text(encoding="utf-8")
        jobs = _scraper(tmp_path)._parse_ejobs(html_text, _market())
        iot = next(j for j in jobs if j["title"] == "IoT Engineer")
        assert iot["company"].startswith("VISION TECHNOLOGY")

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
            resp = MagicMock()
            resp.raise_for_status.return_value = None
            resp.status_code = 200
            if "/user/locuri-de-munca/" in url:      # pagină de detaliu
                resp.text = _detail("Descriere completa.")
                return resp
            if "python-developer" in url:
                raise RuntimeError("503 Service Unavailable")
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

        def fake_get(url, **kwargs):
            resp = MagicMock()
            resp.raise_for_status.return_value = None
            resp.status_code = 200
            resp.text = _detail("Descriere.") if "/user/locuri-de-munca/" in url else html
            return resp

        with patch("scrapers.ro_boards.httpx.get", side_effect=fake_get):
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


_DETAIL_HTML = """
<html><body>
<script type="application/ld+json">%s</script>
</body></html>
"""


def _detail(description, company="ACME SRL", city="București"):
    return _DETAIL_HTML % json.dumps({
        "@context": "https://schema.org",
        "@type": "JobPosting",
        "title": "Python Developer",
        "description": description,
        "datePosted": "2026-08-04",
        "hiringOrganization": {"@type": "Organization", "name": company},
        "jobLocation": {"@type": "Place",
                        "address": {"@type": "PostalAddress", "addressLocality": city}},
    })


class TestDetailEnrichment:
    """Pagina de listă nu are descriere, deci joburile eJobs ajungeau la
    prescore doar cu titlul, iar `email_hr` era mereu gol → cu
    `require_email: true` niciun job eJobs nu putea trece poarta. Pagina de
    detaliu are `JobPosting` complet (verificat live 2026-08-06)."""

    def test_description_is_filled_from_the_detail_page(self, tmp_path):
        job = {"url": "https://www.ejobs.ro/user/locuri-de-munca/x/1", "description": "",
               "company": "", "location": "Romania", "email_hr": ""}
        with patch("scrapers.ro_boards.httpx.get") as get:
            get.return_value = MagicMock(text=_detail("<p>Cautam un dezvoltator Python.</p>"),
                                         status_code=200)
            _scraper(tmp_path)._enrich_from_detail(job)
        assert "dezvoltator Python" in job["description"]
        assert "<p>" not in job["description"]          # tag-urile HTML sunt curățate

    def test_email_is_extracted_from_the_description(self, tmp_path):
        job = {"url": "https://www.ejobs.ro/user/locuri-de-munca/x/1", "description": "",
               "company": "", "location": "Romania", "email_hr": ""}
        with patch("scrapers.ro_boards.httpx.get") as get:
            get.return_value = MagicMock(
                text=_detail("Trimite CV la cariere@acme.ro"), status_code=200)
            _scraper(tmp_path)._enrich_from_detail(job)
        assert job["email_hr"] == "cariere@acme.ro"

    def test_boilerplate_email_is_not_used(self, tmp_path):
        """Aceeași regulă ca pe jobspy: `privacy@` nu e adresă de candidatură."""
        job = {"url": "https://www.ejobs.ro/user/locuri-de-munca/x/1", "description": "",
               "company": "", "location": "Romania", "email_hr": ""}
        with patch("scrapers.ro_boards.httpx.get") as get:
            get.return_value = MagicMock(
                text=_detail("GDPR: scrie la privacy@acme.ro"), status_code=200)
            _scraper(tmp_path)._enrich_from_detail(job)
        assert job["email_hr"] == ""

    def test_company_and_location_fill_only_when_missing(self, tmp_path):
        job = {"url": "https://www.ejobs.ro/user/locuri-de-munca/x/1", "description": "",
               "company": "DEJA STIUTA", "location": "Romania", "email_hr": ""}
        with patch("scrapers.ro_boards.httpx.get") as get:
            get.return_value = MagicMock(text=_detail("text", company="ALTA SRL"),
                                         status_code=200)
            _scraper(tmp_path)._enrich_from_detail(job)
        assert job["company"] == "DEJA STIUTA"          # nu suprascrie ce știm deja
        assert job["location"] == "București"           # „Romania" e doar implicit

    def test_detail_failure_leaves_the_job_usable(self, tmp_path):
        """Best-effort: o pagină de detaliu căzută nu are voie să arunce —
        jobul rămâne cu titlu și URL, doar fără descriere."""
        job = {"url": "https://www.ejobs.ro/user/locuri-de-munca/x/1", "description": "",
               "company": "ACME", "location": "Romania", "email_hr": ""}
        with patch("scrapers.ro_boards.httpx.get", side_effect=RuntimeError("timeout")):
            _scraper(tmp_path)._enrich_from_detail(job)
        assert job["description"] == ""
        assert job["company"] == "ACME"

    def test_detail_without_jobposting_is_not_an_error(self, tmp_path):
        job = {"url": "https://www.ejobs.ro/user/locuri-de-munca/x/1", "description": "",
               "company": "ACME", "location": "Romania", "email_hr": ""}
        with patch("scrapers.ro_boards.httpx.get") as get:
            get.return_value = MagicMock(text="<html><body>nimic</body></html>", status_code=200)
            _scraper(tmp_path)._enrich_from_detail(job)
        assert job["description"] == ""

    def test_job_location_as_list_is_handled(self, tmp_path):
        """eJobs livrează `jobLocation` ca LISTĂ, nu ca dict (verificat live
        2026-08-22 pe /devops-engineer/1977087). Fixture-ul folosea dict, deci
        testele treceau în timp ce producția crăpa: `.get("address")` pe listă
        dă AttributeError, înghițit de `except Exception`. Anunțul se raporta
        `unavailable` DEȘI descrierea se scrisese deja, iar locația și data se
        pierdeau — restul funcției nu mai rula."""
        posting = json.dumps({
            "@context": "https://schema.org",
            "@type": "JobPosting",
            "title": "DevOps Engineer",
            "description": "Cautam DevOps. CV la cariere@acme.ro",
            "datePosted": "2026-08-04",
            "hiringOrganization": {"@type": "Organization", "name": "URBIO"},
            "jobLocation": [{"@type": "Place",
                             "address": {"@type": "PostalAddress",
                                         "addressLocality": "Iași (Iași)"}}],
        })
        job = {"url": "https://www.ejobs.ro/user/locuri-de-munca/x/1", "description": "",
               "company": "", "location": "Romania", "email_hr": ""}
        with patch("scrapers.ro_boards.httpx.get") as get:
            get.return_value = MagicMock(text=_DETAIL_HTML % posting, status_code=200)
            status = _scraper(tmp_path)._enrich_from_detail(job)

        assert status == "ok"                       # nu „unavailable"
        assert job["location"] == "Iași (Iași)"     # locația nu se mai pierde
        assert job["date_posted"] == "2026-08-04"   # codul de după nu mai e sărit
        assert job["email_hr"] == "cariere@acme.ro"

    def test_hiring_organization_as_list_is_handled(self, tmp_path):
        """Aceeași formă de JSON-LD admite listă și pentru firmă."""
        posting = json.dumps({
            "@context": "https://schema.org",
            "@type": "JobPosting",
            "description": "text",
            "datePosted": "2026-08-04",
            "hiringOrganization": [{"@type": "Organization", "name": "URBIO"}],
            "jobLocation": {"@type": "Place",
                            "address": {"@type": "PostalAddress",
                                        "addressLocality": "Cluj"}},
        })
        job = {"url": "https://www.ejobs.ro/user/locuri-de-munca/x/1", "description": "",
               "company": "", "location": "Romania", "email_hr": ""}
        with patch("scrapers.ro_boards.httpx.get") as get:
            get.return_value = MagicMock(text=_DETAIL_HTML % posting, status_code=200)
            status = _scraper(tmp_path)._enrich_from_detail(job)

        assert status == "ok"
        assert job["company"] == "URBIO"


class TestDetailRateLimiting:
    """eJobs limitează agresiv: măsurat 2026-08-06, cu 4 workeri 81 din 106
    cereri au primit 429. Fiindcă `raise_for_status()` nu era apelat, un 429
    arăta identic cu o pagină fără `JobPosting` — scraperul raporta „0 erori"
    cu 76% din îmbogățire pierdută."""

    def test_429_is_reported_not_swallowed(self, tmp_path):
        market = _market()
        html = (FIXTURES / "ejobs_sample.html").read_text(encoding="utf-8")

        def fake_get(url, **kwargs):
            resp = MagicMock()
            resp.raise_for_status.return_value = None
            if "/user/locuri-de-munca/" in url:      # pagină de detaliu
                resp.status_code = 429
                resp.text = ""
            else:                                    # pagină de listă
                resp.status_code = 200
                resp.text = html
            return resp

        with patch("scrapers.ro_boards.httpx.get", side_effect=fake_get):
            jobs, errors = _scraper(tmp_path).scrape_market(market)

        assert len(jobs) >= EXPECTED_MIN_JOBS       # anunțurile NU se pierd
        assert any("429" in e for e in errors)
        assert all(e.startswith("[avertisment]") for e in errors)

    def test_rate_limit_stops_further_requests(self, tmp_path):
        """Odată limita activă, cererile următoare primesc 429 oricum —
        insistența doar prelungește blocarea."""
        market = _market()
        html = (FIXTURES / "ejobs_sample.html").read_text(encoding="utf-8")
        detail_calls = []

        def fake_get(url, **kwargs):
            resp = MagicMock()
            resp.raise_for_status.return_value = None
            if "/user/locuri-de-munca/" in url:
                detail_calls.append(url)
                resp.status_code = 429
                resp.text = ""
            else:
                resp.status_code = 200
                resp.text = html
            return resp

        with patch("scrapers.ro_boards.httpx.get", side_effect=fake_get):
            _scraper(tmp_path).scrape_market(market)

        # Se oprește mult sub cele 40 de anunțuri ale paginii.
        assert len(detail_calls) < 20

    def test_no_warning_when_everything_is_enriched(self, tmp_path):
        market = _market()
        html = (FIXTURES / "ejobs_sample.html").read_text(encoding="utf-8")

        def fake_get(url, **kwargs):
            resp = MagicMock()
            resp.raise_for_status.return_value = None
            resp.status_code = 200
            resp.text = _detail("Descriere completa.") if "/user/locuri-de-munca/" in url else html
            return resp

        with patch("scrapers.ro_boards.httpx.get", side_effect=fake_get):
            jobs, errors = _scraper(tmp_path).scrape_market(market)

        assert errors == []
        assert all(j["description"] for j in jobs)
