"""
Board-uri românești — eJobs, BestJobs.

Sursă BEST-EFFORT prin design: aceste site-uri nu au API public, deci
parsarea se face pe HTML și **se va strica** la primul redesign. Eșecul ei
nu trebuie să marcheze niciodată runul ca FAILED; de aceea piața o
declară în `best_effort_sources`.

Notă: eJobs randează server-side doar primele ~3 carduri din listă; restul
sunt placeholdere (`job-card-wrapper__temp`) cu titlu și firmă, dar FĂRĂ link.
Toate cele ~40 de anunțuri ale paginii există însă în blocul JSON-LD
`ItemList`, cu nume și URL — de acolo se citesc (JOB-49). Varianta care se
uita doar la carduri pierdea 37 din 40 de anunțuri per căutare.
"""

import json
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urljoin

import httpx

from scrapers.base import Market, make_job
from scrapers.jobspy_scraper import WARNING_PREFIX, _first_email

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
# Placeholderele client-side: fără link, dar cu <strong>titlu</strong> +
# <strong>firmă</strong>. Sunt singura sursă de firmă pentru anunțurile care
# apar doar în `ItemList`.
_SELECTOR_PLACEHOLDER = "li.job-card-wrapper div.job-card-wrapper__temp"

_JSONLD_RE = re.compile(
    r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', re.S
)
_TAG_RE = re.compile(r"<[^>]+>")
_EMAIL_RE = re.compile(r"[\w._%+-]+@[\w.-]+\.\w{2,}")

# Câte pagini de detaliu se cer simultan. eJobs limitează agresiv: măsurat
# 2026-08-06, cu 4 workeri 81 din 106 cereri au primit 429 — și, fiindcă
# `raise_for_status()` nu era apelat, un 429 arăta identic cu o pagină fără
# `JobPosting`, deci scraperul raporta „0 erori" cu 76% din îmbogățire pierdută.
_DETAIL_WORKERS = 2
# După atâtea 429-uri renunțăm la restul: odată ce limita s-a activat, cererile
# următoare eșuează oricum, iar insistența doar prelungește blocarea.
_RATE_LIMIT_GIVE_UP = 8

# Rezultatele unei încercări de îmbogățire — decid ce se raportează.
_DETAIL_OK = "ok"
_DETAIL_RATE_LIMITED = "rate_limited"
_DETAIL_UNAVAILABLE = "unavailable"
# Plafon de siguranță pentru numărul de pagini de detaliu cerute într-o rulare.
# O pagină de listă are ~40 de anunțuri, deci 4 grupuri de keywords încap lejer
# sub el; e o plasă pentru cazul în care eJobs ar începe să pagineze, nu o
# limită care să muște normal. NU se leagă de `max_results_per_call` (acela e
# numărul de rezultate cerute per apel jobspy — altă unitate).
_MAX_DETAIL_FETCHES = 200


def _iter_jsonld(html_text: str):
    """Blocurile JSON-LD parsabile din pagină. Cele stricate se sar tăcut."""
    for block in _JSONLD_RE.findall(html_text):
        try:
            yield json.loads(block)
        except (ValueError, TypeError):
            continue


def _find_typed(node, wanted: str):
    """Primul obiect cu `@type == wanted`, oriunde în structură.

    eJobs împachetează `ItemList`/`JobPosting` la adâncimi diferite (uneori
    într-un `@graph`), deci căutarea trebuie să fie recursivă, nu pe o cale fixă.
    """
    if isinstance(node, dict):
        if node.get("@type") == wanted:
            return node
        for value in node.values():
            found = _find_typed(value, wanted)
            if found is not None:
                return found
    elif isinstance(node, list):
        for value in node:
            found = _find_typed(value, wanted)
            if found is not None:
                return found
    return None


def _first_node(value) -> dict:
    """Primul nod dintr-un câmp JSON-LD care poate fi dict SAU listă.

    Schema.org permite ambele forme pentru `hiringOrganization`/`jobLocation`/
    `address`, iar eJobs chiar folosește lista (verificat live 2026-08-22).
    Fără normalizarea asta, `.get()` pe listă ridica AttributeError — prins de
    `except Exception` din `_enrich_from_detail`, deci un anunț îmbogățit cu
    succes se raporta `unavailable` și pierdea locația și data.
    """
    if isinstance(value, list):
        value = value[0] if value else None
    return value if isinstance(value, dict) else {}


def _plain_text(html_fragment: str) -> str:
    """Descrierea din JSON-LD vine cu tag-uri HTML înăuntru."""
    return " ".join(_TAG_RE.sub(" ", html_fragment or "").split())


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
            # Un grup gol în settings.yaml nu are voie să spargă contractul
            # best-effort („nu ridică") cu un IndexError pe group[0].
            if not group:
                continue
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

        unique = self._dedupe(jobs)

        # Descrierea și emailul există doar pe pagina fiecărui anunț: un request
        # per job. Un job neîmbogățit rămâne fără email, deci cu
        # `require_email: true` n-ar mai putea trece poarta — de aceea ce nu
        # s-a putut lua se RAPORTEAZĂ, nu se pierde tăcut.
        to_enrich = unique[:_MAX_DETAIL_FETCHES]
        if len(unique) > _MAX_DETAIL_FETCHES:
            errors.append(
                f"{WARNING_PREFIX} [{market.key}/ejobs] {len(unique) - _MAX_DETAIL_FETCHES} "
                f"anunțuri au rămas fără descriere/email (plafon {_MAX_DETAIL_FETCHES} "
                "pagini de detaliu)"
            )
        errors.extend(self._enrich_all(to_enrich, market))
        return unique, errors

    def _enrich_all(self, jobs: list[dict], market: Market) -> list[str]:
        """Cere paginile de detaliu, cu oprire la rate limit. Întoarce mesaje.

        Oprirea nu e o optimizare: odată ce eJobs a activat limita, cererile
        următoare primesc 429 oricum, iar insistența doar prelungește blocarea.
        """
        if not jobs:
            return []

        stop = threading.Event()
        lock = threading.Lock()
        counters = {_DETAIL_OK: 0, _DETAIL_RATE_LIMITED: 0, _DETAIL_UNAVAILABLE: 0}
        skipped = 0

        def run(job: dict) -> None:
            nonlocal skipped
            if stop.is_set():
                with lock:
                    skipped += 1
                return
            outcome = self._enrich_from_detail(job)
            with lock:
                counters[outcome] = counters.get(outcome, 0) + 1
                if (outcome == _DETAIL_RATE_LIMITED
                        and counters[_DETAIL_RATE_LIMITED] >= _RATE_LIMIT_GIVE_UP):
                    stop.set()

        with ThreadPoolExecutor(max_workers=_DETAIL_WORKERS) as pool:
            list(pool.map(run, jobs))

        incomplete = counters[_DETAIL_RATE_LIMITED] + counters[_DETAIL_UNAVAILABLE] + skipped
        if not incomplete:
            return []
        detail = f"{counters[_DETAIL_RATE_LIMITED]} × 429" if counters[_DETAIL_RATE_LIMITED] else ""
        if skipped:
            detail = f"{detail}, {skipped} sărite după oprire" if detail else f"{skipped} sărite"
        return [
            f"{WARNING_PREFIX} [{market.key}/ejobs] {incomplete}/{len(jobs)} anunțuri "
            f"au rămas fără descriere și email"
            + (f" ({detail})" if detail else "")
        ]

    def _parse_ejobs(self, html_text: str, market: Market) -> list[dict]:
        """Extrage anunțurile din pagina de rezultate eJobs.

        Sursa de adevăr e blocul JSON-LD `ItemList`: el conține TOATE anunțurile
        paginii cu nume și URL, inclusiv pe cele randate doar ca placeholder.
        Cardurile HTML rămân folosite pentru firmă/locație, pe care `ItemList`
        nu le are. Dacă `ItemList` lipsește (site schimbat), se cade înapoi pe
        vechea parsare din carduri, ca sursa să degradeze, nu să se oprească.

        HTML-ul fără anunțuri dă listă goală, nu excepție.
        """
        soup = BeautifulSoup(html_text, "html.parser")
        by_title = self._card_details(soup)

        items = None
        for document in _iter_jsonld(html_text):
            found = _find_typed(document, "ItemList")
            if found is not None:
                items = found.get("itemListElement") or []
                break

        if items is None:
            return self._parse_cards_only(soup, market)

        jobs = []
        for entry in items:
            item = (entry or {}).get("item") or {}
            title = str(item.get("name") or "").strip()
            url = str(item.get("id") or entry.get("url") or "").strip()
            if not title or not url:
                continue
            company, location = by_title.get(title.lower(), ("", ""))
            jobs.append(
                make_job(
                    source=f"ejobs-{market.key}",
                    platform="ejobs",
                    title=title,
                    company=company,
                    location=location or "Romania",
                    url=urljoin(_EJOBS_BASE, url),
                    description="",  # se completează din pagina jobului
                    market_key=market.key,
                    language=market.language,
                )
            )
        return jobs

    @staticmethod
    def _card_details(soup) -> dict[str, tuple[str, str]]:
        """titlu (lowercase) → (firmă, locație), din cardurile HTML.

        Două forme: cardul randat complet (are și locație) și placeholderul
        client-side (doar titlu + firmă, în două `<strong>`).
        """
        details: dict[str, tuple[str, str]] = {}

        for card in soup.select(_SELECTOR_CARD):
            title_el = card.select_one(_SELECTOR_TITLE)
            if not title_el:
                continue
            company_el = card.select_one(_SELECTOR_COMPANY)
            location_el = card.select_one(_SELECTOR_LOCATION)
            details[title_el.get_text(" ", strip=True).lower()] = (
                company_el.get_text(" ", strip=True) if company_el else "",
                location_el.get_text(" ", strip=True) if location_el else "",
            )

        for placeholder in soup.select(_SELECTOR_PLACEHOLDER):
            strongs = placeholder.find_all("strong")
            if len(strongs) < 2:
                continue
            title = strongs[0].get_text(" ", strip=True).lower()
            details.setdefault(title, (strongs[1].get_text(" ", strip=True), ""))

        return details

    def _parse_cards_only(self, soup, market: Market) -> list[dict]:
        """Parsarea veche, doar din carduri — rezervă dacă `ItemList` dispare."""
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
                    url=urljoin(_EJOBS_BASE, link["href"]),
                    description="",
                    market_key=market.key,
                    language=market.language,
                )
            )
        return jobs

    def _enrich_from_detail(self, job: dict) -> str:
        """Completează jobul din `JobPosting`-ul paginii de detaliu, pe loc.

        Pagina de listă n-are descriere, deci joburile eJobs ajungeau la
        prescore doar cu titlul, iar `email_hr` era mereu gol — cu
        `require_email: true` niciunul n-ar fi putut trece poarta.

        Best-effort până la capăt: orice eroare lasă jobul neatins (are deja
        titlu și URL), fiindcă o pagină căzută nu trebuie să piardă anunțul.
        Întoarce ÎNSĂ ce s-a întâmplat, ca apelantul să poată raporta
        degradarea — un 429 tăcut arăta exact ca o pagină fără `JobPosting`.
        """
        try:
            resp = httpx.get(job["url"], headers=_HEADERS, timeout=_TIMEOUT,
                             follow_redirects=True)
            if getattr(resp, "status_code", 200) == 429:
                return _DETAIL_RATE_LIMITED
            posting = None
            for document in _iter_jsonld(resp.text):
                posting = _find_typed(document, "JobPosting")
                if posting is not None:
                    break
            if posting is None:
                return _DETAIL_UNAVAILABLE

            description = _plain_text(posting.get("description", ""))
            if description:
                job["description"] = description
                # Aceleași reguli ca pe jobspy: `privacy@` & co. nu sunt adrese
                # de candidatură, deci nu trebuie să treacă drept destinatar.
                if not job.get("email_hr"):
                    job["email_hr"] = _first_email(", ".join(_EMAIL_RE.findall(description)))

            if not job.get("company"):
                organization = _first_node(posting.get("hiringOrganization"))
                job["company"] = str(organization.get("name") or "").strip()

            # „Romania" e valoarea implicită pusă de parser, nu una aflată.
            if not job.get("location") or job["location"] == "Romania":
                address = _first_node(_first_node(posting.get("jobLocation")).get("address"))
                locality = str(address.get("addressLocality") or "").strip()
                if locality:
                    job["location"] = locality

            if not job.get("date_posted"):
                job["date_posted"] = str(posting.get("datePosted") or "").strip()
            return _DETAIL_OK
        except Exception:
            return _DETAIL_UNAVAILABLE

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
