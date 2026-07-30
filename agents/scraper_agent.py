"""
Scraper Agent — orchestrează scraping-ul pe piețe și surse.

Responsabilități:
  1. Citește piețele din config și rulează sursele declarate de fiecare
  2. Izolează erorile: o sursă căzută nu doboară runul
  3. Raportează un status onest (SUCCESS / PARTIAL / FAILED)
  4. Salvează rezultatele rulării în data/jobs_scraped.json
"""

import re

from agents.base_agent import AgentResult, AgentStatus, BaseAgent
from scrapers.base import Market, save_jobs
from scrapers.career_page_scraper import CareerPageScraper
from scrapers.jobspy_scraper import WARNING_PREFIX, JobSpyScraper
from scrapers.remote_boards import RemoteBoardsScraper
from scrapers.ro_boards import RoBoardsScraper


class ScraperAgent(BaseAgent):
    """Agent pentru scraping joburi pe mai multe piețe."""

    def __init__(self):
        super().__init__("ScraperAgent")
        self.jobspy = JobSpyScraper(self.config, self.project_root)
        self.remote_boards = RemoteBoardsScraper(self.config, self.project_root)
        self.ro_boards = RoBoardsScraper(self.config, self.project_root)
        self.career = CareerPageScraper(self.config, self.project_root)

    def run(
        self,
        markets: list[str] | None = None,
        sources: list[str] | None = None,
        **kwargs,
    ) -> AgentResult:
        """Rulează scraping pe piețele cerute.

        Args:
            markets: Chei de piață (implicit: toate din config)
            sources: Restrânge sursele (implicit: cele declarate de fiecare piață)
        """
        self._start()
        result = AgentResult(agent_name=self.name, status=AgentStatus.RUNNING)

        all_markets = Market.from_config(self.config)
        if markets is not None:
            all_markets = [m for m in all_markets if m.key in markets]

        all_jobs: list[dict] = []
        # Erori care contează pentru status (exclude sursele best-effort).
        blocking_errors: list[str] = []

        for market in all_markets:
            active = [s for s in market.sources if sources is None or s in sources]
            self.logger.info(f"Piața „{market.key}“ — surse: {', '.join(active) or 'niciuna'}")

            for source in active:
                jobs, errors = self._run_source(source, market)
                all_jobs.extend(jobs)
                result.errors.extend(errors)
                # Blocante = erorile surselor ne-best-effort, mai puțin cele
                # ale sub-site-urilor declarate best-effort (ex. linkedin) și
                # avertismentele (filtre renunțate — degradare, nu eșec).
                if source not in market.best_effort_sources:
                    blocking_errors.extend(
                        e for e in errors
                        if not e.startswith(WARNING_PREFIX)
                        and not self._is_best_effort_error(e, market)
                    )
                self.logger.info(f"  {source}: {len(jobs)} joburi, {len(errors)} erori")

        path = save_jobs(all_jobs, self.project_root / "data" / "jobs_scraped.json")
        self.logger.info(f"Salvat: {path}")

        result.items_processed = len(all_jobs)
        result.items_succeeded = len(all_jobs)
        result.data = {
            "jobs": all_jobs,
            "markets_used": [m.key for m in all_markets],
            "sources_used": sorted({s for m in all_markets for s in m.sources
                                    if sources is None or s in sources}),
            "total_found": len(all_jobs),
        }

        # Statusul spune adevărul: varianta veche raporta SUCCESS chiar și
        # când majoritatea apelurilor eșuaseră.
        if not all_jobs and blocking_errors:
            result.status = AgentStatus.FAILED
            result.message = f"Scraping eșuat: {len(blocking_errors)} erori"
        elif blocking_errors:
            result.status = AgentStatus.PARTIAL
            result.message = f"{len(all_jobs)} joburi ({len(blocking_errors)} surse cu probleme)"
        else:
            result.status = AgentStatus.SUCCESS
            result.message = f"{len(all_jobs)} joburi din {len(all_markets)} piețe"

        return self._finish(result)

    @staticmethod
    def _is_best_effort_error(error: str, market: Market) -> bool:
        """True dacă eroarea vine de la un sub-site declarat best-effort.

        Sursele compuse (jobspy) raportează erori per site, cu prefixul
        `[piață/site]`. `best_effort_sources: ["linkedin"]` din config se
        referă la site-ul jobspy, nu la sursa orchestrată — fără potrivirea
        pe etichetă, intrarea din config ar fi literă moartă și un 429 de la
        LinkedIn ar coborî statusul la fiecare rulare.
        """
        m = re.match(r"\[[^/\]]+/([^\]]+)\]", error)
        return bool(m) and m.group(1) in market.best_effort_sources

    def _run_source(self, source: str, market: Market) -> tuple[list[dict], list[str]]:
        """Rulează o sursă pentru o piață, prinzând orice excepție.

        Contractul e uniform: `(joburi, erori)`. O sursă care crapă întoarce
        lista goală plus eroarea, nu propagă excepția.
        """
        try:
            if source == "jobspy":
                return self.jobspy.scrape_market(market)
            if source == "remote_boards":
                return self.remote_boards.scrape_market(market)
            if source == "ro_boards":
                return self.ro_boards.scrape_market(market)
            if source == "career_pages":
                # CareerPageScraper e anterior conceptului de piață și are
                # altă semnătură; îl adaptăm aici, nu îl rescriem.
                return self.career.scrape(), []
            return [], [f"[{market.key}] sursă necunoscută: {source}"]
        except ImportError as e:
            return [], [f"[{market.key}/{source}] dependență lipsă: {e}"]
        except Exception as e:
            return [], [f"[{market.key}/{source}] {e}"]
