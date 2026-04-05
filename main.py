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
from agents.cover_letter_agent import CoverLetterAgent
from agents.email_agent import EmailAgent
from agents.document_agent import DocumentAgent
from agents.tracker_agent import TrackerAgent


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
        agent = AnalyzerAgent()
        result = agent.run(
            jobs=pipeline_jobs if pipeline_jobs else None,
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


def main():
    parser = argparse.ArgumentParser(
        description="Job_app — Multi-Agent Bewerbung Pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--step",
        choices=["scrape", "analyze", "generate", "organize", "verify", "send", "report", "full"],
        default="full",
        help="Step de executat (default: full)",
    )
    parser.add_argument("--live", action="store_true", help="Trimite emailuri efectiv (implicit: dry-run)")
    parser.add_argument("--provider", choices=["gmx", "brevo"], default=None, help="SMTP provider")
    parser.add_argument("--min-score", type=int, default=30, help="Scor minim de match (default: 30)")
    parser.add_argument("--use-ai", action="store_true", default=True, help="Folosește AI pentru analiză")
    parser.add_argument("--no-ai", action="store_true", help="Dezactivează AI")

    args = parser.parse_args()
    if args.no_ai:
        args.use_ai = False

    try:
        run_pipeline(args)
    except KeyboardInterrupt:
        print("\n\n⏹ Pipeline oprit de utilizator.")
        sys.exit(130)
    except Exception as e:
        print(f"\n❌ Eroare fatală: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
