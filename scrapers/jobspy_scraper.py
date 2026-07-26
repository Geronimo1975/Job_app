"""
JobSpy Scraper — agregare multi-platformă, organizată pe piețe.

Varianta veche făcea produs cartezian keywords × locații (49 de apeluri
pentru 7 keywords și 7 orașe), din care majoritatea se loveau reciproc de
rate-limit. Acum: un apel per (grup de keywords, site), rulate în paralel.
"""

from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from scrapers.base import Market, make_job

try:
    from jobspy import scrape_jobs
    HAS_JOBSPY = True
except ImportError:
    scrape_jobs = None
    HAS_JOBSPY = False

# Câte apeluri jobspy rulează simultan. Peste 4 crește rata de 429.
_MAX_WORKERS = 4


class JobSpyScraper:
    """Scraper bazat pe python-jobspy, cu o piață ca unitate de lucru."""

    def __init__(self, config: dict, project_root: Path):
        self.config = config
        self.project_root = project_root
        self.scrape_config = config.get("scraping", {}) or {}

    def scrape_market(self, market: Market) -> tuple[list[dict], list[str]]:
        """Caută joburi pentru o piață.

        Întoarce `(joburi, erori)`. Erorile se întorc, nu se ridică: un site
        blocat nu trebuie să anuleze rezultatele celorlalte.
        """
        if not HAS_JOBSPY:
            raise ImportError(
                "python-jobspy nu este instalat. Rulează: pip install python-jobspy"
            )

        sites = self.scrape_config.get("jobspy_sites", ["indeed"])
        location = market.locations[0] if market.locations else market.country

        units = [(group, site) for group in market.keyword_groups for site in sites]

        jobs: list[dict] = []
        errors: list[str] = []

        with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as pool:
            futures = {
                pool.submit(self._call, market, group, site, location): (group, site)
                for group, site in units
            }
            for future in as_completed(futures):
                group, site = futures[future]
                try:
                    jobs.extend(future.result())
                except Exception as e:
                    errors.append(f"[{market.key}/{site}] {group[0]}: {e}")

        return jobs, errors

    def _call(self, market: Market, group: list[str], site: str, location: str) -> list[dict]:
        """Un singur apel jobspy. Excepțiile urcă — le prinde `scrape_market`."""
        # Grupul de keywords devine o interogare booleană: un apel în loc de
        # câte unul per keyword.
        search_term = " OR ".join(f'"{k}"' for k in group)

        df = scrape_jobs(
            site_name=[site],
            search_term=search_term,
            location=location,
            results_wanted=int(self.scrape_config.get("max_results_per_call", 50)),
            hours_old=int(self.scrape_config.get("hours_old", 72)),
            country_indeed=market.country,
            is_remote=market.remote,
        )

        if df is None or df.empty:
            return []

        jobs = []
        for _, row in df.iterrows():
            job = self._normalize_job(row, market)
            if job:
                jobs.append(job)
        return jobs

    def _normalize_job(self, row: Any, market: Market) -> dict | None:
        """Transformă un rând jobspy în forma canonică."""
        try:
            url = str(row.get("job_url", "") or "").strip()
            # Fără URL nu există cheie de match la upsert („Apply URL"):
            # n8n ar cădea pe append și ar adăuga rânduri goale.
            if not url:
                return None

            platform = str(row.get("site", "unknown"))
            return make_job(
                source=f"{platform}-{market.key}",
                platform=platform,
                title=str(row.get("title", "")),
                company=str(row.get("company", "")),
                location=str(row.get("location", "") or market.country),
                url=url,
                description=str(row.get("description", "") or ""),
                market_key=market.key,
                language=market.language,
                date_posted=str(row.get("date_posted", "") or ""),
                salary_min=row.get("min_amount"),
                salary_max=row.get("max_amount"),
            )
        except Exception:
            return None
