"""
Scraper Agent — Coordonează scraping-ul de joburi din multiple surse.

Responsabilități:
  1. Rulează JobSpy scraper pentru platforme (Indeed, Glassdoor, Google)
  2. Rulează Career Page scraper pentru studiouri de jocuri
  3. Consolidează rezultatele și elimină duplicatele
  4. Salvează în data/jobs_scraped.json
"""

from datetime import datetime
from pathlib import Path

from agents.base_agent import BaseAgent, AgentResult, AgentStatus
from scrapers.jobspy_scraper import JobSpyScraper
from scrapers.career_page_scraper import CareerPageScraper


class ScraperAgent(BaseAgent):
    """Agent pentru scraping joburi din multiple surse."""

    def __init__(self):
        super().__init__("ScraperAgent")
        self.jobspy = JobSpyScraper(self.config, self.project_root)
        self.career = CareerPageScraper(self.config, self.project_root)

    def run(
        self,
        sources: list[str] | None = None,
        keywords: list[str] | None = None,
        locations: list[str] | None = None,
        studio_names: list[str] | None = None,
        **kwargs,
    ) -> AgentResult:
        """
        Rulează scraping pe sursele specificate.

        Args:
            sources: Lista de surse ["jobspy", "career_pages"]
            keywords: Override keywords de căutare
            locations: Override locații
            studio_names: Studiouri specifice de scraped
        """
        start = self._start()
        result = AgentResult(agent_name=self.name, status=AgentStatus.RUNNING)

        # Determină sursele active
        scrape_cfg = self.config.get("scraping", {}).get("sources", {})
        if sources is None:
            sources = []
            if scrape_cfg.get("jobspy", True):
                sources.append("jobspy")
            if scrape_cfg.get("career_pages", True):
                sources.append("career_pages")

        all_jobs = []

        # ── JobSpy ──────────────────────────────────────────
        if "jobspy" in sources:
            self.logger.info("Scraping JobSpy (Indeed, Glassdoor, Google)...")
            try:
                jobs = self.jobspy.scrape(
                    keywords=keywords,
                    locations=locations,
                )
                self.logger.info(f"  JobSpy: {len(jobs)} joburi găsite")
                all_jobs.extend(jobs)
                path = self.jobspy.save_results(jobs)
                self.logger.info(f"  Salvat: {path}")
            except ImportError as e:
                self.logger.warning(f"  JobSpy skip: {e}")
                result.errors.append(f"JobSpy: {e}")
            except Exception as e:
                self.logger.error(f"  JobSpy error: {e}")
                result.errors.append(f"JobSpy: {e}")

        # ── Career Pages ────────────────────────────────────
        if "career_pages" in sources:
            self.logger.info("Scraping career pages ale studiourilor...")
            try:
                jobs = self.career.scrape(studio_names=studio_names)
                self.logger.info(f"  Career Pages: {len(jobs)} joburi găsite")
                all_jobs.extend(jobs)
                path = self.career.save_results(jobs)
                self.logger.info(f"  Salvat: {path}")
            except ImportError as e:
                self.logger.warning(f"  Career Pages skip: {e}")
                result.errors.append(f"Career Pages: {e}")
            except Exception as e:
                self.logger.error(f"  Career Pages error: {e}")
                result.errors.append(f"Career Pages: {e}")

        # ── Rezultate ───────────────────────────────────────
        result.items_processed = len(all_jobs)
        result.items_succeeded = len(all_jobs)
        result.data = {
            "jobs": all_jobs,
            "sources_used": sources,
            "total_found": len(all_jobs),
        }

        if result.errors and not all_jobs:
            result.status = AgentStatus.FAILED
            result.message = f"Scraping eșuat: {'; '.join(result.errors)}"
        elif result.errors:
            result.status = AgentStatus.PARTIAL
            result.message = f"{len(all_jobs)} joburi găsite (cu erori)"
        else:
            result.status = AgentStatus.SUCCESS
            result.message = f"{len(all_jobs)} joburi găsite din {len(sources)} surse"

        return self._finish(result)
