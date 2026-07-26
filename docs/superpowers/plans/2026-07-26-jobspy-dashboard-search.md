# Căutare JobSpy în dashboard — plan de implementare

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Aduce căutarea JobSpy în dashboard-ul Django (preset din `settings.yaml` + override, staging, import selectiv în `Job`) și închide cele trei gap-uri față de upstream `speedyapply/JobSpy`.

**Architecture:** `SearchQuery` devine unitatea de lucru a scraperului, iar `JobSpyScraper.scrape_query()` rămâne singurul loc care apelează `scrape_jobs()` — calea CLI (`scrape_market`) devine un wrapper peste ea. Dashboard-ul primește `search_bridge.py` (același tipar ca `sheet_bridge`/`generation_bridge`: nicio excepție nu urcă spre views), care rulează căutarea într-un thread de fundal și scrie în două modele noi de staging (`SearchRun`, `SearchResult`). Importul în `Job` e o simplă copiere de câmpuri — fără apel AI, pentru că JobSpy livrează deja structura.

**Tech Stack:** Python 3.12, `python-jobspy` 1.1.82, Django 6.0, SQLite, `unittest` (Django) + `pytest` (pipeline), zero JS framework, zero build step.

## Global Constraints

- Comentarii, docstring-uri, mesaje UI și mesaje de commit **în română**.
- Commit-uri cu tag `JOB-46` (ex. `feat(scrapers): ... (JOB-46)`).
- Zero rețea în teste: `scrape_jobs` se mock-uiește mereu.
- Două comenzi de test, ambele trebuie să treacă:
  `python -m pytest -q` și `python dashboard/manage.py test jobs`
  (rulate din rădăcina repo-ului, cu `.venv` activ).
- `SeenIndex` e folosit **read-only** din dashboard — nu se scrie niciodată în `data/seen_jobs.json`.
- Nu se atinge col M „Status" și col P „Sende Befehl" din Sheet.
- Un `Job` în `generated` / `sending` / `sent` nu se suprascrie niciodată.
- CSS: doar adaosuri în `base.html`; nicio regulă existentă modificată.
- `CareerPageScraper` rămâne neatins.

**Abatere conștientă de la spec:** spec-ul promitea o pastilă separată
„întrerupt" pentru rulările moarte. Planul le trece în `status=error` cu motivul
„Rulare întreruptă…" în `errors_json` — aceeași informație, fără o a patra stare
de întreținut. Dacă vrei pastila separată, e o stare nouă în `SearchRun` plus o
clasă CSS, nu o rescriere.

## Structura de fișiere

| Fișier | Responsabilitate |
|---|---|
| `scrapers/base.py` | + `SearchQuery`, `Market.to_query()`; `make_job()` primește `market_key`+`language`+`raw` |
| `scrapers/jobspy_scraper.py` | `scrape_query()` = motor unic; sanitizare parametri per site; normalizare îmbogățită; `SUPPORTED_SITES` |
| `scrapers/remote_boards.py`, `scrapers/ro_boards.py` | adaptate la noua semnătură `make_job` |
| `agents/scraper_agent.py` | avertismentele `[avertisment]` nu coboară statusul |
| `config/settings.yaml` | + `google_search_term`, `linkedin_fetch_description` |
| `store/seen.py` | + `SeenIndex.decision_for()` |
| `dashboard/jobs/models.py` | + `SearchRun`, `SearchResult` |
| `dashboard/jobs/search_bridge.py` | preseturi, construcția interogării, worker de fundal, gărzi, import în `Job` |
| `dashboard/jobs/forms.py` | + `SearchForm` |
| `dashboard/jobs/views.py`, `urls.py` | 3 view-uri noi: listă+start, detaliu rulare, import |
| `dashboard/jobs/templates/jobs/cauta.html` | formular + istoric rulări |
| `dashboard/jobs/templates/jobs/cauta_run.html` | status rulare + tabel rezultate + bară import |
| `dashboard/jobs/templates/jobs/base.html` | + link nav „Căutare", + clase CSS noi |

---

### Task 1: `SearchQuery` + `Market.to_query()` + `make_job` fără `Market`

Fundația: interogarea devine un obiect propriu, iar jobul canonic nu mai are nevoie de tot obiectul `Market` — doar de cheia pieței și limbă. Zero schimbare de comportament.

**Files:**
- Modify: `scrapers/base.py`
- Modify: `scrapers/jobspy_scraper.py` (apelul `make_job`)
- Modify: `scrapers/remote_boards.py:103,123,161`
- Modify: `scrapers/ro_boards.py:122`
- Test: `tests/test_scrapers_base.py` (nou), `tests/test_market.py` (adaptat)

**Interfaces:**
- Produces:
  - `SearchQuery(keyword_groups, sites, location, country, language, market_key="adhoc", remote=False, hours_old=72, results_wanted=50, job_type="", distance=None, google_search_term="", linkedin_fetch_description=False)` — dataclass frozen în `scrapers/base.py`
  - `Market.to_query(scrape_config: dict) -> SearchQuery`
  - `make_job(*, source, platform, title, company, location, url, description, market_key: str, language: str, date_posted="", salary_min=None, salary_max=None, email_hr="", raw: dict | None = None) -> dict` — dictul are cheia nouă `"raw"`

- [ ] **Step 1: Scrie testele care picӑ**

Creează `tests/test_scrapers_base.py`:

```python
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
```

- [ ] **Step 2: Rulează testele ca să vezi că pică**

Run: `python -m pytest tests/test_scrapers_base.py -q`
Expected: FAIL — `ImportError: cannot import name 'SearchQuery' from 'scrapers.base'`

- [ ] **Step 3: Adaugă `SearchQuery` în `scrapers/base.py`**

Imediat după definiția `Market` (după `from_config`), adaugă în același fișier:

```python
@dataclass(frozen=True)
class SearchQuery:
    """Parametrii unei singure căutări JobSpy.

    Există ca să existe UN SINGUR loc care apelează `scrape_jobs()`: piețele
    din YAML se transformă în `SearchQuery` (`Market.to_query`), iar căutările
    ad-hoc din dashboard construiesc direct un `SearchQuery`. Fără el,
    dashboard-ul ar avea nevoie de al doilea wrapper JobSpy — și fiecare bug
    upstream ar trebui reparat în două locuri.
    """

    keyword_groups: list[list[str]]
    sites: list[str]
    location: str
    country: str                       # → country_indeed
    language: str                      # → col „Sprache" → limba Anschreiben
    market_key: str = "adhoc"
    remote: bool = False
    hours_old: int | None = 72
    results_wanted: int = 50
    # Filtrele opționale rămân goale ca să fie OMISE din apel: un `job_type`
    # gol trimis la jobspy ar fi interpretat ca filtru activ.
    job_type: str = ""
    distance: int | None = None
    google_search_term: str = ""
    linkedin_fetch_description: bool = False
```

- [ ] **Step 4: Adaugă `Market.to_query()`**

În clasa `Market`, după `from_config`:

```python
    def to_query(self, scrape_config: dict) -> "SearchQuery":
        """Interogarea corespunzătoare acestei piețe, cu limitele din YAML."""
        return SearchQuery(
            keyword_groups=self.keyword_groups,
            sites=list(scrape_config.get("jobspy_sites", ["indeed"])),
            # Piața fără orașe caută la nivel de țară — Indeed întoarce
            # oricum toate orașele.
            location=self.locations[0] if self.locations else self.country,
            country=self.country,
            language=self.language,
            market_key=self.key,
            remote=self.remote,
            hours_old=int(scrape_config.get("hours_old", 72)),
            results_wanted=int(scrape_config.get("max_results_per_call", 50)),
            google_search_term=str(scrape_config.get("google_search_term", "") or ""),
            linkedin_fetch_description=bool(
                scrape_config.get("linkedin_fetch_description", False)
            ),
        )
```

`SearchQuery` e definit după `Market`, deci adnotarea trebuie să fie string (`"SearchQuery"`) — e deja.

- [ ] **Step 5: Schimbă semnătura `make_job`**

Înlocuiește parametrul `market: Market` cu `market_key`/`language` și adaugă `raw`:

```python
def make_job(
    *,
    source: str,
    platform: str,
    title: str,
    company: str,
    location: str,
    url: str,
    description: str,
    market_key: str,
    language: str,
    date_posted: str = "",
    salary_min=None,
    salary_max=None,
    email_hr: str = "",
    raw: dict | None = None,
) -> dict:
    """Construiește forma canonică a unui job, indiferent de sursă.

    `language` vine din piață/interogare și ajunge în col „Sprache" din Sheet,
    care comandă limba scrisorii — de asta un job din RO nu primește
    Anschreiben în germană.

    `raw` ține câmpurile fără coloană în Sheet (is_remote, job_type, currency,
    company_url) — utile în dashboard, ignorate la push.
    """
```

În corpul funcției, înlocuiește `"market": market.key,` cu `"market": market_key,`,
`"language": market.language,` cu `"language": language,` și adaugă înainte de
`"applied": False`:

```python
        "raw": raw or {},
```

- [ ] **Step 6: Adaptează cele 5 call-site-uri**

În `scrapers/remote_boards.py` (3 locuri) și `scrapers/ro_boards.py` (1 loc),
înlocuiește linia `market=market,` cu:

```python
                    market_key=market.key,
                    language=market.language,
```

În `scrapers/jobspy_scraper.py`, în `_normalize_job`, înlocuiește `market=market,` cu:

```python
                market_key=market.key,
                language=market.language,
```

- [ ] **Step 7: Adaptează `tests/test_market.py::TestMakeJob`**

Clasa are 7 apeluri `make_job(...)` cu `market=market,` (liniile ~86-125).
În fiecare, înlocuiește `market=market,` cu:

```python
            market_key=market.key, language=market.language,
```

Fixture-ul `market` rămâne neschimbat și **nicio asserțiune nu se modifică** —
`job["market"]` și `job["language"]` au exact aceleași valori ca înainte.

- [ ] **Step 8: Rulează toată suita**

Run: `python -m pytest -q`
Expected: PASS

- [ ] **Step 9: Commit**

```bash
git add scrapers/base.py scrapers/jobspy_scraper.py scrapers/remote_boards.py \
        scrapers/ro_boards.py tests/test_scrapers_base.py tests/test_market.py
git commit -m "refactor(scrapers): SearchQuery + make_job fără obiect Market (JOB-46)"
```

---

### Task 2: `scrape_query()` ca motor unic + sanitizare per site

Aici se repară bug-ul Indeed. `scrape_market` devine wrapper, deci calea CLI păstrează contractul `(jobs, errors)`.

**Files:**
- Modify: `scrapers/jobspy_scraper.py`
- Test: `tests/test_jobspy_scraper.py`

**Interfaces:**
- Consumes: `SearchQuery`, `Market.to_query()` (Task 1)
- Produces:
  - `scrapers.jobspy_scraper.SUPPORTED_SITES: tuple[str, ...]` — cele 8 site-uri upstream
  - `scrapers.jobspy_scraper.WARNING_PREFIX: str` = `"[avertisment]"`
  - `JobSpyScraper.scrape_query(q: SearchQuery) -> tuple[list[dict], list[str]]`
  - `JobSpyScraper.scrape_market(market: Market) -> tuple[list[dict], list[str]]` (neschimbat ca semnătură)

- [ ] **Step 1: Scrie testele care pică**

În `tests/test_jobspy_scraper.py`, adaugă importurile și un helper la nivel de modul (după `_market`):

```python
from scrapers.base import SearchQuery
from scrapers.jobspy_scraper import SUPPORTED_SITES, WARNING_PREFIX


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
```

Adaugă clasele de test:

```python
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
```

**Actualizează testul existent** `TestScrapeMarket::test_passes_country_and_remote_from_market` — el asertează azi `kwargs["hours_old"] == 72` pentru `ro_remote`, ceea ce e chiar bug-ul reparat. Înlocuiește-l cu:

```python
    def test_passes_country_and_remote_from_market(self, tmp_path):
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
```

- [ ] **Step 2: Rulează ca să vezi că pică**

Run: `python -m pytest tests/test_jobspy_scraper.py -q`
Expected: FAIL — `ImportError: cannot import name 'SUPPORTED_SITES'`

- [ ] **Step 3: Rescrie motorul în `scrapers/jobspy_scraper.py`**

Înlocuiește importul și constantele de sus:

```python
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from scrapers.base import Market, SearchQuery, make_job

try:
    from jobspy import scrape_jobs
    HAS_JOBSPY = True
except ImportError:
    scrape_jobs = None
    HAS_JOBSPY = False

# Câte apeluri jobspy rulează simultan. Peste 4 crește rata de 429.
_MAX_WORKERS = 4

# Site-urile suportate de upstream. Hardcodate (nu citite din enum-ul jobspy)
# ca formularul din dashboard să le poată afișa și când jobspy nu e instalat.
SUPPORTED_SITES = (
    "linkedin", "indeed", "zip_recruiter", "glassdoor",
    "google", "bayt", "naukri", "bdjobs",
)

# Prefix pentru mesajele care raportează o degradare, nu un eșec. `ScraperAgent`
# le exclude din erorile blocante, iar dashboard-ul le arată separat de erori.
WARNING_PREFIX = "[avertisment]"
```

Înlocuiește `scrape_market` și `_call` cu:

```python
    def scrape_market(self, market: Market) -> tuple[list[dict], list[str]]:
        """Caută joburi pentru o piață (calea pipeline-ului CLI)."""
        return self.scrape_query(market.to_query(self.scrape_config))

    def scrape_query(self, q: SearchQuery) -> tuple[list[dict], list[str]]:
        """Rulează o interogare. Întoarce `(joburi, mesaje)`.

        Mesajele nu se ridică drept excepții: un site blocat nu trebuie să
        anuleze rezultatele celorlalte. Cele cu prefix `[avertisment]` descriu
        o degradare (un filtru renunțat), nu un eșec.
        """
        if not HAS_JOBSPY:
            raise ImportError(
                "python-jobspy nu este instalat. Rulează: pip install python-jobspy"
            )

        jobs: list[dict] = []
        errors: list[str] = []

        sites = []
        for site in q.sites:
            if site in SUPPORTED_SITES:
                sites.append(site)
            else:
                errors.append(f"[{q.market_key}/{site}] site necunoscut, ignorat")

        units = [(group, site) for group in q.keyword_groups for site in sites]

        with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as pool:
            futures = {
                pool.submit(self._call, q, group, site): (group, site)
                for group, site in units
            }
            for future in as_completed(futures):
                group, site = futures[future]
                try:
                    found, warnings = future.result()
                    jobs.extend(found)
                    errors.extend(warnings)
                except Exception as e:
                    errors.append(f"[{q.market_key}/{site}] {group[0]}: {e}")

        return jobs, self._dedupe_messages(errors)

    @staticmethod
    def _dedupe_messages(messages: list[str]) -> list[str]:
        """Același avertisment se repetă per (grup × site) — îl raportăm o dată."""
        seen: set[str] = set()
        out = []
        for m in messages:
            if m in seen:
                continue
            seen.add(m)
            out.append(m)
        return out

    def _call(self, q: SearchQuery, group: list[str], site: str) -> tuple[list[dict], list[str]]:
        """Un singur apel jobspy. Excepțiile urcă — le prinde `scrape_query`."""
        # Grupul de keywords devine o interogare booleană: un apel în loc de
        # câte unul per keyword.
        search_term = " OR ".join(f'"{k}"' for k in group)

        kwargs: dict[str, Any] = {
            "site_name": [site],
            "search_term": search_term,
            "location": q.location,
            "results_wanted": int(q.results_wanted),
            "country_indeed": q.country,
            "is_remote": q.remote,
        }
        # Filtrele opționale se OMIT când sunt goale: trimise ca "" / None,
        # jobspy le-ar trata ca filtre active.
        if q.hours_old is not None:
            kwargs["hours_old"] = int(q.hours_old)
        if q.job_type:
            kwargs["job_type"] = q.job_type
        if q.distance is not None:
            kwargs["distance"] = int(q.distance)
        if site == "google" and q.google_search_term:
            kwargs["google_search_term"] = q.google_search_term
        if site == "linkedin" and q.linkedin_fetch_description:
            kwargs["linkedin_fetch_description"] = True

        kwargs, warnings = self._sanitize(site, kwargs, q)

        df = scrape_jobs(**kwargs)

        if df is None or df.empty:
            return [], warnings

        jobs = []
        for _, row in df.iterrows():
            job = self._normalize_job(row, q)
            if job:
                jobs.append(job)
        return jobs, warnings

    @staticmethod
    def _sanitize(site: str, kwargs: dict, q: SearchQuery) -> tuple[dict, list[str]]:
        """Elimină combinațiile de filtre pe care site-ul nu le acceptă.

        Upstream e explicit: pe Indeed poți folosi DOAR unul din `hours_old` /
        `(job_type & is_remote)` / `easy_apply`. Trimise împreună, unul cade
        silențios — motiv pentru care piața `ro_remote` credea că filtrează
        pe vechime, dar nu filtra.
        """
        warnings: list[str] = []
        if (
            site == "indeed"
            and kwargs.get("hours_old") is not None
            and (kwargs.get("is_remote") or kwargs.get("job_type"))
        ):
            kwargs.pop("hours_old")
            warnings.append(
                f"{WARNING_PREFIX} [{q.market_key}/indeed] am renunțat la "
                "hours_old: Indeed nu îl combină cu is_remote/job_type "
                "(filtrul remote are prioritate)"
            )
        return kwargs, warnings
```

- [ ] **Step 4: Adaptează `_normalize_job` la `SearchQuery`**

Semnătura primește `q: SearchQuery` în loc de `market: Market`:

```python
    def _normalize_job(self, row: Any, q: SearchQuery) -> dict | None:
        """Transformă un rând jobspy în forma canonică."""
        try:
            url = str(row.get("job_url", "") or "").strip()
            # Fără URL nu există cheie de match la upsert („Apply URL"):
            # n8n ar cădea pe append și ar adăuga rânduri goale.
            if not url:
                return None

            platform = str(row.get("site", "unknown"))
            return make_job(
                source=f"{platform}-{q.market_key}",
                platform=platform,
                title=str(row.get("title", "")),
                company=str(row.get("company", "")),
                location=str(row.get("location", "") or q.country),
                url=url,
                description=str(row.get("description", "") or ""),
                market_key=q.market_key,
                language=q.language,
                date_posted=str(row.get("date_posted", "") or ""),
                salary_min=row.get("min_amount"),
                salary_max=row.get("max_amount"),
            )
        except Exception:
            return None
```

Testele existente din `TestNormalizeJob` apelează `_normalize_job(ROW, _market("de"))`. Actualizează-le să paseze o interogare:

```python
def _q(key="de"):
    return _market(key).to_query(CFG["scraping"])
```

și înlocuiește în cele 4 teste `_market("de")` → `_q("de")`, `_market("ro_remote")` → `_q("ro_remote")`. Asserțiunile rămân identice.

- [ ] **Step 5: Rulează testele**

Run: `python -m pytest tests/test_jobspy_scraper.py tests/test_scrapers_base.py -q`
Expected: PASS

- [ ] **Step 6: Rulează toată suita**

Run: `python -m pytest -q`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add scrapers/jobspy_scraper.py tests/test_jobspy_scraper.py
git commit -m "fix(scrapers): scrape_query + sanitizare filtre per site; Indeed nu mai pierde hours_old silențios (JOB-46)"
```

---

### Task 3: Normalizare îmbogățită — email HR, câmpuri `raw`, NaN

JobSpy întoarce coloana `emails` (extrasă din descriere) — exact câmpul fără care „Transmite" refuză să trimită. În plus, celulele lipsă din pandas sunt `NaN`, care e **truthy**, deci `str(x or "")` produce literal `"nan"`.

**Files:**
- Modify: `scrapers/jobspy_scraper.py`
- Modify: `config/settings.yaml`
- Test: `tests/test_jobspy_scraper.py`

**Interfaces:**
- Consumes: `make_job(..., raw=...)` (Task 1), `_normalize_job(row, q)` (Task 2)
- Produces: joburile au `email_hr` completat când JobSpy găsește un email, și `raw` cu `is_remote`/`job_type`/`currency`/`interval`/`company_url`

- [ ] **Step 1: Scrie testele care pică**

```python
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
```

- [ ] **Step 2: Rulează ca să vezi că pică**

Run: `python -m pytest tests/test_jobspy_scraper.py -k Enrichment -q`
Expected: FAIL — `assert '' == 'jobs@testco.de'`

- [ ] **Step 3: Adaugă helperii în `scrapers/jobspy_scraper.py`**

La nivel de modul, după constante:

```python
def _text(value: Any) -> str:
    """Text curat dintr-o celulă pandas.

    `NaN` e truthy, deci `str(value or "")` producea literal „nan" în
    descrieri și companii.
    """
    if value is None:
        return ""
    if isinstance(value, float) and value != value:      # NaN != NaN
        return ""
    return str(value).strip()


def _first_email(value: Any) -> str:
    """Primul email plauzibil din coloana `emails` (listă sau string)."""
    if isinstance(value, str):
        candidates = [value]
    elif isinstance(value, (list, tuple, set)):
        candidates = list(value)
    else:
        return ""
    for candidate in candidates:
        text = _text(candidate)
        if "@" in text and "." in text.rsplit("@", 1)[-1]:
            return text
    return ""
```

- [ ] **Step 4: Folosește-i în `_normalize_job`**

Înlocuiește corpul `try` cu:

```python
            url = _text(row.get("job_url"))
            # Fără URL nu există cheie de match la upsert („Apply URL"):
            # n8n ar cădea pe append și ar adăuga rânduri goale.
            if not url:
                return None

            platform = _text(row.get("site")) or "unknown"
            return make_job(
                source=f"{platform}-{q.market_key}",
                platform=platform,
                title=_text(row.get("title")),
                company=_text(row.get("company")),
                location=_text(row.get("location")) or q.country,
                url=url,
                description=_text(row.get("description")),
                market_key=q.market_key,
                language=q.language,
                date_posted=_text(row.get("date_posted")),
                salary_min=row.get("min_amount"),
                salary_max=row.get("max_amount"),
                email_hr=_first_email(row.get("emails")),
                raw={
                    "is_remote": row.get("is_remote"),
                    "job_type": _text(row.get("job_type")),
                    "currency": _text(row.get("currency")),
                    "interval": _text(row.get("interval")),
                    "company_url": _text(row.get("company_url")),
                },
            )
```

- [ ] **Step 5: Deblochează site-urile și noile opțiuni în `config/settings.yaml`**

Înlocuiește blocul `jobspy_sites` (linia ~83) cu:

```yaml
  # Site-uri jobspy folosite per piață. Suportate upstream:
  # indeed, linkedin, glassdoor, google, zip_recruiter, bayt, naukri, bdjobs
  # (zip_recruiter e doar US/CA; bayt/naukri/bdjobs sunt alte regiuni).
  jobspy_sites: ["indeed", "linkedin"]

  # Google Jobs cere o sintaxă proprie, identică cu ce ai tasta în browser
  # (ex. „Python Developer jobs near Berlin since yesterday"). Gol = Google
  # nu primește termen special, deci dă rezultate slabe — completează-l dacă
  # adaugi „google" în jobspy_sites.
  google_search_term: ""

  # Descrierile LinkedIn cer un request per job: lent, dar altfel joburile
  # LinkedIn ajung la AI fără descriere, deci fără material pentru Anschreiben.
  linkedin_fetch_description: false
```

- [ ] **Step 6: Rulează testele**

Run: `python -m pytest -q`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add scrapers/jobspy_scraper.py config/settings.yaml tests/test_jobspy_scraper.py
git commit -m "feat(scrapers): email HR din coloana emails, câmpuri raw, NaN nu mai devine \"nan\" (JOB-46)"
```

---

### Task 4: Avertismentele nu coboară statusul agentului

Un filtru renunțat e o degradare raportată, nu un eșec de sursă. Fără asta, fiecare rulare pe `ro_remote` ar raporta PARTIAL.

**Files:**
- Modify: `agents/scraper_agent.py:64-67`
- Test: `tests/test_scraper_agent.py`

**Interfaces:**
- Consumes: `WARNING_PREFIX` (Task 2)
- Produces: `AgentResult.errors` conține avertismentele, dar `status` rămâne SUCCESS dacă nu există erori reale

- [ ] **Step 1: Scrie testul care pică**

`tests/test_scraper_agent.py` are deja un fixture `agent` cu toate sursele
mock-uite, unde `agent.jobspy.scrape_market.return_value` e tuplul
`(joburi, erori)`. Folosește-l — nu construi un `ScraperAgent` nou.

Adaugă importul la nivel de modul:

```python
from scrapers.jobspy_scraper import WARNING_PREFIX
```

și clasa de test:

```python
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
```

- [ ] **Step 2: Rulează ca să vezi că pică**

Run: `python -m pytest tests/test_scraper_agent.py -k Warnings -q`
Expected: FAIL — `assert AgentStatus.SUCCESS == AgentStatus.PARTIAL`

- [ ] **Step 3: Exclude avertismentele din erorile blocante**

În `agents/scraper_agent.py`, adaugă la importuri:

```python
from scrapers.jobspy_scraper import WARNING_PREFIX
```

și înlocuiește blocul de la linia ~64:

```python
                # Blocante = erorile surselor ne-best-effort, mai puțin cele
                # ale sub-site-urilor declarate best-effort (ex. linkedin) și
                # avertismentele (filtre renunțate — degradare, nu eșec).
                if source not in market.best_effort_sources:
                    blocking_errors.extend(
                        e for e in errors
                        if not e.startswith(WARNING_PREFIX)
                        and not self._is_best_effort_error(e, market)
                    )
```

- [ ] **Step 4: Rulează testele**

Run: `python -m pytest -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add agents/scraper_agent.py tests/test_scraper_agent.py
git commit -m "fix(agents): avertismentele de filtre nu mai coboară statusul la PARTIAL (JOB-46)"
```

---

### Task 5: Modelele `SearchRun` și `SearchResult`

**Files:**
- Modify: `dashboard/jobs/models.py`
- Create: `dashboard/jobs/migrations/0002_searchrun_searchresult.py` (generată)
- Modify: `dashboard/jobs/admin.py`
- Test: `dashboard/jobs/tests.py`

**Interfaces:**
- Produces:
  - `SearchRun(label, params_json, status, started_at, finished_at, found_count, imported_count, warnings_json, errors_json)`; `status ∈ {"running","done","error"}`; `related_name="results"`; property `duration_sec: int | None`
  - `SearchResult(run, url, title, company, location, platform, date_posted, language, description, email_hr, salary_min, salary_max, prescore, prescore_reasons_json, seen_state, imported_job, raw_json)`

- [ ] **Step 1: Scrie testele care pică**

În `dashboard/jobs/tests.py`, adaugă la importuri `SearchResult, SearchRun` din `jobs.models` și clasa:

```python
class SearchModelTests(TestCase):
    def _run(self, **over):
        data = dict(label="de · Python Developer", params_json={"preset": "de"},
                    status="running")
        data.update(over)
        return SearchRun.objects.create(**data)

    def test_run_defaults(self):
        run = self._run()
        self.assertEqual(run.found_count, 0)
        self.assertEqual(run.imported_count, 0)
        self.assertEqual(run.warnings_json, [])
        self.assertEqual(run.errors_json, [])
        self.assertIsNone(run.finished_at)
        self.assertIsNotNone(run.started_at)

    def test_runs_are_newest_first(self):
        first = self._run(label="prima")
        second = self._run(label="a doua")
        self.assertEqual(
            list(SearchRun.objects.values_list("pk", flat=True)),
            [second.pk, first.pk],
        )

    def test_duration_is_none_while_running(self):
        self.assertIsNone(self._run().duration_sec)

    def test_duration_counts_seconds_when_finished(self):
        from datetime import timedelta
        run = self._run()
        run.finished_at = run.started_at + timedelta(seconds=42)
        run.save()
        self.assertEqual(run.duration_sec, 42)

    def test_results_are_sorted_by_score_desc(self):
        run = self._run()
        SearchResult.objects.create(run=run, url="https://a.co/1", prescore=30)
        SearchResult.objects.create(run=run, url="https://a.co/2", prescore=80)
        self.assertEqual(
            list(run.results.values_list("prescore", flat=True)), [80, 30]
        )

    def test_same_url_twice_in_one_run_is_rejected(self):
        from django.db import IntegrityError
        run = self._run()
        SearchResult.objects.create(run=run, url="https://a.co/1")
        with self.assertRaises(IntegrityError):
            SearchResult.objects.create(run=run, url="https://a.co/1")

    def test_same_url_in_two_runs_is_allowed(self):
        """Fiecare rulare e un snapshot propriu — dublura se vede din badge."""
        SearchResult.objects.create(run=self._run(), url="https://a.co/1")
        SearchResult.objects.create(run=self._run(), url="https://a.co/1")
        self.assertEqual(SearchResult.objects.count(), 2)

    def test_deleting_run_deletes_its_results(self):
        run = self._run()
        SearchResult.objects.create(run=run, url="https://a.co/1")
        run.delete()
        self.assertEqual(SearchResult.objects.count(), 0)

    def test_deleting_imported_job_keeps_the_result(self):
        """Ștergerea unui Job nu trebuie să șteargă istoricul căutării."""
        job = Job.objects.create(url="https://a.co/1")
        res = SearchResult.objects.create(run=self._run(), url="https://a.co/1",
                                          imported_job=job)
        job.delete()
        res.refresh_from_db()
        self.assertIsNone(res.imported_job)
```

- [ ] **Step 2: Rulează ca să vezi că pică**

Run: `python dashboard/manage.py test jobs.tests.SearchModelTests`
Expected: FAIL — `ImportError: cannot import name 'SearchRun' from 'jobs.models'`

- [ ] **Step 3: Adaugă modelele în `dashboard/jobs/models.py`**

La finalul fișierului:

```python
class SearchRun(models.Model):
    """O căutare JobSpy = un snapshot. Rulează într-un thread de fundal."""

    RUNNING, DONE, ERROR = "running", "done", "error"

    label = models.CharField(max_length=200)
    # Parametrii exacți ai căutării, ca să poți repeta sau audita rularea.
    params_json = models.JSONField(default=dict)
    status = models.CharField(max_length=10, default=RUNNING)
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    # Rezultate salvate (nu se ascunde nimic sub prag) și câte au ajuns în Job.
    found_count = models.IntegerField(default=0)
    imported_count = models.IntegerField(default=0)
    # Avertismentele (filtre renunțate) sunt separate de erori: prima categorie
    # descrie o degradare, a doua un eșec de sursă.
    warnings_json = models.JSONField(default=list, blank=True)
    errors_json = models.JSONField(default=list, blank=True)

    class Meta:
        ordering = ["-started_at", "-pk"]

    def __str__(self):
        return f"{self.label} ({self.status})"

    @property
    def duration_sec(self) -> int | None:
        if not self.finished_at:
            return None
        return int((self.finished_at - self.started_at).total_seconds())


class SearchResult(models.Model):
    """Un rezultat de căutare, în staging — devine `Job` doar la import."""

    run = models.ForeignKey(SearchRun, related_name="results",
                            on_delete=models.CASCADE)
    url = models.URLField(max_length=500)
    title = models.CharField(max_length=300, blank=True)
    company = models.CharField(max_length=200, blank=True)
    location = models.CharField(max_length=200, blank=True)
    platform = models.CharField(max_length=40, blank=True)
    date_posted = models.CharField(max_length=40, blank=True)
    language = models.CharField(max_length=5, blank=True, default="")
    description = models.TextField(blank=True)
    email_hr = models.CharField(max_length=254, blank=True)
    salary_min = models.IntegerField(null=True, blank=True)
    salary_max = models.IntegerField(null=True, blank=True)
    # Scorul determinist din filters/relevance.py + motivele, ca să fie auditabil.
    prescore = models.IntegerField(default=0)
    prescore_reasons_json = models.JSONField(default=list, blank=True)
    # Decizia din store/seen.py: "" | aplicat | respins_prefiltru. Citit
    # read-only — dashboard-ul nu scrie în seen_jobs.json.
    seen_state = models.CharField(max_length=20, blank=True, default="")
    # SET_NULL: ștergerea unui Job nu trebuie să șteargă istoricul căutării.
    imported_job = models.ForeignKey("Job", null=True, blank=True,
                                     on_delete=models.SET_NULL,
                                     related_name="search_results")
    raw_json = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-prescore", "pk"]
        constraints = [
            models.UniqueConstraint(fields=["run", "url"], name="uniq_url_per_run"),
        ]

    def __str__(self):
        return f"{self.title or '(fără titlu)'} @ {self.company or '?'}"
```

- [ ] **Step 4: Generează și aplică migrarea**

```bash
python dashboard/manage.py makemigrations jobs
python dashboard/manage.py migrate
```

Expected: creează `0002_searchrun_searchresult.py` (numele exact poate varia).

- [ ] **Step 5: Înregistrează în admin**

În `dashboard/jobs/admin.py`, adaugă:

```python
from .models import SearchResult, SearchRun

admin.site.register(SearchRun)
admin.site.register(SearchResult)
```

(păstrează înregistrarea existentă a lui `Job`.)

- [ ] **Step 6: Rulează testele**

Run: `python dashboard/manage.py test jobs`
Expected: PASS (toate, inclusiv cele existente)

- [ ] **Step 7: Commit**

```bash
git add dashboard/jobs/models.py dashboard/jobs/admin.py \
        dashboard/jobs/migrations/ dashboard/jobs/tests.py
git commit -m "feat(dashboard): modele SearchRun + SearchResult pentru staging căutări (JOB-46)"
```

---

### Task 6: `SeenIndex.decision_for()` + worker-ul de căutare

**Files:**
- Modify: `store/seen.py`
- Create: `dashboard/jobs/search_bridge.py`
- Test: `tests/test_seen.py`, `dashboard/jobs/tests.py`

**Interfaces:**
- Consumes: `SearchQuery` (Task 1), `scrape_query` + `WARNING_PREFIX` + `SUPPORTED_SITES` (Task 2), modelele (Task 5), `load_pipeline_config()` din `.sheet_bridge`, `prescore()` din `filters.relevance`
- Produces:
  - `SeenIndex.decision_for(job: dict) -> str`
  - `search_bridge.presets() -> list[dict]` — `[{"key","label","location","country","language","remote","keywords","sites","hours_old","results_wanted"}]`
  - `search_bridge.query_from_params(params: dict) -> SearchQuery`
  - `search_bridge.label_for(params: dict) -> str`
  - `search_bridge.expire_stale_runs() -> int`
  - `search_bridge.start_search(params: dict) -> dict` — `{"ok": bool, "run": SearchRun | None, "error": str}`
  - `search_bridge.run_search_now(run_pk: int) -> None` — corpul sincron al worker-ului (apelabil din teste)

- [ ] **Step 1: Testul pentru `decision_for`**

În `tests/test_seen.py`, adaugă:

```python
class TestDecisionFor:
    def test_returns_recorded_decision(self, tmp_path):
        from store.seen import APLICAT, SeenIndex
        index = SeenIndex(tmp_path / "seen.json")
        job = {"url": "https://a.co/1", "company": "ACME", "title": "Python Dev"}
        index.mark(job, APLICAT)
        assert index.decision_for(job) == APLICAT

    def test_returns_empty_for_unseen_job(self, tmp_path):
        from store.seen import SeenIndex
        index = SeenIndex(tmp_path / "seen.json")
        assert index.decision_for({"url": "https://a.co/9"}) == ""
```

- [ ] **Step 2: Rulează ca să vezi că pică**

Run: `python -m pytest tests/test_seen.py -k DecisionFor -q`
Expected: FAIL — `AttributeError: 'SeenIndex' object has no attribute 'decision_for'`

- [ ] **Step 3: Adaugă `decision_for` în `store/seen.py`**

Imediat după `is_seen`:

```python
    def decision_for(self, job: dict) -> str:
        """Decizia înregistrată pentru job, sau "" dacă nu a fost văzut.

        `is_seen` spune doar dacă jobul e cunoscut; dashboard-ul trebuie să
        arate și DE CE (aplicat vs respins la prefiltru).
        """
        for key in self._keys(job):
            entry = self.entries.get(key)
            if isinstance(entry, dict) and entry.get("decision"):
                return str(entry["decision"])
        return ""
```

- [ ] **Step 4: Rulează**

Run: `python -m pytest tests/test_seen.py -q`
Expected: PASS

- [ ] **Step 5: Testele pentru worker (Django)**

În `dashboard/jobs/tests.py`:

```python
class SearchBridgeQueryTests(TestCase):
    def test_presets_come_from_settings_yaml(self):
        from jobs import search_bridge
        keys = [p["key"] for p in search_bridge.presets()]
        self.assertIn("de", keys)
        self.assertIn("ro_remote", keys)

    def test_preset_exposes_keywords_one_group_per_line(self):
        from jobs import search_bridge
        de = next(p for p in search_bridge.presets() if p["key"] == "de")
        self.assertIn("\n", de["keywords"])
        self.assertIn("Python Developer", de["keywords"])

    def test_query_from_params_parses_keyword_groups(self):
        from jobs import search_bridge
        q = search_bridge.query_from_params({
            "keywords": "Python Developer, Django Developer\nAI Engineer",
            "location": "Berlin", "country": "GERMANY", "language": "de",
            "sites": ["indeed"], "hours_old": 48, "results_wanted": 20,
        })
        self.assertEqual(q.keyword_groups,
                         [["Python Developer", "Django Developer"], ["AI Engineer"]])
        self.assertEqual(q.hours_old, 48)
        self.assertEqual(q.market_key, "adhoc")

    def test_label_summarises_the_search(self):
        from jobs import search_bridge
        label = search_bridge.label_for({
            "preset": "de", "keywords": "Python Developer\nAI Engineer",
            "location": "Berlin",
        })
        self.assertIn("Python Developer", label)
        self.assertIn("Berlin", label)


class SearchWorkerTests(TestCase):
    PARAMS = {
        "preset": "de", "keywords": "Python Developer", "location": "Berlin",
        "country": "GERMANY", "language": "de", "sites": ["indeed"],
        "hours_old": 72, "results_wanted": 10, "remote": False,
    }

    def _job(self, **over):
        job = {
            "url": "https://acme.de/jobs/1", "title": "Python Developer",
            "company": "ACME", "location": "Berlin", "platform": "indeed",
            "description": "Django and Python backend", "email_hr": "hr@acme.de",
            "date_posted": "2026-07-20", "language": "de",
            "salary_min": None, "salary_max": None, "raw": {"currency": "EUR"},
        }
        job.update(over)
        return job

    def _start(self, jobs, errors):
        """Pornește o rulare și execută worker-ul sincron (fără thread)."""
        from jobs import search_bridge
        from jobs.models import SearchRun
        run = SearchRun.objects.create(label="test", params_json=self.PARAMS)
        with patch.object(search_bridge.JobSpyScraper, "scrape_query",
                          return_value=(jobs, errors)):
            search_bridge.run_search_now(run.pk)
        run.refresh_from_db()
        return run

    def test_successful_run_saves_results(self):
        run = self._start([self._job()], [])
        self.assertEqual(run.status, "done")
        self.assertEqual(run.found_count, 1)
        self.assertIsNotNone(run.finished_at)
        res = run.results.get()
        self.assertEqual(res.title, "Python Developer")
        self.assertEqual(res.email_hr, "hr@acme.de")
        self.assertEqual(res.platform, "indeed")
        self.assertEqual(res.raw_json["currency"], "EUR")

    def test_prescore_and_reasons_are_stored(self):
        run = self._start([self._job()], [])
        res = run.results.get()
        self.assertGreater(res.prescore, 0)
        self.assertTrue(res.prescore_reasons_json)

    def test_warnings_and_errors_are_split_by_prefix(self):
        from scrapers.jobspy_scraper import WARNING_PREFIX
        run = self._start(
            [self._job()],
            [f"{WARNING_PREFIX} [de/indeed] am renunțat la hours_old",
             "[de/linkedin] Python Developer: 429"],
        )
        self.assertEqual(len(run.warnings_json), 1)
        self.assertEqual(len(run.errors_json), 1)
        self.assertIn("429", run.errors_json[0])

    def test_duplicate_urls_are_collapsed(self):
        run = self._start([self._job(), self._job()], [])
        self.assertEqual(run.found_count, 1)

    def test_no_results_with_errors_is_an_error_run(self):
        run = self._start([], ["[de/indeed] Python Developer: 500"])
        self.assertEqual(run.status, "error")

    def test_no_results_without_errors_is_a_done_run(self):
        run = self._start([], [])
        self.assertEqual(run.status, "done")
        self.assertEqual(run.found_count, 0)

    def test_exception_in_scraper_marks_run_as_error(self):
        from jobs import search_bridge
        from jobs.models import SearchRun
        run = SearchRun.objects.create(label="test", params_json=self.PARAMS)
        with patch.object(search_bridge.JobSpyScraper, "scrape_query",
                          side_effect=RuntimeError("jobspy a explodat")):
            search_bridge.run_search_now(run.pk)
        run.refresh_from_db()
        self.assertEqual(run.status, "error")
        self.assertIn("jobspy a explodat", run.errors_json[0])

    def test_seen_state_comes_from_the_index(self):
        from jobs import search_bridge
        from store.seen import APLICAT

        class FakeIndex:
            def decision_for(self, job):
                return APLICAT

        with patch.object(search_bridge, "_load_seen", return_value=FakeIndex()):
            run = self._start([self._job()], [])
        self.assertEqual(run.results.get().seen_state, APLICAT)


class SearchConcurrencyTests(TestCase):
    def test_second_search_is_refused_while_one_runs(self):
        from jobs import search_bridge
        from jobs.models import SearchRun
        SearchRun.objects.create(label="în curs", params_json={}, status="running")
        res = search_bridge.start_search(SearchWorkerTests.PARAMS)
        self.assertFalse(res["ok"])
        self.assertIn("în curs", res["error"])

    def test_stale_run_does_not_block_a_new_search(self):
        from datetime import timedelta
        from django.utils import timezone
        from jobs import search_bridge
        from jobs.models import SearchRun

        old = SearchRun.objects.create(label="mort", params_json={}, status="running")
        SearchRun.objects.filter(pk=old.pk).update(
            started_at=timezone.now() - timedelta(minutes=25))

        self.assertEqual(search_bridge.expire_stale_runs(), 1)
        old.refresh_from_db()
        self.assertEqual(old.status, "error")
        self.assertIn("întrerupt", old.errors_json[0])

    def test_start_search_expires_stale_runs_first(self):
        from datetime import timedelta
        from django.utils import timezone
        from jobs import search_bridge
        from jobs.models import SearchRun

        old = SearchRun.objects.create(label="mort", params_json={}, status="running")
        SearchRun.objects.filter(pk=old.pk).update(
            started_at=timezone.now() - timedelta(minutes=25))

        with patch.object(search_bridge.threading, "Thread") as thread:
            res = search_bridge.start_search(SearchWorkerTests.PARAMS)
        self.assertTrue(res["ok"])
        thread.assert_called_once()
```

- [ ] **Step 6: Rulează ca să vezi că pică**

Run: `python dashboard/manage.py test jobs.tests.SearchWorkerTests`
Expected: FAIL — `ModuleNotFoundError: No module named 'jobs.search_bridge'`

- [ ] **Step 7: Scrie `dashboard/jobs/search_bridge.py`**

```python
"""Puntea dashboard → JobSpy: rulează căutări în fundal și le pune în staging.

Refolosește `scrapers/` read-only, exact ca `generation_bridge` pentru
`generators/`. Nicio funcție nu ridică excepții spre views — eșecul e codat în
`{ok, error}` sau în `SearchRun.status`.

`store/seen.py` e citit, NICIODATĂ scris: indexul de dedupe aparține
pipeline-ului CLI, iar două procese care scriu același JSON s-ar călca.
"""
import threading
from datetime import timedelta
from pathlib import Path

from django.db import connections
from django.utils import timezone

from filters.relevance import prescore
from scrapers.base import SearchQuery
from scrapers.jobspy_scraper import (SUPPORTED_SITES, WARNING_PREFIX,
                                     JobSpyScraper)
from store.seen import SeenIndex

from .models import SearchResult, SearchRun
from .sheet_bridge import load_pipeline_config

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SEEN_PATH = _REPO_ROOT / "data" / "seen_jobs.json"

# Thread-ul e `daemon`, deci moare la restart de runserver. Fără expirare, un
# `running` orfan ar bloca pe veci garda de concurență.
_STALE_AFTER = timedelta(minutes=20)


def _scrape_config() -> dict:
    return (load_pipeline_config().get("scraping", {}) or {})


def presets() -> list[dict]:
    """Piețele din settings.yaml, în forma cerută de formular."""
    out = []
    scraping = _scrape_config()
    for key, cfg in (scraping.get("markets", {}) or {}).items():
        locations = list(cfg.get("locations", []) or [])
        groups = [list(g) for g in (cfg.get("keyword_groups", []) or [])]
        out.append({
            "key": key,
            # Cheia sub numele câmpului ascuns din formular, ca `initial=preset`
            # să completeze și `preset`.
            "preset": key,
            "label": key,
            "country": str(cfg.get("country", "")),
            "language": str(cfg.get("language", "en")),
            "location": locations[0] if locations else str(cfg.get("country", "")),
            "remote": bool(cfg.get("remote", False)),
            # Un grup pe linie, cuvintele din grup separate prin virgulă.
            "keywords": "\n".join(", ".join(g) for g in groups),
            "sites": list(scraping.get("jobspy_sites", ["indeed"])),
            "hours_old": int(scraping.get("hours_old", 72)),
            "results_wanted": int(scraping.get("max_results_per_call", 50)),
        })
    return out


def parse_keyword_groups(text: str) -> list[list[str]]:
    """Textarea → grupuri: o linie = un grup, virgula separă cuvintele."""
    groups = []
    for line in (text or "").splitlines():
        words = [w.strip() for w in line.split(",") if w.strip()]
        if words:
            groups.append(words)
    return groups


def query_from_params(params: dict) -> SearchQuery:
    """Parametrii validați ai formularului → interogare JobSpy."""
    hours_old = params.get("hours_old")
    distance = params.get("distance")
    return SearchQuery(
        keyword_groups=parse_keyword_groups(params.get("keywords", "")),
        sites=list(params.get("sites") or ["indeed"]),
        location=str(params.get("location", "") or ""),
        country=str(params.get("country", "") or ""),
        language=str(params.get("language", "en") or "en"),
        market_key="adhoc",
        remote=bool(params.get("remote")),
        hours_old=int(hours_old) if hours_old not in (None, "") else None,
        results_wanted=int(params.get("results_wanted") or 50),
        job_type=str(params.get("job_type", "") or ""),
        distance=int(distance) if distance not in (None, "") else None,
        google_search_term=str(params.get("google_search_term", "") or ""),
        linkedin_fetch_description=bool(params.get("linkedin_fetch_description")),
    )


def label_for(params: dict) -> str:
    """Etichetă scurtă pentru listă: primul grup + câte mai sunt + locația."""
    groups = parse_keyword_groups(params.get("keywords", ""))
    first = ", ".join(groups[0]) if groups else "(fără cuvinte-cheie)"
    extra = f" +{len(groups) - 1} grupuri" if len(groups) > 1 else ""
    location = params.get("location") or "?"
    return f"{first}{extra} · {location}"


def expire_stale_runs() -> int:
    """Marchează drept întrerupte rulările `running` mai vechi decât pragul."""
    cutoff = timezone.now() - _STALE_AFTER
    return SearchRun.objects.filter(
        status=SearchRun.RUNNING, started_at__lt=cutoff
    ).update(
        status=SearchRun.ERROR,
        finished_at=timezone.now(),
        errors_json=["Rulare întreruptă (probabil un restart de server). "
                     "Pornește o căutare nouă."],
    )


def active_run() -> SearchRun | None:
    """Rularea în curs, dacă există (după expirarea celor moarte)."""
    expire_stale_runs()
    return SearchRun.objects.filter(status=SearchRun.RUNNING).first()


def start_search(params: dict) -> dict:
    """Creează rularea și pornește worker-ul. Nu așteaptă rezultatul."""
    running = active_run()
    if running:
        return {
            "ok": False, "run": running,
            "error": f"Căutarea #{running.pk} e încă în curs — "
                     "două rulări simultane înseamnă rate-limit garantat.",
        }

    run = SearchRun.objects.create(label=label_for(params), params_json=params)
    threading.Thread(target=run_search_now, args=(run.pk,), daemon=True).start()
    return {"ok": True, "run": run, "error": ""}


def _load_seen() -> SeenIndex:
    """Indexul de dedupe, citit read-only (punct de injecție în teste)."""
    return SeenIndex.load(_SEEN_PATH)


def run_search_now(run_pk: int) -> None:
    """Corpul worker-ului. Sincron — testele îl apelează direct.

    Nu ridică niciodată: orice eșec devine `status=error` pe rând, altfel
    thread-ul ar muri în tăcere și rândul ar rămâne `running` pe veci.
    """
    run = SearchRun.objects.get(pk=run_pk)
    try:
        config = load_pipeline_config()
        query = query_from_params(run.params_json)
        jobs, messages = JobSpyScraper(config, _REPO_ROOT).scrape_query(query)

        run.warnings_json = [m for m in messages if m.startswith(WARNING_PREFIX)]
        run.errors_json = [m for m in messages if not m.startswith(WARNING_PREFIX)]

        seen = _load_seen()
        rows, urls = [], set()
        for job in jobs:
            url = str(job.get("url") or "").strip()
            if not url or url in urls:
                continue
            urls.add(url)
            score, reasons = prescore(job, config)
            rows.append(SearchResult(
                run=run, url=url,
                title=job.get("title", ""), company=job.get("company", ""),
                location=job.get("location", ""), platform=job.get("platform", ""),
                date_posted=job.get("date_posted", ""),
                language=job.get("language", ""),
                description=job.get("description", ""),
                email_hr=job.get("email_hr", ""),
                salary_min=_as_int(job.get("salary_min")),
                salary_max=_as_int(job.get("salary_max")),
                prescore=score, prescore_reasons_json=reasons,
                seen_state=seen.decision_for(job),
                raw_json=job.get("raw") or {},
            ))
        SearchResult.objects.bulk_create(rows, ignore_conflicts=True)

        run.found_count = len(rows)
        # 0 rezultate + erori = eșec real; 0 rezultate curate = doar empty state.
        run.status = SearchRun.ERROR if (not rows and run.errors_json) else SearchRun.DONE
    except Exception as e:
        run.status = SearchRun.ERROR
        run.errors_json = (run.errors_json or []) + [f"{type(e).__name__}: {e}"]
    finally:
        run.finished_at = timezone.now()
        run.save()
        # Thread separat + SQLite: conexiunea trebuie închisă explicit.
        connections.close_all()


def _as_int(value) -> int | None:
    """Salariile vin din pandas ca float/NaN; modelul cere int sau None."""
    try:
        if value is None or (isinstance(value, float) and value != value):
            return None
        return int(float(value))
    except (TypeError, ValueError):
        return None
```

- [ ] **Step 8: Rulează testele**

Run: `python dashboard/manage.py test jobs`
Expected: PASS

- [ ] **Step 9: Commit**

```bash
git add store/seen.py tests/test_seen.py dashboard/jobs/search_bridge.py \
        dashboard/jobs/tests.py
git commit -m "feat(dashboard): search_bridge cu worker de fundal, prescore + seen read-only (JOB-46)"
```

---

### Task 7: Importul rezultatelor în `Job`

**Files:**
- Modify: `dashboard/jobs/search_bridge.py`
- Test: `dashboard/jobs/tests.py`

**Interfaces:**
- Consumes: `SearchResult` (Task 5), `search_bridge` (Task 6)
- Produces: `search_bridge.import_results(result_ids: list[int]) -> dict` cu cheile `created, updated, skipped, no_email, message`
- Produces: `search_bridge.LOCKED_STATES: tuple[str, ...]` = `("generated", "sending", "sent")`

- [ ] **Step 1: Scrie testele care pică**

```python
class SearchImportTests(TestCase):
    def _result(self, **over):
        from jobs.models import SearchResult, SearchRun
        run = SearchRun.objects.create(label="test", params_json={}, status="done")
        data = dict(run=run, url="https://acme.de/jobs/1", title="Python Developer",
                    company="ACME", location="Berlin", platform="indeed",
                    description="Django backend", email_hr="hr@acme.de",
                    language="de", prescore=60)
        data.update(over)
        return SearchResult.objects.create(**data)

    def test_import_creates_a_draft_job(self):
        from jobs import search_bridge
        res = self._result()
        report = search_bridge.import_results([res.pk])

        job = Job.objects.get(url=res.url)
        self.assertEqual(report["created"], 1)
        self.assertEqual(job.send_state, "draft")
        self.assertEqual(job.extraction_status, "ok")
        self.assertEqual(job.source, "jobspy:indeed")
        self.assertEqual(job.title, "Python Developer")
        self.assertEqual(job.email_hr, "hr@acme.de")

    def test_import_links_result_to_job(self):
        from jobs import search_bridge
        res = self._result()
        search_bridge.import_results([res.pk])
        res.refresh_from_db()
        self.assertEqual(res.imported_job.url, res.url)

    def test_import_never_calls_the_ai_extractor(self):
        """JobSpy livrează deja structura — un apel AI ar fi bani aruncați."""
        from jobs import search_bridge
        res = self._result()
        with patch.object(extractor, "extract_job") as ai:
            search_bridge.import_results([res.pk])
        ai.assert_not_called()

    def test_import_updates_an_existing_draft(self):
        from jobs import search_bridge
        Job.objects.create(url="https://acme.de/jobs/1", title="titlu vechi")
        res = self._result()
        report = search_bridge.import_results([res.pk])
        self.assertEqual(report["updated"], 1)
        self.assertEqual(Job.objects.get(url=res.url).title, "Python Developer")

    def test_import_refuses_to_overwrite_a_sent_job(self):
        from jobs import search_bridge
        Job.objects.create(url="https://acme.de/jobs/1", title="ARHIVĂ",
                           send_state="sent", email_body="ce a plecat")
        res = self._result()
        report = search_bridge.import_results([res.pk])

        job = Job.objects.get(url=res.url)
        self.assertEqual(report["skipped"], 1)
        self.assertEqual(report["created"], 0)
        self.assertEqual(job.title, "ARHIVĂ")
        self.assertEqual(job.email_body, "ce a plecat")

    def test_import_refuses_to_overwrite_generated_and_sending(self):
        from jobs import search_bridge
        for state in ("generated", "sending"):
            Job.objects.all().delete()
            Job.objects.create(url="https://acme.de/jobs/1", title="ÎN ZBOR",
                               send_state=state)
            res = self._result()
            search_bridge.import_results([res.pk])
            self.assertEqual(Job.objects.get(url=res.url).title, "ÎN ZBOR")

    def test_skipped_result_still_links_to_the_existing_job(self):
        from jobs import search_bridge
        job = Job.objects.create(url="https://acme.de/jobs/1", send_state="sent")
        res = self._result()
        search_bridge.import_results([res.pk])
        res.refresh_from_db()
        self.assertEqual(res.imported_job_id, job.pk)

    def test_report_counts_results_without_hr_email(self):
        from jobs import search_bridge
        res = self._result(email_hr="")
        report = search_bridge.import_results([res.pk])
        self.assertEqual(report["no_email"], 1)
        self.assertIn("fără email HR", report["message"])

    def test_import_updates_the_run_counter(self):
        from jobs import search_bridge
        res = self._result()
        search_bridge.import_results([res.pk])
        res.run.refresh_from_db()
        self.assertEqual(res.run.imported_count, 1)

    def test_empty_selection_is_reported_not_crashed(self):
        from jobs import search_bridge
        report = search_bridge.import_results([])
        self.assertEqual(report["created"], 0)
        self.assertIn("Nimic selectat", report["message"])
```

- [ ] **Step 2: Rulează ca să vezi că pică**

Run: `python dashboard/manage.py test jobs.tests.SearchImportTests`
Expected: FAIL — `AttributeError: module 'jobs.search_bridge' has no attribute 'import_results'`

- [ ] **Step 3: Adaugă `import_results` în `search_bridge.py`**

Actualizează importul modelelor și adaugă la finalul fișierului:

```python
# Stări în care pachetul de email e în zbor sau deja trimis: un import peste
# ele ar distruge editările sau arhiva a ceea ce a plecat efectiv.
LOCKED_STATES = ("generated", "sending", "sent")


def import_results(result_ids: list[int]) -> dict:
    """Transformă rezultatele selectate în rânduri `Job` (stare draft).

    Fără apel AI: JobSpy livrează deja titlul, compania, descrierea și adesea
    emailul, deci `extract_job()` rămâne doar pentru URL-urile lipite manual.
    """
    report = {"created": 0, "updated": 0, "skipped": 0, "no_email": 0, "message": ""}
    if not result_ids:
        report["message"] = "Nimic selectat — bifează cel puțin un rezultat."
        return report

    results = list(SearchResult.objects.filter(pk__in=result_ids))
    touched_runs: set[int] = set()

    for res in results:
        touched_runs.add(res.run_id)
        existing = Job.objects.filter(url=res.url).first()

        if existing and existing.send_state in LOCKED_STATES:
            report["skipped"] += 1
            if res.imported_job_id != existing.pk:
                res.imported_job = existing
                res.save(update_fields=["imported_job"])
            continue

        job, created = Job.objects.update_or_create(
            url=res.url,
            defaults={
                "title": res.title, "company": res.company,
                "location": res.location, "description": res.description,
                "email_hr": res.email_hr, "language": res.language,
                "date_posted": res.date_posted,
                "salary_min": res.salary_min, "salary_max": res.salary_max,
                "source": f"jobspy:{res.platform or 'unknown'}",
                "extraction_status": "ok",
                "send_state": "draft",
                "raw_json": res.raw_json or {},
            },
        )
        report["created" if created else "updated"] += 1
        if not (res.email_hr or "").strip():
            report["no_email"] += 1
        res.imported_job = job
        res.save(update_fields=["imported_job"])

    for run_id in touched_runs:
        run = SearchRun.objects.get(pk=run_id)
        run.imported_count = run.results.filter(imported_job__isnull=False).count()
        run.save(update_fields=["imported_count"])

    parts = []
    if report["created"]:
        parts.append(f"{report['created']} importate")
    if report["updated"]:
        parts.append(f"{report['updated']} actualizate")
    if report["skipped"]:
        parts.append(f"{report['skipped']} sărite (pachet în zbor sau deja trimis)")
    if report["no_email"]:
        parts.append(f"{report['no_email']} fără email HR — completează-l "
                     "înainte de „Transmite”")
    report["message"] = " · ".join(parts) or "Nimic de importat."
    return report
```

Adaugă `Job` la importul din `.models`:

```python
from .models import Job, SearchResult, SearchRun
```

- [ ] **Step 4: Rulează testele**

Run: `python dashboard/manage.py test jobs`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add dashboard/jobs/search_bridge.py dashboard/jobs/tests.py
git commit -m "feat(dashboard): import selectiv în Job, fără AI și fără a călca pachetele trimise (JOB-46)"
```

---

### Task 8: Formular, view-uri și rute

**Files:**
- Modify: `dashboard/jobs/forms.py`
- Modify: `dashboard/jobs/views.py`
- Modify: `dashboard/jobs/urls.py`
- Test: `dashboard/jobs/tests.py`

**Interfaces:**
- Consumes: `search_bridge.presets/start_search/import_results/expire_stale_runs` (Tasks 6-7), `SUPPORTED_SITES` (Task 2)
- Produces:
  - `forms.SearchForm` — `cleaned_data` are exact cheile cerute de `query_from_params`
  - `views.search_index(request)` → `GET` listă+formular, `POST` pornește căutarea
  - `views.search_run(request, pk)` → detaliu rulare
  - `views.import_search_results(request, pk)` → `POST`, import în bloc
  - rute: `cauta/` (`search_index`), `cauta/<int:pk>/` (`search_run`), `cauta/<int:pk>/import` (`import_search_results`)

- [ ] **Step 1: Scrie testele care pică**

```python
class SearchFormTests(SimpleTestCase):
    BASE = {"keywords": "Python Developer", "location": "Berlin",
            "country": "GERMANY", "language": "de", "sites": ["indeed"],
            "hours_old": 72, "results_wanted": 20}

    def test_valid_minimal_form(self):
        from jobs.forms import SearchForm
        self.assertTrue(SearchForm(self.BASE).is_valid())

    def test_keywords_are_required(self):
        from jobs.forms import SearchForm
        form = SearchForm({**self.BASE, "keywords": "   "})
        self.assertFalse(form.is_valid())
        self.assertIn("keywords", form.errors)

    def test_at_least_one_site_is_required(self):
        from jobs.forms import SearchForm
        form = SearchForm({**self.BASE, "sites": []})
        self.assertFalse(form.is_valid())
        self.assertIn("sites", form.errors)

    def test_unknown_site_is_rejected(self):
        from jobs.forms import SearchForm
        form = SearchForm({**self.BASE, "sites": ["monster"]})
        self.assertFalse(form.is_valid())

    def test_results_wanted_is_capped(self):
        """Un results_wanted uriaș înseamnă minute de așteptare și 429."""
        from jobs.forms import SearchForm
        form = SearchForm({**self.BASE, "results_wanted": 5000})
        self.assertFalse(form.is_valid())
        self.assertIn("results_wanted", form.errors)

    def test_empty_hours_old_means_no_filter(self):
        from jobs.forms import SearchForm
        form = SearchForm({**self.BASE, "hours_old": ""})
        self.assertTrue(form.is_valid())
        self.assertIsNone(form.cleaned_data["hours_old"])


class SearchViewTests(TestCase):
    def test_get_search_page_lists_presets(self):
        resp = self.client.get(reverse("search_index"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "ro_remote")

    def test_post_starts_a_search_and_redirects_to_the_run(self):
        from jobs.models import SearchRun
        with patch("jobs.views.search_bridge.threading.Thread") as thread:
            resp = self.client.post(reverse("search_index"), {
                "keywords": "Python Developer", "location": "Berlin",
                "country": "GERMANY", "language": "de", "sites": ["indeed"],
                "hours_old": 72, "results_wanted": 20,
            })
        run = SearchRun.objects.get()
        self.assertRedirects(resp, reverse("search_run", args=[run.pk]))
        thread.assert_called_once()

    def test_invalid_post_redisplays_the_form_without_starting(self):
        from jobs.models import SearchRun
        resp = self.client.post(reverse("search_index"), {
            "keywords": "", "location": "Berlin", "sites": ["indeed"]})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(SearchRun.objects.count(), 0)

    def test_run_page_shows_results(self):
        from jobs.models import SearchResult, SearchRun
        run = SearchRun.objects.create(label="test", params_json={}, status="done",
                                       found_count=1)
        SearchResult.objects.create(run=run, url="https://acme.de/jobs/1",
                                    title="Python Developer", prescore=60)
        resp = self.client.get(reverse("search_run", args=[run.pk]))
        self.assertContains(resp, "Python Developer")

    def test_running_page_asks_the_browser_to_refresh(self):
        from jobs.models import SearchRun
        run = SearchRun.objects.create(label="test", params_json={}, status="running")
        resp = self.client.get(reverse("search_run", args=[run.pk]))
        self.assertContains(resp, "http-equiv=\"refresh\"")

    def test_finished_page_does_not_refresh(self):
        from jobs.models import SearchRun
        run = SearchRun.objects.create(label="test", params_json={}, status="done")
        resp = self.client.get(reverse("search_run", args=[run.pk]))
        self.assertNotContains(resp, "http-equiv=\"refresh\"")

    def test_import_post_creates_jobs_and_redirects_back(self):
        from jobs.models import SearchResult, SearchRun
        run = SearchRun.objects.create(label="test", params_json={}, status="done")
        res = SearchResult.objects.create(run=run, url="https://acme.de/jobs/1",
                                          title="Python Developer",
                                          platform="indeed")
        resp = self.client.post(reverse("import_search_results", args=[run.pk]),
                                {"result_ids": [res.pk]})
        self.assertRedirects(resp, reverse("search_run", args=[run.pk]))
        self.assertTrue(Job.objects.filter(url=res.url).exists())

    def test_import_get_is_not_allowed_to_change_anything(self):
        from jobs.models import SearchRun
        run = SearchRun.objects.create(label="test", params_json={}, status="done")
        resp = self.client.get(reverse("import_search_results", args=[run.pk]))
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(Job.objects.count(), 0)

    def test_import_ignores_ids_from_another_run(self):
        """Un id trimis manual nu trebuie să importe din altă rulare."""
        from jobs.models import SearchResult, SearchRun
        mine = SearchRun.objects.create(label="a mea", params_json={}, status="done")
        other = SearchRun.objects.create(label="alta", params_json={}, status="done")
        alien = SearchResult.objects.create(run=other, url="https://x.co/1")
        self.client.post(reverse("import_search_results", args=[mine.pk]),
                         {"result_ids": [alien.pk]})
        self.assertEqual(Job.objects.count(), 0)
```

- [ ] **Step 2: Rulează ca să vezi că pică**

Run: `python dashboard/manage.py test jobs.tests.SearchViewTests`
Expected: FAIL — `NoReverseMatch: Reverse for 'search_index' not found`

- [ ] **Step 3: Adaugă `SearchForm` în `dashboard/jobs/forms.py`**

```python
from scrapers.jobspy_scraper import SUPPORTED_SITES

_JOB_TYPES = [("", "orice"), ("fulltime", "full-time"), ("parttime", "part-time"),
              ("contract", "contract"), ("internship", "internship")]


class SearchForm(forms.Form):
    """Parametrii unei căutări JobSpy. Cheile din `cleaned_data` sunt exact
    cele așteptate de `search_bridge.query_from_params`."""

    preset = forms.CharField(required=False, widget=forms.HiddenInput)
    keywords = forms.CharField(
        widget=forms.Textarea(attrs={"rows": 4}),
        help_text="Un grup pe linie; cuvintele de pe aceeași linie se caută cu OR.",
    )
    location = forms.CharField(max_length=120)
    country = forms.CharField(max_length=60,
                              help_text="Enum de țară jobspy, ex. GERMANY.")
    language = forms.CharField(max_length=5, initial="de",
                               help_text="Comandă limba Anschreiben-ului.")
    sites = forms.MultipleChoiceField(
        choices=[(s, s) for s in SUPPORTED_SITES],
        widget=forms.CheckboxSelectMultiple,
    )
    remote = forms.BooleanField(required=False)
    # Gol = fără filtru de vechime.
    hours_old = forms.IntegerField(required=False, min_value=1, max_value=8760)
    # Plafon: peste ~200 rezultate/apel, jobspy petrece minute și ia 429.
    results_wanted = forms.IntegerField(min_value=1, max_value=200, initial=50)
    job_type = forms.ChoiceField(choices=_JOB_TYPES, required=False)
    distance = forms.IntegerField(required=False, min_value=1, max_value=200)
    google_search_term = forms.CharField(
        required=False, max_length=200,
        help_text="Sintaxa proprie Google Jobs, ex. „Python Developer jobs "
                  "near Berlin since yesterday”. Contează doar cu „google” bifat.",
    )
    linkedin_fetch_description = forms.BooleanField(
        required=False,
        help_text="Aduce descrierea completă de pe LinkedIn: un request per job, "
                  "deci mult mai lent.",
    )

    def clean_keywords(self):
        from .search_bridge import parse_keyword_groups
        value = self.cleaned_data["keywords"]
        if not parse_keyword_groups(value):
            raise forms.ValidationError("Scrie cel puțin un cuvânt-cheie.")
        return value
```

- [ ] **Step 4: Adaugă view-urile în `dashboard/jobs/views.py`**

La importuri:

```python
from . import search_bridge
from .forms import JobForm, SearchForm
from .models import Job, SearchResult, SearchRun
```

La finalul fișierului:

```python
def search_index(request):
    """Secțiunea „Căutare": formular (preset + override) și istoricul rulărilor."""
    if request.method == "POST":
        form = SearchForm(request.POST)
        if form.is_valid():
            res = search_bridge.start_search(form.cleaned_data)
            if res["ok"]:
                messages.info(request, "Căutare pornită — pagina se "
                                       "reîmprospătează până se termină.")
                return redirect("search_run", pk=res["run"].pk)
            messages.error(request, res["error"])
            if res["run"]:
                return redirect("search_run", pk=res["run"].pk)
        else:
            messages.error(request, "Formular invalid — verifică câmpurile marcate.")
    else:
        search_bridge.expire_stale_runs()
        presets = search_bridge.presets()
        # Presetul implicit vine randat din server, ca formularul să funcționeze
        # și fără JavaScript.
        form = SearchForm(initial=presets[0] if presets else None)

    return render(request, "jobs/cauta.html", {
        "form": form,
        "presets": search_bridge.presets(),
        "runs": SearchRun.objects.all()[:20],
        "section": "search",
    })


def search_run(request, pk):
    """Starea unei rulări + rezultatele, cu import în bloc."""
    run = get_object_or_404(SearchRun, pk=pk)
    if run.status == SearchRun.RUNNING:
        search_bridge.expire_stale_runs()
        run.refresh_from_db()

    order = "-prescore" if request.GET.get("sort") != "data" else "-date_posted"
    return render(request, "jobs/cauta_run.html", {
        "run": run,
        "results": run.results.order_by(order, "pk"),
        "sort": request.GET.get("sort", "scor"),
        "is_running": run.status == SearchRun.RUNNING,
        "section": "search",
    })


def import_search_results(request, pk):
    """„Importă selectate" — rezultatele bifate devin rânduri Job (draft)."""
    if request.method != "POST":
        return redirect("search_run", pk=pk)
    run = get_object_or_404(SearchRun, pk=pk)

    # Filtrăm pe rulare: un id trimis manual nu trebuie să importe din altă listă.
    ids = [int(i) for i in request.POST.getlist("result_ids") if str(i).isdigit()]
    ids = list(run.results.filter(pk__in=ids).values_list("pk", flat=True))

    report = search_bridge.import_results(ids)
    if report["created"] or report["updated"]:
        messages.success(request, report["message"])
    elif report["skipped"]:
        messages.warning(request, report["message"])
    else:
        messages.error(request, report["message"])
    return redirect("search_run", pk=pk)
```

- [ ] **Step 5: Adaugă rutele în `dashboard/jobs/urls.py`**

Înainte de ruta `trimise/`:

```python
    path("cauta/", views.search_index, name="search_index"),
    path("cauta/<int:pk>/", views.search_run, name="search_run"),
    path("cauta/<int:pk>/import", views.import_search_results,
         name="import_search_results"),
```

- [ ] **Step 6: Rulează testele**

Run: `python dashboard/manage.py test jobs`
Expected: PASS. Testele care ating template-uri (`test_get_search_page_lists_presets`, `test_run_page_shows_results`) vor eșua cu `TemplateDoesNotExist` până la Task 9 — este așteptat; rulează-le din nou la finalul Task 9.

- [ ] **Step 7: Commit**

```bash
git add dashboard/jobs/forms.py dashboard/jobs/views.py dashboard/jobs/urls.py \
        dashboard/jobs/tests.py
git commit -m "feat(dashboard): SearchForm + view-uri cauta/run/import (JOB-46)"
```

---

### Task 9: Template-uri, navigație și CSS

**Files:**
- Modify: `dashboard/jobs/templates/jobs/base.html`
- Create: `dashboard/jobs/templates/jobs/cauta.html`
- Create: `dashboard/jobs/templates/jobs/cauta_run.html`
- Test: `dashboard/jobs/tests.py` (rulează testele din Task 8)

**Interfaces:**
- Consumes: contextul din `search_index` / `search_run` (Task 8)

- [ ] **Step 1: Extinde `base.html` — nav + CSS aditiv**

În `<nav class="side">`, între link-ul „Joburi" și „Trimise":

```html
      <a class="nav-link {% if section == 'search' %}active{% endif %}"
         {% if section == 'search' %}aria-current="page"{% endif %}
         href="{% url 'search_index' %}">Căutare</a>
```

La finalul blocului `<style>`, înainte de `@media (max-width:720px)`:

```css
    /* ── Secțiunea Căutare (adaosuri, nicio regulă existentă schimbată) ── */
    .pill.running { background:rgba(245,158,11,.15); color:var(--warn); }
    .pill.done    { background:rgba(34,197,94,.15);  color:var(--ok); }
    .pill.nou     { background:rgba(76,139,245,.18); color:#7ea6f7; }
    .pill.vazut   { background:rgba(148,163,184,.15); color:var(--muted); }

    /* Filtre avansate — progressive disclosure */
    details.adv { margin:0 0 18px; background:var(--surface);
                  border:1px solid var(--border); border-radius:var(--radius); }
    details.adv > summary { cursor:pointer; padding:12px 16px; font-size:14px;
                            font-weight:600; color:var(--fg); }
    details.adv > summary:focus-visible { outline:2px solid var(--accent);
                                          outline-offset:2px; }
    details.adv .adv-body { padding:4px 16px 16px; }
    details.adv[open] > summary { border-bottom:1px solid var(--border); }
    .hint { color:var(--muted); font-size:12px; margin:5px 0 0; }

    /* Grup de checkbox-uri (site-uri) */
    fieldset.sites { border:1px solid var(--border-strong); border-radius:var(--radius);
                     padding:12px 14px; margin:0 0 14px; }
    fieldset.sites legend { font-size:12px; color:var(--muted);
                            text-transform:uppercase; letter-spacing:.04em;
                            padding:0 6px; }
    fieldset.sites ul { list-style:none; margin:0; padding:0; display:flex;
                        flex-wrap:wrap; gap:8px 18px; }
    /* 44px de zonă de atins, nu doar caseta de 18px */
    fieldset.sites label { display:flex; align-items:center; gap:8px;
                           min-height:44px; cursor:pointer; font-size:14px; }
    fieldset.sites input { width:18px; height:18px; accent-color:var(--accent); }

    /* Coloana de selecție din tabelul de rezultate */
    td.checkcol, th.checkcol { width:44px; text-align:center; }
    td.checkcol input, th.checkcol input { width:18px; height:18px;
                                           accent-color:var(--accent);
                                           cursor:pointer; }
    .score { font-weight:700; font-variant-numeric:tabular-nums; }

    /* Bara de acțiune în bloc */
    .bulkbar { display:flex; align-items:center; gap:14px; flex-wrap:wrap;
               margin:14px 0 0; padding:12px 16px; background:var(--surface);
               border:1px solid var(--border-strong); border-radius:var(--radius); }
    .bulkbar .count { color:var(--muted); font-size:14px; }
    .btn-primary:disabled { opacity:.45; cursor:not-allowed; }

    /* Chips cu parametrii rulării */
    .chips { display:flex; flex-wrap:wrap; gap:8px; margin:0 0 18px; }
    .chips span { font-size:12px; padding:4px 10px; border-radius:20px;
                  background:var(--surface-2); color:var(--muted);
                  border:1px solid var(--border); }
```

- [ ] **Step 2: Creează `cauta.html`**

```html
{% extends "jobs/base.html" %}
{% block title %}Căutare joburi{% endblock %}
{% block content %}
  <h1>Căutare joburi</h1>
  <p class="sub">Caută pe Indeed, LinkedIn, Glassdoor, Google Jobs și restul
    platformelor JobSpy. Rezultatele stau aici până le imporți — nimic nu ajunge
    în „Joburi” fără să bifezi.</p>

  {{ presets|json_script:"presets-data" }}

  <form method="post" action="{% url 'search_index' %}">
    {% csrf_token %}

    <div class="form-grid">
      <div class="field">
        <label for="preset-select">Preset</label>
        <select id="preset-select">
          {% for p in presets %}
            <option value="{{ p.key }}">{{ p.label }} · {{ p.location }}</option>
          {% endfor %}
        </select>
        <p class="hint">Completează câmpurile din <code>settings.yaml</code>.
          Poți schimba orice după aceea.</p>
      </div>

      <div class="field">
        <label for="{{ form.location.id_for_label }}">Locație</label>
        {{ form.location }}
        {% for e in form.location.errors %}<p class="hint">{{ e }}</p>{% endfor %}
      </div>

      <div class="field full">
        <label for="{{ form.keywords.id_for_label }}">Cuvinte cheie</label>
        {{ form.keywords }}
        <p class="hint">{{ form.keywords.help_text }}</p>
        {% for e in form.keywords.errors %}<p class="hint">{{ e }}</p>{% endfor %}
      </div>
    </div>

    <details class="adv">
      <summary>Filtre avansate</summary>
      <div class="adv-body">
        <fieldset class="sites">
          <legend>Site-uri</legend>
          {{ form.sites }}
          {% for e in form.sites.errors %}<p class="hint">{{ e }}</p>{% endfor %}
        </fieldset>

        <div class="form-grid">
          <div class="field">
            <label for="{{ form.country.id_for_label }}">Țară (jobspy)</label>
            {{ form.country }}
            <p class="hint">{{ form.country.help_text }}</p>
          </div>
          <div class="field">
            <label for="{{ form.language.id_for_label }}">Limbă</label>
            {{ form.language }}
            <p class="hint">{{ form.language.help_text }}</p>
          </div>
          <div class="field">
            <label for="{{ form.hours_old.id_for_label }}">Vechime max. (ore)</label>
            {{ form.hours_old }}
            <p class="hint">Gol = fără filtru de vechime.</p>
          </div>
          <div class="field">
            <label for="{{ form.results_wanted.id_for_label }}">Rezultate / apel</label>
            {{ form.results_wanted }}
            {% for e in form.results_wanted.errors %}<p class="hint">{{ e }}</p>{% endfor %}
          </div>
          <div class="field">
            <label for="{{ form.job_type.id_for_label }}">Tip contract</label>
            {{ form.job_type }}
          </div>
          <div class="field">
            <label for="{{ form.distance.id_for_label }}">Rază (mile)</label>
            {{ form.distance }}
          </div>
          <div class="field full">
            <label for="{{ form.google_search_term.id_for_label }}">Termen Google Jobs</label>
            {{ form.google_search_term }}
            <p class="hint">{{ form.google_search_term.help_text }}</p>
          </div>
        </div>

        <label class="live-toggle">{{ form.remote }}
          <span>Doar joburi remote
            <span class="hint">Pe Indeed, remote are prioritate față de filtrul
              de vechime — nu pot fi trimise împreună.</span></span>
        </label>
        <label class="live-toggle">{{ form.linkedin_fetch_description }}
          <span>Descrieri complete de pe LinkedIn
            <span class="hint">{{ form.linkedin_fetch_description.help_text }}</span></span>
        </label>
      </div>
    </details>

    {{ form.preset }}
    <div class="actions">
      <button type="submit" class="btn-primary"
              onclick="this.disabled=true;this.textContent='Pornesc căutarea…';this.form.submit()">
        Caută
      </button>
    </div>
  </form>

  <h3>Rulări recente</h3>
  {% if runs %}
  <div class="table-scroll">
    <table>
      <thead>
        <tr><th>Căutare</th><th>Status</th><th>Găsite</th><th>Importate</th>
            <th>Durată</th><th>Pornită</th></tr>
      </thead>
      <tbody>
        {% for run in runs %}
        <tr>
          <td><a href="{% url 'search_run' run.pk %}">{{ run.label }}</a></td>
          <td><span class="pill {{ run.status }}">{{ run.status }}</span></td>
          <td class="num">{{ run.found_count }}</td>
          <td class="num">{{ run.imported_count }}</td>
          <td class="dim num">{% if run.duration_sec %}{{ run.duration_sec }}s{% else %}—{% endif %}</td>
          <td class="dim num">{{ run.started_at|date:"d.m H:i" }}</td>
        </tr>
        {% endfor %}
      </tbody>
    </table>
  </div>
  {% else %}
  <div class="empty">Nicio căutare încă. Alege un preset și apasă „Caută”.</div>
  {% endif %}

  <script>
    // Pre-completare din preset. Formularul funcționează și fără JS: valorile
    // presetului implicit vin randate din server.
    (function () {
      var data = JSON.parse(document.getElementById("presets-data").textContent);
      var select = document.getElementById("preset-select");
      if (!select) { return; }

      function set(name, value) {
        var el = document.querySelector('[name="' + name + '"]');
        if (el) { el.value = value; }
      }

      function apply() {
        var p = data.filter(function (x) { return x.key === select.value; })[0];
        if (!p) { return; }
        set("preset", p.key);
        set("keywords", p.keywords);
        set("location", p.location);
        set("country", p.country);
        set("language", p.language);
        set("hours_old", p.hours_old);
        set("results_wanted", p.results_wanted);
        var remote = document.querySelector('[name="remote"]');
        if (remote) { remote.checked = !!p.remote; }
        document.querySelectorAll('[name="sites"]').forEach(function (box) {
          box.checked = p.sites.indexOf(box.value) !== -1;
        });
      }

      select.addEventListener("change", apply);
    })();
  </script>
{% endblock %}
```

- [ ] **Step 3: Creează `cauta_run.html`**

```html
{% extends "jobs/base.html" %}
{% block title %}{{ run.label }}{% endblock %}
{% block content %}
  {% if is_running %}
    {# Randat DOAR cât rulează: o pagină terminată nu se mai reîncarcă. #}
    <meta http-equiv="refresh" content="5">
  {% endif %}

  <h1>{{ run.label }}</h1>
  <p class="sub">
    <span class="pill {{ run.status }}">{{ run.status }}</span>
    · {{ run.found_count }} rezultate · {{ run.imported_count }} importate
    {% if run.duration_sec %}· {{ run.duration_sec }}s{% endif %}
    · <a href="{% url 'search_index' %}">căutare nouă</a>
  </p>

  <div class="chips">
    {% for site in run.params_json.sites %}<span>{{ site }}</span>{% endfor %}
    <span>{{ run.params_json.location }}</span>
    {% if run.params_json.remote %}<span>remote</span>{% endif %}
    {% if run.params_json.hours_old %}<span>≤ {{ run.params_json.hours_old }}h</span>{% endif %}
    {% if run.params_json.job_type %}<span>{{ run.params_json.job_type }}</span>{% endif %}
  </div>

  {% if is_running %}
    <div class="banner dry" role="status" aria-live="polite">
      Caut… pagina se reîmprospătează automat la 5 secunde.
      <a href="{{ request.get_full_path }}">Reîmprospătează acum</a>
    </div>
  {% endif %}

  {% if run.warnings_json %}
    <div class="banner dry">
      {% for w in run.warnings_json %}<div>{{ w }}</div>{% endfor %}
    </div>
  {% endif %}

  {% if run.errors_json %}
    <details class="adv">
      <summary>Erori pe surse ({{ run.errors_json|length }})</summary>
      <div class="adv-body">
        {% for e in run.errors_json %}<p class="hint">{{ e }}</p>{% endfor %}
      </div>
    </details>
  {% endif %}

  {% if results %}
  <form method="post" action="{% url 'import_search_results' run.pk %}">
    {% csrf_token %}
    <div class="table-scroll">
      <table>
        <thead>
          <tr>
            <th class="checkcol">
              <input type="checkbox" id="check-all" aria-label="Selectează tot">
            </th>
            <th {% if sort != "data" %}aria-sort="descending"{% endif %}>
              {% if sort == "data" %}<a href="?">Scor</a>{% else %}Scor{% endif %}
            </th>
            <th>Titlu</th><th>Companie</th><th>Locație</th><th>Platformă</th>
            <th>Email HR</th>
            <th {% if sort == "data" %}aria-sort="descending"{% endif %}>
              {% if sort == "data" %}Publicat{% else %}<a href="?sort=data">Publicat</a>{% endif %}
            </th>
            <th>Stare</th>
          </tr>
        </thead>
        <tbody>
          {% for r in results %}
          <tr>
            <td class="checkcol">
              <input type="checkbox" name="result_ids" value="{{ r.pk }}"
                     {% if r.imported_job_id %}disabled{% endif %}
                     aria-label="Selectează {{ r.title|default:'rezultatul' }}">
            </td>
            <td class="score num" title="{{ r.prescore_reasons_json|join:', ' }}">{{ r.prescore }}</td>
            <td><a href="{{ r.url }}" target="_blank" rel="noopener">{{ r.title|default:"(fără titlu)" }}</a></td>
            <td>{{ r.company|default:"—" }}</td>
            <td class="dim">{{ r.location|default:"—" }}</td>
            <td class="dim">{{ r.platform|default:"—" }}</td>
            <td class="mono">{{ r.email_hr|default:"—" }}</td>
            <td class="dim num">{{ r.date_posted|default:"—" }}</td>
            <td>
              {% if r.imported_job_id %}
                <a class="pill done" href="{% url 'job_detail' r.imported_job_id %}">importat</a>
              {% elif r.seen_state %}
                <span class="pill vazut">văzut: {{ r.seen_state }}</span>
              {% else %}
                <span class="pill nou">nou</span>
              {% endif %}
            </td>
          </tr>
          {% endfor %}
        </tbody>
      </table>
    </div>

    <div class="bulkbar">
      <button type="submit" class="btn-primary" id="import-btn">Importă selectate</button>
      <span class="count" id="import-count" role="status" aria-live="polite">
        niciun rezultat selectat
      </span>
      <span class="hint">Importul creează rânduri „draft” în „Joburi”, fără apel AI.
        Joburile deja trimise nu se suprascriu.</span>
    </div>
  </form>

  <script>
    // Doar confort: numără selecția și dezactivează butonul la 0. Fără JS,
    // butonul rămâne activ și serverul validează selecția goală.
    (function () {
      var boxes = Array.prototype.slice.call(
        document.querySelectorAll('input[name="result_ids"]:not([disabled])'));
      var all = document.getElementById("check-all");
      var btn = document.getElementById("import-btn");
      var out = document.getElementById("import-count");

      function sync() {
        var n = boxes.filter(function (b) { return b.checked; }).length;
        btn.disabled = n === 0;
        out.textContent = n === 0 ? "niciun rezultat selectat"
                                  : n + " selectate";
      }

      boxes.forEach(function (b) { b.addEventListener("change", sync); });
      if (all) {
        all.addEventListener("change", function () {
          boxes.forEach(function (b) { b.checked = all.checked; });
          sync();
        });
      }
      sync();
    })();
  </script>
  {% elif is_running %}
    <div class="empty">Încă nu am rezultate — caut.</div>
  {% else %}
    <div class="empty">Nicio potrivire. Încearcă mai puține cuvinte-cheie,
      crește vechimea maximă sau bifează mai multe site-uri.</div>
  {% endif %}
{% endblock %}
```

- [ ] **Step 4: Rulează toate testele dashboard**

Run: `python dashboard/manage.py test jobs`
Expected: PASS — inclusiv testele de template din Task 8.

- [ ] **Step 5: Verifică manual în browser**

```bash
python dashboard/manage.py runserver
```

Verifică pe `http://127.0.0.1:8000/cauta/`:
- schimbarea presetului completează keywords/locație/țară/site-uri
- „Filtre avansate" se deschide și se închide, focus vizibil pe `summary`
- la 375px lățime nu apare scroll orizontal pe `body` (tabelul scrollează în `.table-scroll`)
- „Importă selectate" e dezactivat până bifezi ceva, iar contorul se actualizează
- linkul „Căutare" din sidebar e marcat activ

- [ ] **Step 6: Rulează ambele suite**

Run: `python -m pytest -q && python dashboard/manage.py test jobs`
Expected: PASS pe amândouă.

- [ ] **Step 7: Commit**

```bash
git add dashboard/jobs/templates/jobs/
git commit -m "feat(dashboard): UI secțiunea Căutare — formular, rulare live, import în bloc (JOB-46)"
```

---

### Task 10: Documentează în CLAUDE.md

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Adaugă secțiunea**

După paragraful „**Poarta dintre scrape și analyze.**", adaugă:

```markdown
**Căutare din dashboard.** `SearchQuery` (`scrapers/base.py`) e unitatea de
lucru a scraperului, iar `JobSpyScraper.scrape_query()` e SINGURUL loc care
apelează `scrape_jobs()` — `scrape_market()` e un wrapper peste
`Market.to_query()`. Dashboard-ul (`dashboard/jobs/search_bridge.py`) rulează
căutări ad-hoc într-un thread `daemon`, cu staging în `SearchRun`/`SearchResult`
și import selectiv în `Job` (fără apel AI — JobSpy livrează deja structura).
Indexul de dedupe e citit read-only din dashboard: `data/seen_jobs.json`
aparține pipeline-ului CLI.

**Filtre incompatibile per site.** Upstream: pe Indeed poți folosi DOAR unul din
`hours_old` / `(job_type & is_remote)` / `easy_apply`. `_sanitize()` renunță la
`hours_old` când căutarea e remote (sau are `job_type`) și raportează asta cu
prefix `[avertisment]`. `ScraperAgent` exclude avertismentele din
`blocking_errors`, deci o degradare nu coboară statusul la PARTIAL.
```

- [ ] **Step 2: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: căutarea din dashboard + filtrele incompatibile per site (JOB-46)"
```

---

## Verificare finală

- [ ] `python -m pytest -q` — verde
- [ ] `python dashboard/manage.py test jobs` — verde
- [ ] `python dashboard/manage.py makemigrations --check --dry-run` — fără migrări nedetectate
- [ ] `python3 main.py --step scrape` rulează și raportează SUCCESS (nu PARTIAL) pe piața `de`
- [ ] `git log --oneline` arată 10 commit-uri cu tag `JOB-46`
