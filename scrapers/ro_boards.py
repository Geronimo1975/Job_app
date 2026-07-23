"""
Board-uri românești — eJobs, BestJobs.

Sursă BEST-EFFORT prin design: aceste site-uri nu au API public, deci
parsarea se face pe HTML și **se va strica** la primul redesign. Eșecul ei
nu trebuie să marcheze niciodată runul ca FAILED; de aceea piața o
declară în `best_effort_sources`.

Notă: eJobs randează server-side doar primele carduri din listă; restul
sunt placeholdere (`job-card-wrapper__temp`) fără link, deci fără Apply URL
utilizabil — parserul le sare intenționat.
"""

from pathlib import Path
from urllib.parse import urljoin

import httpx

from scrapers.base import Market, make_job

try:
    from bs4 import BeautifulSoup
    HAS_BS4 = True
except ImportError:
    BeautifulSoup = None
    HAS_BS4 = False

_EJOBS_BASE = "https://www.ejobs.ro"
_EJOBS_SEARCH = "https://www.ejobs.ro/locuri-de-munca/{keyword}"
_TIMEOUT = 30
# Fără User-Agent de browser, eJobs întoarce 403.
_HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}

# ── Selectori CSS ────────────────────────────────────────────
# Verificați pe HTML-ul real din 2026-07-23 (fixture: tests/fixtures/
# ejobs_sample.html). Testul `test_extracts_jobs_from_real_fixture` confirmă
# că sunt corecți; dacă întoarce 0 joburi, site-ul s-a schimbat.
# Sunt izolate aici tocmai pentru că eJobs nu are API și HTML-ul se schimbă:
# la următoarea stricare se editează doar acest bloc.
# Atenție: firma și locația împart clasa de bază
# `job-card-content-middle__info`; firma are modificatorul `--darker`,
# locația e elementul FĂRĂ modificator.
_SELECTOR_CARD = "li.job-card-wrapper"
_SELECTOR_TITLE = "h2.job-card-content-middle__title"
_SELECTOR_LINK = "a[href*='/locuri-de-munca/']"
_SELECTOR_COMPANY = ".job-card-content-middle__info--darker"
_SELECTOR_LOCATION = (
    ".job-card-content-middle__info:not(.job-card-content-middle__info--darker)"
)


class RoBoardsScraper:
    """Scraper pentru board-urile de joburi din România."""

    def __init__(self, config: dict, project_root: Path):
        self.config = config
        self.project_root = project_root

    def scrape_market(self, market: Market) -> tuple[list[dict], list[str]]:
        """Caută pe eJobs pentru fiecare grup de keywords.

        Best-effort: orice eroare de rețea/HTTP ajunge în lista `errors`,
        nu ridică excepție — runul pipeline-ului nu pică din cauza eJobs.
        """
        if not HAS_BS4:
            return [], ["bs4 nu e instalat — ro_boards sărit"]

        jobs: list[dict] = []
        errors: list[str] = []

        for group in market.keyword_groups:
            keyword = group[0].lower().replace(" ", "-")
            try:
                resp = httpx.get(
                    _EJOBS_SEARCH.format(keyword=keyword),
                    headers=_HEADERS, timeout=_TIMEOUT, follow_redirects=True,
                )
                resp.raise_for_status()
                jobs.extend(self._parse_ejobs(resp.text, market))
            except Exception as e:
                errors.append(f"[{market.key}/ejobs] {group[0]}: {e}")

        return self._dedupe(jobs), errors

    def _parse_ejobs(self, html_text: str, market: Market) -> list[dict]:
        """Extrage anunțurile din pagina de rezultate eJobs.

        HTML-ul care nu conține anunțuri dă listă goală, nu excepție: e
        situația normală când site-ul se schimbă. Cardurile fără titlu sau
        fără link (placeholdere client-side) sunt sărite — fără URL nu
        există cheie de upsert în Sheet.
        """
        soup = BeautifulSoup(html_text, "html.parser")
        jobs = []

        for card in soup.select(_SELECTOR_CARD):
            title_el = card.select_one(_SELECTOR_TITLE)
            link = card.select_one(_SELECTOR_LINK)
            if not title_el or not link or not link.get("href"):
                continue
            title = title_el.get_text(" ", strip=True)
            if not title:
                continue

            company_el = card.select_one(_SELECTOR_COMPANY)
            location_el = card.select_one(_SELECTOR_LOCATION)

            jobs.append(
                make_job(
                    source=f"ejobs-{market.key}",
                    platform="ejobs",
                    title=title,
                    company=company_el.get_text(" ", strip=True) if company_el else "",
                    location=location_el.get_text(" ", strip=True) if location_el else "Romania",
                    # Link-urile din listă sunt relative.
                    url=urljoin(_EJOBS_BASE, link["href"]),
                    description="",  # descrierea completă e pe pagina jobului
                    market=market,
                )
            )

        return jobs

    @staticmethod
    def _dedupe(jobs: list[dict]) -> list[dict]:
        """Elimină duplicatele după URL (același job poate apărea la mai
        multe grupuri de keywords)."""
        seen: set[str] = set()
        unique = []
        for job in jobs:
            if job["url"] in seen:
                continue
            seen.add(job["url"])
            unique.append(job)
        return unique
