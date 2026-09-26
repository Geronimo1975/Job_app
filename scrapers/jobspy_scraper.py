"""
JobSpy Scraper — agregare multi-platformă, organizată pe piețe.

Varianta veche făcea produs cartezian keywords × locații (49 de apeluri
pentru 7 keywords și 7 orașe), din care majoritatea se loveau reciproc de
rate-limit. Acum: un apel per (grup de keywords, site), rulate în paralel.

`scrape_query()` e SINGURUL loc care apelează `jobspy.scrape_jobs()`.
`scrape_market()` e doar un wrapper peste el, pentru calea CLI/pipeline.
"""

import re
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

# Separatorii cu care pot veni lipite mai multe adrese într-o singură celulă
# `emails`: virgulă (forma produsă de upstream), punct-și-virgulă, spațiu.
_EMAIL_SEPARATORS = re.compile(r"[,;\s]+")

# Adrese care apar în textul anunțurilor dar NU primesc candidaturi: clauze
# legale standard, contacte de presă sau de protecția datelor. Măsurat live
# (2026-08-06) pe UAE/RO/CH, majoritatea adreselor extrase erau exact astea
# (`reasonable.accommodations@`, `privacy@`, `paytransparency@`) — o
# candidatură trimisă acolo e mai rea decât lipsa adresei: arată ca spam.
# Potrivire pe subșir: termenii sunt destul de lungi cât să nu dea fals pozitiv.
_EMAIL_DENY_SUBSTRINGS = (
    "privacy", "datenschutz", "legal", "compliance", "accommodation",
    "paytransparency", "pay-transparency", "press", "media", "noreply",
    "no-reply", "donotreply", "do-not-reply", "unsubscribe", "abuse",
    "postmaster", "webmaster", "security", "inclusion", "diversity",
    # Protecția datelor în limbile piețelor europene (JOB-58). Lista era doar
    # engleză+germană, iar `Ochrona.danych@roche.com` (poloneză) a trecut drept
    # adresă de candidatură într-o rulare reală. Fiecare piață scrapată își
    # scrie clauza GDPR în limba ei, deci fiecare are nevoie de intrare.
    "ochrona", "daneosobowe", "dane-osobowe",          # pl
    "ochrana", "osobnichudaju", "osobnich-udaju",      # cs
    "privacidad", "proteccion",                        # es
    "privacidade", "protecao",                         # pt
    "personvern", "personopplysning",                  # no
    "personuppgift",                                   # sv
    "persondata",                                      # da
    "tietosuoja",                                      # fi
    "confidentialitate", "protectiadatelor",           # ro
    "confidentialite",                                 # fr
    "riservatezza",                                    # it
)
# Prea scurți pentru subșir („dpo" s-ar prinde în „adpower") — token întreg.
# `rodo`/`avg` sunt GDPR în poloneză, respectiv neerlandeză.
_EMAIL_DENY_TOKENS = frozenset({"dpo", "gdpr", "rodo", "avg"})

# Adrese tipice de candidatură — preferate chiar dacă apar mai târziu în text.
_EMAIL_PREFER_SUBSTRINGS = (
    "bewerbung", "recruit", "career", "karriere", "vacature", "sollicitatie",
    "stellen", "hiring", "talent", "application", "personal",
    # Echivalentele locale ale piețelor europene, ca o adresă bună să nu rămână
    # doar „neutră" și să piardă în fața alteia care se potrivește pe engleză.
    "kariera", "kariery", "rekrutacja",                # pl
    "kariera-cz", "nabor",                             # cs
    "empleo", "trabaja",                               # es
    "emprego", "carreiras",                            # pt
    "rekruttering", "ledige",                          # no/da
    "rekrytering", "lediga",                           # sv
    "rekrytointi", "tyopaikat",                        # fi
    "cariere", "recrutare", "angajari",                # ro
)
# `hr` ca subșir s-ar prinde în „chris@" — token întreg.
_EMAIL_PREFER_TOKENS = frozenset({"hr", "jobs", "job", "apply", "work", "people"})

_RANK_APPLICATION = 0   # adresă de candidatură
_RANK_NEUTRAL = 1       # plauzibilă, fără semnal clar
_RANK_AVOID = 2         # boilerplate — nu se folosește niciodată


def _email_rank(address: str) -> int:
    """Cât de plauzibil e că adresa chiar primește candidaturi (mic = bun)."""
    local = address.rsplit("@", 1)[0].lower()
    # Local part care începe cu punctuație = adresă tăiată de parserul jobspy.
    # Observate live: `_Acquisition@eon.com` (din „Talent_Acquisition@") și
    # `_mb@oracle.com`. Sintactic valide, dar ambele ar da bounce — exact
    # eroarea pe care `require_email` trebuie s-o prevină.
    if local[:1] in {"_", ".", "-", "+"}:
        return _RANK_AVOID
    tokens = {t for t in re.split(r"[^a-z0-9]+", local) if t}
    if any(s in local for s in _EMAIL_DENY_SUBSTRINGS) or (tokens & _EMAIL_DENY_TOKENS):
        return _RANK_AVOID
    if any(s in local for s in _EMAIL_PREFER_SUBSTRINGS) or (tokens & _EMAIL_PREFER_TOKENS):
        return _RANK_APPLICATION
    return _RANK_NEUTRAL


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


def _is_remote_row(row: Any) -> bool:
    """True dacă rândul jobspy descrie un job remote.

    Folosit când `is_remote` a fost scos din apel (vezi `_sanitize`), deci
    filtrul trebuie aplicat local. `is_remote` e semnalul principal, dar Indeed
    nu-l completează mereu; „remote" scris explicit în titlu sau locație e la
    fel de neechivoc și recuperează anunțurile pe care flagul le ratează.
    """
    flag = row.get("is_remote")
    if flag is True or _text(flag).lower() == "true":
        return True
    haystack = f"{_text(row.get('title'))} {_text(row.get('location'))}".lower()
    return re.search(r"\bremote\b", haystack) is not None


def _first_email(value: Any) -> str:
    """Cea mai plauzibilă adresă de CANDIDATURĂ din coloana `emails`.

    Nu mai e „prima adresă plauzibilă": ordinea din anunț nu spune nimic despre
    cui i se trimite candidatura, iar clauzele legale („reasonable
    accommodations", „pay transparency") apar de regulă ÎNAINTEA adresei de
    recrutare. Se alege adresa cu cel mai bun `_email_rank`, iar cele marcate
    `_RANK_AVOID` nu se folosesc deloc — un job fără adresă utilizabilă trebuie
    să arate ca un job fără adresă, ca poarta `require_email` să-l respingă.

    Forma REALĂ din producție e un singur string cu TOATE adresele lipite:
    upstream face `", ".join(emails)` înainte de a construi DataFrame-ul
    (`jobspy/__init__.py:141-143`), deci ramura list/tuple/set nu se execută
    aici niciodată — rămâne doar ca plasă de siguranță dacă upstream schimbă
    forma. Fără spargerea pe separatori, „a@x.de, b@x.de" trecea validarea
    întreagă și ajungea în col „Kontakt Email" și în `msg["To"]` → un singur
    `RCPT TO:<a@x.de, b@x.de>` → `SMTPRecipientsRefused`.
    """
    if isinstance(value, str):
        raw = [value]
    elif isinstance(value, (list, tuple, set)):
        raw = list(value)
    else:
        return ""

    best, best_rank = "", _RANK_AVOID
    for item in raw:
        for candidate in _EMAIL_SEPARATORS.split(_text(item)):
            if "@" not in candidate or "." not in candidate.rsplit("@", 1)[-1]:
                continue
            rank = _email_rank(candidate)
            if rank < best_rank:
                best, best_rank = candidate, rank
                if rank == _RANK_APPLICATION:
                    return best      # nu există ceva mai bun
    return best


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

        # Când `_sanitize` a scos `is_remote` din apel, filtrul cerut de piață
        # se aplică aici. Condiția se citește din kwargs, nu dintr-un flag
        # separat: pe site-urile unde filtrul a ajuns la upstream (LinkedIn) o
        # a doua filtrare ar tăia rândurile pe un câmp pe care acele site-uri
        # nu-l completează.
        filter_remote = bool(q.remote) and "is_remote" not in kwargs

        jobs = []
        for _, row in df.iterrows():
            if filter_remote and not _is_remote_row(row):
                continue
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

        Ordinea contează: `is_remote` se evaluează PRIMUL, fiindcă renunțarea
        la el poate face `hours_old` folosibil din nou.
        """
        warnings: list[str] = []

        # Pe Indeed, `is_remote` nu costă doar `hours_old` — anulează complet
        # `search_term`. Verificat live (2026-08-06, UAE): interogarea booleană
        # „AI Developer/AI Automation/…" întoarce cu `is_remote=True` joburi
        # fără nicio legătură („Senior Real Estate Broker", „Nursery Admin
        # Manager"), identice pentru toate cele 4 grupuri de keywords — semn că
        # termenul e ignorat. Fără `is_remote`: „AI Engineer / Agentic AI
        # Developer", „AI Automation Engineer". Termenul de căutare valorează
        # mai mult decât filtrul, deci filtrul cade și remote se aplică local,
        # pe rândurile întoarse (vezi `_call` + `_is_remote_row`).
        if site == "indeed" and kwargs.get("is_remote") and kwargs.get("search_term"):
            kwargs.pop("is_remote")
            warnings.append(
                f"{WARNING_PREFIX} [{q.market_key}/indeed] am renunțat la "
                "is_remote: Indeed ignoră search_term când filtrul remote e "
                "activ (remote se filtrează local, pe câmpul întors)"
            )

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
