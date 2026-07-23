#!/usr/bin/env python3
"""
Job_app — Main Orchestrator
============================
Pipeline multi-agent pentru automatizarea aplicațiilor de job.

Workflow complet:
  1. ScraperAgent    → Scraping joburi (JobSpy + Career Pages)
  2. TrackerAgent    → Adăugare în Excel Tracker
  3. AnalyzerAgent   → Analiză cerințe + Match Score
  4. CoverLetterAgent → Generare Anschreiben personalizate
  5. DocumentAgent   → Organizare documente + verificare
  6. EmailAgent      → Trimitere emailuri (SMTP)
  7. TrackerAgent    → Actualizare status final + raport

Utilizare:
    python3 main.py                          # Pipeline complet (dry-run)
    python3 main.py --step scrape            # Doar scraping
    python3 main.py --step analyze           # Doar analiză
    python3 main.py --step generate          # Doar generare Anschreiben
    python3 main.py --step send --live       # Trimitere LIVE (nu dry-run!)
    python3 main.py --step report            # Raport status
    python3 main.py --step organize          # Organizare documente
    python3 main.py --step verify            # Verificare documente
    python3 main.py --step full --live       # Pipeline complet LIVE
    python3 main.py --min-score 50           # Filtru scor minim
    python3 main.py --provider brevo         # Folosește Brevo SMTP
"""

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

# Adaugă project root în path
PROJECT_ROOT = Path(__file__).parent.resolve()
sys.path.insert(0, str(PROJECT_ROOT))

from agents.base_agent import AgentResult, AgentStatus, load_config, setup_logger
from agents.scraper_agent import ScraperAgent
from agents.analyzer_agent import AnalyzerAgent
from filters.relevance import passes_prefilter, prescore
from store.seen import ANALIZAT, RESPINS_PREFILTRU, SeenIndex, company_title_key, url_key
from agents.cover_letter_agent import CoverLetterAgent
from agents.email_agent import EmailAgent
from agents.document_agent import DocumentAgent
from agents.tracker_agent import TrackerAgent
from agents.n8n_sync_agent import N8nSyncAgent
from sender.n8n_client import N8nClient
from sender.n8n_mapping import sheet_row_to_job


def print_banner():
    print("""
╔══════════════════════════════════════════════════════╗
║           JOB_APP — Multi-Agent Pipeline             ║
║        Automatisierte Bewerbungen für Gaming          ║
╚══════════════════════════════════════════════════════╝
""")


def print_result(result: AgentResult):
    icons = {
        AgentStatus.SUCCESS: "✅",
        AgentStatus.PARTIAL: "⚠️",
        AgentStatus.FAILED: "❌",
        AgentStatus.RUNNING: "🔄",
        AgentStatus.IDLE: "⬜",
    }
    icon = icons.get(result.status, "?")
    print(f"\n  {icon} {result.summary()}")
    if result.errors:
        for err in result.errors[:3]:
            print(f"     ⚠ {err}")
    print()


def validate_environment(config: dict, provider: str | None = None) -> bool:
    """Validează runtime environment înainte de pornirea pipeline-ului.

    Verifică env vars, directoare și fișierul CV.
    Returnează True dacă toate verificările critice trec.
    """
    checks = []
    has_critical_failure = False

    # ── Env vars ────────────────────────────────────────────
    # ANTHROPIC_API_KEY — necesar pentru AI
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if api_key:
        checks.append(("ANTHROPIC_API_KEY", True, "setat"))
    else:
        checks.append(("ANTHROPIC_API_KEY", False, "lipsă — AI nu va funcționa"))
        has_critical_failure = True

    # SMTP password — per provider activ
    active_provider = provider or config.get("email", {}).get("active_provider", "gmx")
    provider_cfg = config.get("email", {}).get("providers", {}).get(active_provider, {})
    env_key = provider_cfg.get("env_password_key", "")
    if env_key:
        if os.environ.get(env_key, ""):
            checks.append((env_key, True, f"setat (provider: {active_provider})"))
        else:
            checks.append((env_key, False, f"lipsă — email nu va funcționa (provider: {active_provider})"))
            has_critical_failure = True

    # ── Directoare ──────────────────────────────────────────
    project_root = Path(__file__).parent.resolve()
    required_dirs = [
        ("data/", config.get("tracker", {}).get("file", "data/").rsplit("/", 1)[0]),
        ("output/logs", config.get("output", {}).get("logs_dir", "output/logs")),
        ("documents/", config.get("documents", {}).get("cv_file", "documents/").rsplit("/", 1)[0]),
    ]
    for label, rel_path in required_dirs:
        dir_path = project_root / rel_path
        if dir_path.is_dir():
            checks.append((f"Dir {label}", True, str(dir_path)))
        else:
            checks.append((f"Dir {label}", False, f"lipsă: {dir_path}"))

    # ── CV fișier ───────────────────────────────────────────
    cv_rel = config.get("documents", {}).get("cv_file", "")
    if cv_rel:
        cv_path = project_root / cv_rel
        if cv_path.is_file():
            checks.append(("CV fișier", True, cv_path.name))
        else:
            checks.append(("CV fișier", False, f"lipsă: {cv_path}"))

    # ── Raport ──────────────────────────────────────────────
    print(f"\n{'─' * 54}")
    print("  🔍 ENVIRONMENT CHECK")
    print(f"{'─' * 54}")
    for name, ok, detail in checks:
        icon = "✅" if ok else "❌"
        print(f"  {icon} {name}: {detail}")
    print(f"{'─' * 54}")

    if has_critical_failure:
        print("  ❌ Verificare eșuată — lipsesc variabile critice de mediu.")
        print("     Setează variabilele lipsă în .env și reîncearcă.\n")
        return False

    print("  ✅ Environment OK\n")
    return True


def apply_prefilter_gate(
    jobs: list[dict], config: dict, seen: SeenIndex
) -> tuple[list[dict], dict]:
    """Poarta ieftină dintre scrape și analyze.

    Elimină joburile deja văzute și pe cele sub pragul local, ca AI-ul să
    ruleze doar pe ce are șansă. Joburile respinse se memorează, altfel ar
    fi re-evaluate la fiecare rulare.
    """
    stats = {"total": len(jobs), "fara_cheie": 0, "deja_vazute": 0, "respinse": 0, "retinute": 0}

    # Joburile fără nicio cheie de identitate nu pot fi nici deduplicate,
    # nici urmărite — le numărăm separat, nu ca „deja văzute".
    keyed = [j for j in jobs if url_key(j) or company_title_key(j)]
    stats["fara_cheie"] = len(jobs) - len(keyed)

    fresh = seen.filter_new(keyed)
    stats["deja_vazute"] = len(keyed) - len(fresh)

    kept = []
    for job in fresh:
        score, reasons = prescore(job, config)
        if not passes_prefilter(job, config):
            seen.mark(job, RESPINS_PREFILTRU)
            stats["respinse"] += 1
            continue
        job["prescore"] = score
        # Motivele ajung în col „Keywords Match" — auditabil de ce a trecut.
        job["keywords_match"] = ", ".join(reasons)
        seen.mark(job, ANALIZAT)
        kept.append(job)

    stats["retinute"] = len(kept)
    return kept, stats


def run_pipeline(args):
    logger = setup_logger("Pipeline")
    config = load_config()
    dry_run = not args.live

    if not validate_environment(config, provider=args.provider):
        logger.error("Environment validation failed — pipeline oprit.")
        sys.exit(1)

    print_banner()
    print(f"  Mode:      {'🔴 LIVE' if args.live else '🟡 DRY RUN'}")
    print(f"  Step:      {args.step}")
    print(f"  Provider:  {args.provider or config.get('email', {}).get('active_provider', 'gmx')}")
    print(f"  Min Score: {args.min_score}")
    print(f"  Timestamp: {datetime.now():%Y-%m-%d %H:%M:%S}")
    print(f"{'─' * 54}")

    results = []
    pipeline_jobs = []

    # STEP 1: Scraping
    if args.step in ("scrape", "full"):
        print("\n📡 STEP 1: Scraping Joburi...")
        agent = ScraperAgent()
        result = agent.run()
        print_result(result)
        results.append(result)

        if result.status != AgentStatus.FAILED:
            pipeline_jobs = result.data.get("jobs", [])
            # Poarta ieftină: dedupe cu memorie + prefiltru local, ca analiza
            # AI să ruleze doar pe joburile cu șansă reală.
            seen = SeenIndex.load(PROJECT_ROOT / "data" / "seen_jobs.json")
            # Seed din Sheet: joburile deja aplicate nu trebuie redescoperite.
            # Sheet-ul e sursa de adevăr; indexul local e doar o oglindă.
            try:
                added = seen.seed_urls(N8nClient(config).get_known_urls())
                if added:
                    print(f"  Index: +{added} URL-uri din Sheet")
            except Exception as e:
                print(f"  ⚠ Seed din Sheet eșuat (continuăm cu indexul local): {e}")

            pipeline_jobs, gate_stats = apply_prefilter_gate(pipeline_jobs, config, seen)
            seen.save()

            # Persistăm lotul care a trecut poarta: pasul `analyze` rulat
            # separat citește ACEST fișier, nu jobs_scraped.json (brut) —
            # altfel AI-ul ar analiza nefiltrat exact ce a respins poarta.
            filtered_path = PROJECT_ROOT / "data" / "jobs_filtered.json"
            filtered_path.parent.mkdir(parents=True, exist_ok=True)
            with open(filtered_path, "w", encoding="utf-8") as f:
                json.dump(pipeline_jobs, f, indent=2, ensure_ascii=False, default=str)

            fara_cheie_suffix = (
                f" ({gate_stats['fara_cheie']} fără cheie)" if gate_stats["fara_cheie"] else ""
            )
            print(
                f"  Prefiltru: {gate_stats['total']} găsite{fara_cheie_suffix} → "
                f"{gate_stats['deja_vazute']} deja văzute, "
                f"{gate_stats['respinse']} sub prag → "
                f"{gate_stats['retinute']} spre analiză"
            )
            print("📊 Sync → Excel Tracker...")
            tracker = TrackerAgent()
            t_result = tracker.run(action="add_jobs", jobs=pipeline_jobs)
            print_result(t_result)
            results.append(t_result)

            # Actualizează pipeline_jobs cu _row setat de TrackerAgent
            if t_result.status != AgentStatus.FAILED:
                pipeline_jobs = t_result.data.get("jobs", pipeline_jobs)

    # STEP 2: Analiză
    if args.step in ("analyze", "full"):
        print("\n🔍 STEP 2: Analiză & Match Score...")

        # `--step analyze` standalone: preferăm lotul deja trecut prin poartă.
        # Fără asta, AnalyzerAgent._load_jobs() ar citi jobs_scraped.json
        # (brut, nefiltrat) și AI-ul ar plăti analiza pe ce a respins poarta.
        # NU re-trecem joburile prin poartă aici: ar fi toate „deja văzute".
        loaded_from_filtered = False
        if not pipeline_jobs:
            filtered_path = PROJECT_ROOT / "data" / "jobs_filtered.json"
            if filtered_path.exists():
                with open(filtered_path, "r", encoding="utf-8") as f:
                    pipeline_jobs = json.load(f)
                loaded_from_filtered = True
                print(f"  📂 {len(pipeline_jobs)} joburi din lotul filtrat (jobs_filtered.json)")

        agent = AnalyzerAgent()
        result = agent.run(
            jobs=pipeline_jobs if (pipeline_jobs or loaded_from_filtered) else None,
            use_ai=args.use_ai,
            min_score=args.min_score,
        )
        print_result(result)
        results.append(result)

        if result.status != AgentStatus.FAILED:
            pipeline_jobs = result.data.get("analyzed_jobs", [])
            print(f"  📈 {result.data.get('qualified', 0)} joburi cu score >= {args.min_score}")

    # STEP 3: Generare Anschreiben
    if args.step in ("generate", "full"):
        print("\n✍ STEP 3: Generare Anschreiben...")
        agent = CoverLetterAgent()
        result = agent.run(jobs=pipeline_jobs if pipeline_jobs else None)
        print_result(result)
        results.append(result)

        if result.status != AgentStatus.FAILED:
            pipeline_jobs = result.data.get("jobs_with_letters", pipeline_jobs)

    # STEP 4: Organizare Documente
    if args.step in ("organize", "full"):
        print("\n📁 STEP 4: Organizare Documente...")
        agent = DocumentAgent()
        result = agent.run(action="organize", source_dir=str(PROJECT_ROOT))
        print_result(result)
        results.append(result)

    # STEP 5: Verificare Documente
    if args.step in ("verify", "organize", "full"):
        print("\n✔ STEP 5: Verificare Documente...")
        agent = DocumentAgent()
        result = agent.run(action="verify")
        print_result(result)
        results.append(result)

    # STEP 6: Trimitere Emailuri
    if args.step in ("send", "full"):
        print(f"\n📧 STEP 6: Trimitere Emailuri {'(DRY RUN)' if dry_run else '(LIVE!)'}...")

        if not dry_run:
            print("  ⚠ LIVE MODE — Emailurile vor fi trimise efectiv!")
            confirm = input("  Continuă? (da/nein): ").strip().lower()
            if confirm not in ("da", "yes", "ja"):
                print("  ⏹ Anulat.")
                return results

        agent = EmailAgent()
        result = agent.run(
            jobs=pipeline_jobs if pipeline_jobs else None,
            dry_run=dry_run,
            provider=args.provider,
        )
        print_result(result)
        results.append(result)

        if result.status != AgentStatus.FAILED:
            print("📊 Actualizare Tracker...")
            tracker = TrackerAgent()
            t_result = tracker.run(action="update_status", jobs=pipeline_jobs)
            print_result(t_result)
            results.append(t_result)

    # STEP 6b: Sync rezultate la n8n / Google Sheets (dual-write, non-blocant)
    if args.step in ("scrape", "analyze", "generate", "send", "full") and pipeline_jobs:
        print("\n☁ STEP 6b: Sync → n8n / Google Sheets...")
        n8n = N8nSyncAgent()
        n8n_result = n8n.run(action="push", jobs=pipeline_jobs)
        print_result(n8n_result)
        results.append(n8n_result)

    # STEP 7: Raport
    if args.step in ("report", "full"):
        print("\n📊 STEP 7: Raport...")
        tracker = TrackerAgent()
        result = tracker.run(action="report")
        print_result(result)
        results.append(result)

    # SUMMARY
    print(f"\n{'═' * 54}")
    print(f"  PIPELINE SUMMARY")
    print(f"{'═' * 54}")
    for r in results:
        icons = {AgentStatus.SUCCESS: "✅", AgentStatus.PARTIAL: "⚠️", AgentStatus.FAILED: "❌"}
        icon = icons.get(r.status, "?")
        print(f"  {icon} {r.agent_name}: {r.message}")

    failed = sum(1 for r in results if r.status == AgentStatus.FAILED)
    if failed:
        print(f"\n  ⚠ {failed} agent(i) au eșuat")
    else:
        print(f"\n  ✅ Pipeline complet — toate stepurile OK")

    return results


def run_poll(args) -> list:
    """Trigger prin polling: cere comenzi SENDEN de la n8n, procesează, push status.

    Pași:
      1. N8nSyncAgent(pending) → comenzi (chei germane).
      2. Mapează în joburi interne; sare peste cele deja GESENDET.
      3. Generează Anschreiben lipsă + trimite email (dry-run/live).
      4. Push status înapoi la n8n (Sheet) + Excel rămâne backup via EmailAgent.
    """
    dry_run = not args.live
    results = []

    n8n = N8nSyncAgent()
    pending = n8n.run(action="pending")
    print_result(pending)
    results.append(pending)

    commands = pending.data.get("commands", [])
    # Filtrează defensiv joburile deja trimise. „Deja trimis" are mai multe
    # forme, pentru că două sisteme scriu col R „Email Status": Apps
    # Script→FastAPI scrie `sent`, iar `--step poll` scrie `GESENDET`.
    # Recunoașterea unei singure forme ducea la retrimiterea unei aplicații
    # deja făcute de celălalt sistem.
    already_sent = {"GESENDET", "SENT", "DRY_RUN"}
    jobs = [
        sheet_row_to_job(c)
        for c in commands
        if str(c.get("Email Status", "")).strip().upper() not in already_sent
    ]

    # Propagă limba (Sprache) acolo unde o citesc agenții (analysis.language)
    for job in jobs:
        analysis = job.setdefault("analysis", {})
        analysis.setdefault("language", job.get("language", "de"))

    if not jobs:
        print("  ⬜ Nicio comandă SENDEN de procesat.")
        return results

    print(f"  📥 {len(jobs)} comenzi SENDEN de procesat.")

    # Generează Anschreiben pentru joburile fără cover_letter.
    # Col O „Anschreiben PDF" conține un LINK Drive, nu textul scrisorii, deci
    # prezența ei NU înseamnă că avem un Anschreiben de trimis — regenerăm
    # pentru tot lotul. Rezultatul se împerechează pe „url", nu prin
    # înlocuirea listei: `jobs_with_letters` conține doar subsetul trimis
    # agentului, iar atribuirea directă ștergea restul joburilor din lot.
    cl = CoverLetterAgent()
    cl_res = cl.run(jobs=jobs)
    print_result(cl_res)
    results.append(cl_res)
    if cl_res.status != AgentStatus.FAILED:
        by_url = {j.get("url"): j for j in cl_res.data.get("jobs_with_letters", [])}
        jobs = [by_url.get(j.get("url"), j) for j in jobs]

    # Trimite emailuri
    em = EmailAgent()
    em_res = em.run(jobs=jobs, dry_run=dry_run, provider=args.provider)
    print_result(em_res)
    results.append(em_res)

    # Marchează status și push înapoi la n8n.
    # În dry-run NU scriem nimic în Sheet: altfel un tick de poll ar marca
    # rândul ca procesat fără să fi plecat vreun email.
    if dry_run:
        print("  ⬜ Dry-run — nu scriu înapoi în Sheet (folosește --live).")
        return results

    if em_res.status != AgentStatus.FAILED:
        sent_urls = {s.get("url") for s in em_res.data.get("sent", []) if s.get("url")}
        marked = 0
        for job in jobs:
            if job.get("url") not in sent_urls:
                continue  # email sărit (fără Kontakt Email) sau eșuat — nu marca
            # Col M „Status" aparține mașinii de stări a Apps Script
            # (plan→send→running→done/error) — nu o atingem din poll.
            job["email_status"] = "GESENDET"
            job["gesendet_am"] = datetime.now().strftime("%Y-%m-%d")
            job["sende_befehl"] = ""  # consumă trigger-ul din col P
            marked += 1
        if marked:
            push = n8n.run(action="push", jobs=jobs)
            print_result(push)
            results.append(push)
            if push.status != AgentStatus.SUCCESS:
                print("  ⚠ Push eșuat — col P/R NU au fost actualizate; "
                      "următorul poll VA RETRIMITE. Repară n8n înainte de următorul tick.")
        else:
            print("  ⬜ Niciun email trimis efectiv — nimic de marcat în Sheet.")

    return results


def main():
    parser = argparse.ArgumentParser(
        description="Job_app — Multi-Agent Bewerbung Pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--step",
        choices=["scrape", "analyze", "generate", "organize", "verify", "send", "report", "full", "poll"],
        default="full",
        help="Step de executat (default: full)",
    )
    parser.add_argument("--live", action="store_true", help="Trimite emailuri efectiv (implicit: dry-run)")
    parser.add_argument("--provider", choices=["gmx", "brevo"], default=None, help="SMTP provider")
    parser.add_argument("--min-score", type=int, default=30, help="Scor minim de match (default: 30)")
    parser.add_argument("--no-ai", action="store_true", help="Dezactivează AI (implicit: AI activ)")

    args = parser.parse_args()
    args.use_ai = not args.no_ai

    try:
        if args.step == "poll":
            run_poll(args)
        else:
            run_pipeline(args)
    except KeyboardInterrupt:
        print("\n\n⏹ Pipeline oprit de utilizator.")
        sys.exit(130)
    except Exception as e:
        print(f"\n❌ Eroare fatală: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
