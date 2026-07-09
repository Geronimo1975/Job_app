"""
JobSpy Scraper — Agregare joburi de pe LinkedIn, Indeed, Glassdoor, etc.

Folosește python-jobspy (pip install python-jobspy) pentru a căuta
joburi pe mai multe platforme simultan.
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    from jobspy import scrape_jobs
    HAS_JOBSPY = True
except ImportError:
    HAS_JOBSPY = False


class JobSpyScraper:
    """Scraper bazat pe python-jobspy pentru agregare multi-platformă."""

    def __init__(self, config: dict, project_root: Path):
        self.config = config
        self.project_root = project_root
        self.scrape_config = config.get("scraping", {})

    def scrape(
        self,
        keywords: list[str] | None = None,
        locations: list[str] | None = None,
        max_results: int | None = None,
    ) -> list[dict]:
        """
        Caută joburi pe platformele suportate.

        Returns:
            Lista de dicționare cu joburi găsite.
        """
        if not HAS_JOBSPY:
            raise ImportError(
                "python-jobspy nu este instalat. Rulează: pip install python-jobspy"
            )

        keywords = keywords or self.scrape_config.get("search_keywords", [])
        locations = locations or self.scrape_config.get("locations", ["Germany"])
        max_results = max_results or self.scrape_config.get("max_results_per_source", 50)

        all_jobs = []

        for keyword in keywords:
            for location in locations:
                try:
                    jobs_df = scrape_jobs(
                        site_name=["indeed", "glassdoor", "google"],
                        search_term=keyword,
                        location=location,
                        results_wanted=max_results,
                        hours_old=168,  # ultima săptămână
                        country_indeed="Germany",
                    )

                    if jobs_df is not None and not jobs_df.empty:
                        for _, row in jobs_df.iterrows():
                            job = self._normalize_job(row, keyword, location)
                            if job and not self._is_duplicate(job, all_jobs):
                                all_jobs.append(job)

                except Exception as e:
                    print(f"  ⚠ JobSpy error [{keyword} / {location}]: {e}")
                    continue

        return all_jobs

    def _normalize_job(self, row: Any, keyword: str, location: str) -> dict | None:
        """Normalizează un rând din DataFrame în format standard."""
        try:
            return {
                "id": f"jobspy_{hash(str(row.get('job_url', '')))}",
                "source": "jobspy",
                "platform": str(row.get("site", "unknown")),
                "title": str(row.get("title", "")),
                "company": str(row.get("company", "")),
                "location": str(row.get("location", location)),
                "url": str(row.get("job_url", "")),
                "description": str(row.get("description", ""))[:5000],
                "salary_min": row.get("min_amount"),
                "salary_max": row.get("max_amount"),
                "date_posted": str(row.get("date_posted", "")),
                "scraped_at": datetime.now().isoformat(),
                "gefunden_am": datetime.now().strftime("%Y-%m-%d"),
                "search_keyword": keyword,
                "search_location": location,
                "applied": False,
                "status": "new",
            }
        except Exception:
            return None

    def _is_duplicate(self, job: dict, existing: list[dict]) -> bool:
        """Verifică duplicatele pe baza URL sau titlu+companie."""
        for j in existing:
            if job["url"] and job["url"] == j["url"]:
                return True
            if job["title"] == j["title"] and job["company"] == j["company"]:
                return True
        return False

    def save_results(self, jobs: list[dict], output_path: Path | None = None) -> Path:
        """Salvează rezultatele în JSON."""
        if output_path is None:
            output_path = self.project_root / "data" / "jobs_scraped.json"

        output_path.parent.mkdir(parents=True, exist_ok=True)

        # Încarcă joburi existente și merge
        existing = []
        if output_path.exists():
            with open(output_path, "r", encoding="utf-8") as f:
                existing = json.load(f)

        # Adaugă doar joburi noi
        new_count = 0
        for job in jobs:
            if not self._is_duplicate(job, existing):
                existing.append(job)
                new_count += 1

        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(existing, f, indent=2, ensure_ascii=False, default=str)

        return output_path
