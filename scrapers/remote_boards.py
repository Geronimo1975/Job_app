"""
Board-uri remote cu acces public — RemoteOK, Remotive, WeWorkRemotely.

Cea mai stabilă categorie de surse din pipeline: primele două au JSON
public, a treia RSS. Fără scraping HTML, fără dependențe noi.
"""

import html
import re
from pathlib import Path

import httpx
# NU `xml.etree.ElementTree`: feed-ul RSS e input extern, iar parserul din
# stdlib e vulnerabil la expansiune de entități („billion laughs").
from defusedxml import ElementTree as ET

from scrapers.base import Market, make_job

_REMOTEOK_URL = "https://remoteok.com/api"
_REMOTIVE_URL = "https://remotive.com/api/remote-jobs"
_WWR_URL = "https://weworkremotely.com/categories/remote-programming-jobs.rss"

_TIMEOUT = 30
# RemoteOK cere un User-Agent identificabil; fără el întoarce 403.
_HEADERS = {"User-Agent": "JobApp/1.0 (+https://github.com/Geronimo1975)"}

_TAG_RE = re.compile(r"<[^>]+>")


def _clean(text: str) -> str:
    """Scoate marcajul HTML și decodează entitățile."""
    return html.unescape(_TAG_RE.sub(" ", str(text or ""))).strip()


class RemoteBoardsScraper:
    """Agregă joburi remote din board-uri cu API/feed public."""

    def __init__(self, config: dict, project_root: Path):
        self.config = config
        self.project_root = project_root

    def scrape_market(self, market: Market) -> tuple[list[dict], list[str]]:
        """Interoghează cele trei board-uri. Fiecare eșuează independent."""
        jobs: list[dict] = []
        errors: list[str] = []

        for name, fetch in (
            ("remoteok", self._fetch_remoteok),
            ("remotive", self._fetch_remotive),
            ("weworkremotely", self._fetch_wwr),
        ):
            try:
                jobs.extend(fetch(market))
            except Exception as e:
                errors.append(f"[{market.key}/{name}] {e}")

        return jobs, errors

    # ── Fetch ───────────────────────────────────────────────

    def _fetch_remoteok(self, market: Market) -> list[dict]:
        resp = httpx.get(_REMOTEOK_URL, headers=_HEADERS, timeout=_TIMEOUT)
        resp.raise_for_status()
        return self._parse_remoteok(resp.json(), market)

    def _fetch_remotive(self, market: Market) -> list[dict]:
        # Remotive acceptă un singur termen de căutare; folosim primul
        # keyword al fiecărui grup ca reprezentant.
        jobs: list[dict] = []
        for group in market.keyword_groups:
            resp = httpx.get(
                _REMOTIVE_URL, params={"search": group[0]},
                headers=_HEADERS, timeout=_TIMEOUT,
            )
            resp.raise_for_status()
            jobs.extend(self._parse_remotive(resp.json(), market))
        return self._dedupe(jobs)

    def _fetch_wwr(self, market: Market) -> list[dict]:
        resp = httpx.get(_WWR_URL, headers=_HEADERS, timeout=_TIMEOUT)
        resp.raise_for_status()
        return self._parse_wwr(resp.text, market)

    # ── Parse ───────────────────────────────────────────────

    def _parse_remoteok(self, payload: list, market: Market) -> list[dict]:
        jobs = []
        for entry in payload or []:
            # Primul element al feed-ului e mereu nota legală, fără `position`.
            if not isinstance(entry, dict) or not entry.get("position"):
                continue
            if not entry.get("url"):
                continue
            jobs.append(
                make_job(
                    source=f"remoteok-{market.key}",
                    platform="remoteok",
                    title=entry.get("position", ""),
                    company=entry.get("company", ""),
                    location=entry.get("location", "Remote"),
                    url=entry.get("url", ""),
                    description=_clean(entry.get("description", "")),
                    market_key=market.key,
                    language=market.language,
                    date_posted=str(entry.get("date", "")),
                )
            )
        return self._dedupe(jobs)

    def _parse_remotive(self, payload: dict, market: Market) -> list[dict]:
        jobs = []
        for entry in (payload or {}).get("jobs", []):
            if not entry.get("url"):
                continue
            jobs.append(
                make_job(
                    source=f"remotive-{market.key}",
                    platform="remotive",
                    title=entry.get("title", ""),
                    company=entry.get("company_name", ""),
                    location=entry.get("candidate_required_location", "Remote"),
                    url=entry.get("url", ""),
                    description=_clean(entry.get("description", "")),
                    market_key=market.key,
                    language=market.language,
                    date_posted=str(entry.get("publication_date", "")),
                )
            )
        return self._dedupe(jobs)

    def _parse_wwr(self, xml_text: str, market: Market) -> list[dict]:
        """Parsează feed-ul RSS. XML invalid sau ostil → listă goală.

        Se prinde `Exception`, nu doar `ParseError`: defusedxml ridică tipuri
        proprii (`EntitiesForbidden`, `DTDForbidden`) pentru feed-uri ostile,
        iar reacția corectă la oricare dintre ele e aceeași — zero joburi din
        sursa asta, runul merge mai departe.
        """
        try:
            root = ET.fromstring(xml_text)
        except Exception:
            return []

        jobs = []
        for item in root.iterfind(".//item"):
            link = (item.findtext("link") or "").strip()
            raw_title = (item.findtext("title") or "").strip()
            if not link or not raw_title:
                continue
            # WWR pune „Firmă: Poziție" într-un singur câmp.
            company, _, title = raw_title.partition(":")
            if not title:
                company, title = "", raw_title
            jobs.append(
                make_job(
                    source=f"weworkremotely-{market.key}",
                    platform="weworkremotely",
                    title=title.strip(),
                    company=company.strip(),
                    location="Remote",
                    url=link,
                    description=_clean(item.findtext("description") or ""),
                    market_key=market.key,
                    language=market.language,
                    date_posted=(item.findtext("pubDate") or "").strip(),
                )
            )
        return self._dedupe(jobs)

    @staticmethod
    def _dedupe(jobs: list[dict]) -> list[dict]:
        """Dedupe local pe URL; dedupe-ul global e treaba lui store/seen.py."""
        seen: set[str] = set()
        unique = []
        for job in jobs:
            if job["url"] in seen:
                continue
            seen.add(job["url"])
            unique.append(job)
        return unique
