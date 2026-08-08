"""
Fundația comună a scraperelor: conceptul de piață și forma canonică a jobului.

Sursele primesc un `Market` și nu știu nimic despre țări. O piață nouă se
adaugă editând YAML-ul, fără cod nou.
"""

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

# Descrierile lungi umflă payload-ul spre AI fără să adauge informație.
_MAX_DESCRIPTION_CHARS = 5000


@dataclass(frozen=True)
class Market:
    """O piață de căutat: țară, limbă, keywords și surse proprii."""

    key: str
    country: str
    language: str
    locations: list[str] = field(default_factory=list)
    remote: bool = False
    keyword_groups: list[list[str]] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    # Surse al căror eșec NU trebuie să marcheze runul ca FAILED
    # (ex. scraping HTML pe site-uri fără API, care se strică la redesign).
    best_effort_sources: tuple[str, ...] = ()
    # Site-urile jobspy ale acestei piețe. Gol = se folosește lista globală
    # `scraping.jobspy_sites`. Există pentru că nu orice site acoperă orice
    # piață: Indeed pe domeniul `www` (`country: worldwide`) întoarce 0
    # rezultate, deci piața remote globală ar face apeluri inutile.
    jobspy_sites: list[str] = field(default_factory=list)

    @staticmethod
    def from_config(config: dict) -> list["Market"]:
        """Citește `scraping.markets` din settings.yaml."""
        markets_cfg = (config.get("scraping", {}) or {}).get("markets", {}) or {}
        markets = []
        for key, cfg in markets_cfg.items():
            markets.append(
                Market(
                    key=key,
                    country=str(cfg.get("country", "")),
                    language=str(cfg.get("language", "en")),
                    locations=list(cfg.get("locations", []) or []),
                    remote=bool(cfg.get("remote", False)),
                    keyword_groups=[list(g) for g in (cfg.get("keyword_groups", []) or [])],
                    sources=list(cfg.get("sources", []) or []),
                    best_effort_sources=tuple(cfg.get("best_effort_sources", []) or []),
                    jobspy_sites=list(cfg.get("jobspy_sites", []) or []),
                )
            )
        return markets

    def to_query(self, scrape_config: dict) -> "SearchQuery":
        """Interogarea corespunzătoare acestei piețe, cu limitele din YAML."""
        return SearchQuery(
            keyword_groups=self.keyword_groups,
            sites=list(self.jobspy_sites or scrape_config.get("jobspy_sites", ["indeed"])),
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
    now = datetime.now()
    return {
        "id": f"{source}_{abs(hash(str(url).strip()))}",
        "source": source,
        "platform": platform,
        "market": market_key,
        "language": language,
        "title": str(title or "").strip(),
        "company": str(company or "").strip(),
        "location": str(location or "").strip(),
        "url": str(url or "").strip(),
        "description": str(description or "")[:_MAX_DESCRIPTION_CHARS],
        "email_hr": str(email_hr or "").strip(),
        "date_posted": str(date_posted or ""),
        "salary_min": salary_min,
        "salary_max": salary_max,
        "scraped_at": now.isoformat(),
        "gefunden_am": now.strftime("%Y-%m-%d"),
        "raw": raw or {},
        "applied": False,
        # `new` nu face parte din mașina de stări a col M; `n8n_mapping`
        # îl filtrează la push, ca să nu suprascrie rânduri deja `done`.
        "status": "new",
    }


def save_jobs(jobs: list[dict], path: Path) -> Path:
    """Scrie rezultatele rulării curente. NU face merge cu ce era înainte —
    memoria între rulări e treaba lui `store/seen.py`."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(jobs, f, indent=2, ensure_ascii=False, default=str)
    return path
