"""
JobSpy Scraper — agregare multi-platformă, organizată pe piețe.

Varianta veche făcea produs cartezian keywords × locații (49 de apeluri
pentru 7 keywords și 7 orașe), din care majoritatea se loveau reciproc de
rate-limit. Acum: un apel per (grup de keywords, site), rulate în paralel.

`scrape_query()` e SINGURUL loc care apelează `jobspy.scrape_jobs()`.
`scrape_market()` e doar un wrapper peste el, pentru calea CLI/pipeline.
"""

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


class JobSpyScraper:
    """Scraper bazat pe python-jobspy, cu o piață ca unitate de lucru."""

    def __init__(self, config: dict, project_root: Path):
        self.config = config
        self.project_root = project_root
        self.scrape_config = config.get("scraping", {}) or {}

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

    def _normalize_job(self, row: Any, q: SearchQuery) -> dict | None:
        """Transformă un rând jobspy în forma canonică."""
        try:
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
        except Exception:
            return None
