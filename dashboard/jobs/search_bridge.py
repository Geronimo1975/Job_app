"""Puntea dashboard → JobSpy: rulează căutări în fundal și le pune în staging.

Refolosește `scrapers/` read-only, exact ca `generation_bridge` pentru
`generators/`. Nicio funcție nu ridică excepții spre views — eșecul e codat în
`{ok, error}` sau în `SearchRun.status`.

`store/seen.py` e citit, NICIODATĂ scris: indexul de dedupe aparține
pipeline-ului CLI, iar două procese care scriu același JSON s-ar călca.
"""
import sys
import threading
from datetime import timedelta
from pathlib import Path

from django.db import connections
from django.utils import timezone

from filters.relevance import prescore
from scrapers.base import SearchQuery
from scrapers.jobspy_scraper import JobSpyScraper, WARNING_PREFIX
from store.seen import SeenIndex

from .models import Job, SearchResult, SearchRun
from .sheet_bridge import load_pipeline_config

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SEEN_PATH = _REPO_ROOT / "data" / "seen_jobs.json"

# Thread-ul e `daemon`, deci moare la restart de runserver. Fără expirare, un
# `running` orfan ar bloca pe veci garda de concurență.
_STALE_AFTER = timedelta(minutes=20)


def _scrape_config() -> dict:
    return (load_pipeline_config().get("scraping", {}) or {})


def presets() -> list[dict]:
    """Piețele din settings.yaml, în forma cerută de formular."""
    out = []
    scraping = _scrape_config()
    for key, cfg in (scraping.get("markets", {}) or {}).items():
        locations = list(cfg.get("locations", []) or [])
        groups = [list(g) for g in (cfg.get("keyword_groups", []) or [])]
        out.append({
            "key": key,
            # Cheia sub numele câmpului ascuns din formular, ca `initial=preset`
            # să completeze și `preset`.
            "preset": key,
            "label": key,
            "country": str(cfg.get("country", "")),
            "language": str(cfg.get("language", "en")),
            "location": locations[0] if locations else str(cfg.get("country", "")),
            "remote": bool(cfg.get("remote", False)),
            # Un grup pe linie, cuvintele din grup separate prin virgulă.
            "keywords": "\n".join(", ".join(g) for g in groups),
            "sites": list(scraping.get("jobspy_sites", ["indeed"])),
            "hours_old": int(scraping.get("hours_old", 72)),
            "results_wanted": int(scraping.get("max_results_per_call", 50)),
            # Aceleași valori implicite ca `Market.to_query` — altfel o
            # căutare pornită dintr-un preset cu site-ul `google` bifat
            # pierde silențios termenul special de căutare.
            "google_search_term": str(scraping.get("google_search_term", "") or ""),
            "linkedin_fetch_description": bool(
                scraping.get("linkedin_fetch_description", False)
            ),
        })
    return out


def parse_keyword_groups(text: str) -> list[list[str]]:
    """Textarea → grupuri: o linie = un grup, virgula separă cuvintele."""
    groups = []
    for line in (text or "").splitlines():
        words = [w.strip() for w in line.split(",") if w.strip()]
        if words:
            groups.append(words)
    return groups


def query_from_params(params: dict) -> SearchQuery:
    """Parametrii validați ai formularului → interogare JobSpy."""
    hours_old = params.get("hours_old")
    distance = params.get("distance")
    return SearchQuery(
        keyword_groups=parse_keyword_groups(params.get("keywords", "")),
        sites=list(params.get("sites") or ["indeed"]),
        location=str(params.get("location", "") or ""),
        country=str(params.get("country", "") or ""),
        language=str(params.get("language", "en") or "en"),
        market_key="adhoc",
        remote=bool(params.get("remote")),
        hours_old=int(hours_old) if hours_old not in (None, "") else None,
        results_wanted=int(params.get("results_wanted") or 50),
        job_type=str(params.get("job_type", "") or ""),
        distance=int(distance) if distance not in (None, "") else None,
        google_search_term=str(params.get("google_search_term", "") or ""),
        linkedin_fetch_description=bool(params.get("linkedin_fetch_description")),
    )


def label_for(params: dict) -> str:
    """Etichetă scurtă pentru listă: primul grup + câte mai sunt + locația."""
    groups = parse_keyword_groups(params.get("keywords", ""))
    first = ", ".join(groups[0]) if groups else "(fără cuvinte-cheie)"
    extra = f" +{len(groups) - 1} grupuri" if len(groups) > 1 else ""
    location = params.get("location") or "?"
    label = f"{first}{extra} · {location}"
    # `SearchRun.label` are max_length=200; SQLite nu-l impune, dar Postgres
    # ar respinge un INSERT peste limită — trunchiem defensiv.
    return label[:190]


def expire_stale_runs() -> int:
    """Marchează drept întrerupte rulările `running` mai vechi decât pragul."""
    cutoff = timezone.now() - _STALE_AFTER
    return SearchRun.objects.filter(
        status=SearchRun.RUNNING, started_at__lt=cutoff
    ).update(
        status=SearchRun.ERROR,
        finished_at=timezone.now(),
        errors_json=["Rulare întreruptă (probabil un restart de server). "
                     "Pornește o căutare nouă."],
    )


def active_run() -> SearchRun | None:
    """Rularea în curs, dacă există (după expirarea celor moarte)."""
    expire_stale_runs()
    return SearchRun.objects.filter(status=SearchRun.RUNNING).first()


def start_search(params: dict) -> dict:
    """Creează rularea și pornește worker-ul. Nu așteaptă rezultatul."""
    running = active_run()
    if running:
        return {
            "ok": False, "run": running,
            "error": f"Căutarea #{running.pk} e încă în curs — "
                     "două rulări simultane înseamnă rate-limit garantat.",
        }

    run = SearchRun.objects.create(label=label_for(params), params_json=params)
    threading.Thread(target=run_search_now, args=(run.pk,), daemon=True).start()
    return {"ok": True, "run": run, "error": ""}


def _load_seen() -> SeenIndex:
    """Indexul de dedupe, citit read-only (punct de injecție în teste)."""
    return SeenIndex.load(_SEEN_PATH)


def run_search_now(run_pk: int) -> None:
    """Corpul worker-ului. Sincron — testele îl apelează direct.

    Nu ridică niciodată: orice eșec devine `status=error` pe rând, altfel
    thread-ul ar muri în tăcere și rândul ar rămâne `running` pe veci. UN
    SINGUR `try/except/finally` acoperă și `get()`-ul rândului (o citire
    SQLite dintr-un thread separat, unde un `OperationalError` de lock e
    posibil) — altfel o cale de ieșire timpurie ar sări peste `finally` și
    ar lăsa conexiunea thread-ului nefolosită.
    """
    run = None
    try:
        run = SearchRun.objects.get(pk=run_pk)
        config = load_pipeline_config()
        query = query_from_params(run.params_json)
        jobs, messages = JobSpyScraper(config, _REPO_ROOT).scrape_query(query)

        run.warnings_json = [m for m in messages if m.startswith(WARNING_PREFIX)]
        run.errors_json = [m for m in messages if not m.startswith(WARNING_PREFIX)]

        seen = _load_seen()
        rows, urls = [], set()
        for job in jobs:
            url = str(job.get("url") or "").strip()
            if not url or url in urls:
                continue
            urls.add(url)
            score, reasons = prescore(job, config)
            rows.append(SearchResult(
                run=run, url=url,
                title=job.get("title", ""), company=job.get("company", ""),
                location=job.get("location", ""), platform=job.get("platform", ""),
                date_posted=job.get("date_posted", ""),
                language=job.get("language", ""),
                description=job.get("description", ""),
                email_hr=job.get("email_hr", ""),
                salary_min=_as_int(job.get("salary_min")),
                salary_max=_as_int(job.get("salary_max")),
                prescore=score, prescore_reasons_json=reasons,
                seen_state=seen.decision_for(job),
                raw_json=job.get("raw") or {},
            ))
        SearchResult.objects.bulk_create(rows, ignore_conflicts=True)

        run.found_count = len(rows)
        # 0 rezultate + erori = eșec real; 0 rezultate curate = doar empty state.
        run.status = SearchRun.ERROR if (not rows and run.errors_json) else SearchRun.DONE
    except Exception as e:
        if run is None:
            # `get()` a eșuat: nu există niciun rând pe care să scriem
            # eroarea (pk inexistent sau lock SQLite) — doar stderr.
            print(f"  ⚠ search_bridge: nu pot încărca SearchRun #{run_pk}: "
                  f"{type(e).__name__}: {e}", file=sys.stderr)
        else:
            run.status = SearchRun.ERROR
            run.errors_json = (run.errors_json or []) + [f"{type(e).__name__}: {e}"]
    finally:
        if run is not None:
            run.finished_at = timezone.now()
            # `update_fields` explicit: un `save()` fără el ar suprascrie cu
            # snapshotul luat la începutul rulării orice câmp schimbat între
            # timp de altcineva (ex. `imported_count` la un import concurent).
            run.save(update_fields=[
                "status", "found_count", "warnings_json", "errors_json", "finished_at",
            ])
        # Thread separat + SQLite: conexiunea trebuie închisă explicit, pe
        # ORICE cale de ieșire — inclusiv cea în care `get()`-ul eșuează.
        connections.close_all()


def _as_int(value) -> int | None:
    """Salariile vin din pandas ca float/NaN; modelul cere int sau None."""
    try:
        if value is None or (isinstance(value, float) and value != value):
            return None
        return int(float(value))
    except (TypeError, ValueError, OverflowError):
        return None


# Stări în care pachetul de email e în zbor sau deja trimis: un import peste
# ele ar distruge editările sau arhiva a ceea ce a plecat efectiv.
LOCKED_STATES = ("generated", "sending", "sent")


def import_results(result_ids: list[int]) -> dict:
    """Transformă rezultatele selectate în rânduri `Job` (stare draft).

    Fără apel AI: JobSpy livrează deja titlul, compania, descrierea și adesea
    emailul, deci `extract_job()` rămâne doar pentru URL-urile lipite manual.

    Nicio excepție nu urcă spre view: `run_search_now`, din același fișier,
    demonstrează că un lock SQLite (mai multe procese pe același fișier) e un
    scenariu real, nu ipotetic. O eroare la un rezultat e contorizată în
    `report["errors"]` și lăsată în stderr — bucla continuă cu restul, ca un
    singur rând stricat să nu blocheze importul celorlalte sau să lase
    `imported_count` neactualizat.
    """
    report = {"created": 0, "updated": 0, "skipped": 0, "no_email": 0,
              "errors": 0, "message": ""}
    if not result_ids:
        report["message"] = "Nimic selectat — bifează cel puțin un rezultat."
        return report

    try:
        results = list(SearchResult.objects.filter(pk__in=result_ids))
    except Exception as e:
        report["errors"] += 1
        print(f"  ⚠ search_bridge: import_results — nu pot citi rezultatele "
              f"selectate: {type(e).__name__}: {e}", file=sys.stderr)
        report["message"] = "citirea rezultatelor selectate a eșuat — vezi log-ul serverului."
        return report

    touched_runs: set[int] = set()

    for res in results:
        try:
            touched_runs.add(res.run_id)
            existing = Job.objects.filter(url=res.url).first()

            if existing and existing.send_state in LOCKED_STATES:
                report["skipped"] += 1
                if res.imported_job_id != existing.pk:
                    res.imported_job = existing
                    res.save(update_fields=["imported_job"])
                continue

            job, created = Job.objects.update_or_create(
                url=res.url,
                defaults={
                    "title": res.title, "company": res.company,
                    "location": res.location, "description": res.description,
                    "email_hr": res.email_hr, "language": res.language,
                    "date_posted": res.date_posted,
                    "salary_min": res.salary_min, "salary_max": res.salary_max,
                    "source": f"jobspy:{res.platform or 'unknown'}",
                    "extraction_status": "ok",
                    "send_state": "draft",
                    "raw_json": res.raw_json or {},
                },
            )
            report["created" if created else "updated"] += 1
            if not (res.email_hr or "").strip():
                report["no_email"] += 1
            res.imported_job = job
            res.save(update_fields=["imported_job"])
        except Exception as e:
            report["errors"] += 1
            print(f"  ⚠ search_bridge: import eșuat pentru rezultatul #{res.pk} "
                  f"({res.url}): {type(e).__name__}: {e}", file=sys.stderr)

    for run_id in touched_runs:
        try:
            run = SearchRun.objects.get(pk=run_id)
            run.imported_count = run.results.filter(imported_job__isnull=False).count()
            run.save(update_fields=["imported_count"])
        except Exception as e:
            report["errors"] += 1
            print(f"  ⚠ search_bridge: nu pot actualiza imported_count pentru "
                  f"rularea #{run_id}: {type(e).__name__}: {e}", file=sys.stderr)

    parts = []
    if report["created"]:
        parts.append(f"{report['created']} importate")
    if report["updated"]:
        parts.append(f"{report['updated']} actualizate")
    if report["skipped"]:
        parts.append(f"{report['skipped']} sărite (pachet în zbor sau deja trimis)")
    if report["no_email"]:
        parts.append(f"{report['no_email']} fără email HR — completează-l "
                     "înainte de „Transmite”")
    if report["errors"]:
        parts.append(f"{report['errors']} eșuate — vezi log-ul serverului")
    report["message"] = " · ".join(parts) or "Nimic de importat."
    return report
