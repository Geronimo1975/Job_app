#!/usr/bin/env python3
"""
Test Pipeline End-to-End — Wempe Lead Integration Architect
============================================================
Test complet: Analiză → Anschreiben → Email → Tracker

Trimite la: cucuiet.george@gmail.com (test)
"""

import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.resolve()
sys.path.insert(0, str(PROJECT_ROOT))

from agents.base_agent import load_config, setup_logger, AgentStatus
from agents.analyzer_agent import AnalyzerAgent
from agents.cover_letter_agent import CoverLetterAgent
from agents.document_agent import DocumentAgent
from agents.email_agent import EmailAgent
from agents.tracker_agent import TrackerAgent


# ── Job Data (scraped manual de pe wempe-karriere.de) ───────
TEST_JOB = {
    "id": "wempe_j1100",
    "source": "career_page",
    "platform": "Wempe Karriere",
    "title": "Lead Integration Architect (m/w/d)",
    "company": "Gerhard D. Wempe GmbH & Co. KG",
    "location": "Hamburg",
    "url": "https://www.wempe-karriere.de/karriere/Lead-Integration-Architect-mwd-in-Hamburg-de-j1100.html",
    "email_hr": "cucuiet.george@gmail.com",  # TEST — trimitem la noi
    "contact_person": "Frau Negar Nazemian",
    "language": "de",
    "description": """Lead Integration Architect (m/w/d) — Gerhard D. Wempe GmbH & Co. KG, Hamburg

Aufgaben:
- Steuerung von Entwicklung und Betrieb zentraler Integrationskomponenten auf Basis der Salesforce Anypoint Plattform (MuleSoft)
- Mitgestaltung und Weiterentwicklung der Integrationsarchitektur
- Umsetzung fachlicher Anforderungen in technische Lösungen
- Code-Reviews durchführen; aktive Mitarbeit bei API-Design (RAML), DataWeave-Skripten und CI/CD-Pipelines (GitLab)
- Steuerung externer Implementierungspartner mit englischsprachigen Nearshore-Teams
- Fachliche Führung und Entwicklung eines interdisziplinären Teams

Anforderungen:
- Abschluss in IT-Bereich oder vergleichbare Berufserfahrung
- Kenntnisse in agiler Softwareentwicklung, Integrationslösungen, API-Management und CI/CD-Prozessen
- Sichere Code-Handhabung und technische Bewertungsfähigkeit
- Erfahrung mit externen Implementierungspartnern
- Fließend Deutsch und Englisch
- Führungserfahrung vorteilhaft, aber nicht erforderlich

Benefits: 37,5-Stunden-Woche, bis zu 2 Home-Office-Tage, 30 Tage Urlaub, Deutschlandticket
Kontakt: Negar Nazemian, +49 (0) 40 / 33 44 8 - 189""",
    "scraped_at": datetime.now().isoformat(),
    "applied": False,
    "status": "new",
}


def main():
    print("""
╔══════════════════════════════════════════════════════════╗
║   TEST PIPELINE END-TO-END — Wempe Integration Arch.    ║
║   Email Test → cucuiet.george@gmail.com                  ║
╚══════════════════════════════════════════════════════════╝
""")

    logger = setup_logger("TestPipeline")
    jobs = [TEST_JOB]

    # ═══════════════════════════════════════════════════════
    # STEP 1: Adaugă în Tracker
    # ═══════════════════════════════════════════════════════
    print("📊 STEP 1: Adăugare în Tracker...")
    tracker = TrackerAgent()
    result = tracker.run(action="add_jobs", jobs=jobs)
    print(f"  {result.summary()}\n")

    # ═══════════════════════════════════════════════════════
    # STEP 2: Analiză cu AI
    # ═══════════════════════════════════════════════════════
    print("🔍 STEP 2: Analiză & Match Score...")
    analyzer = AnalyzerAgent()
    result = analyzer.run(jobs=jobs, use_ai=True, min_score=0)
    print(f"  {result.summary()}")

    if result.data.get("analyzed_jobs"):
        job = result.data["analyzed_jobs"][0]
        analysis = job.get("analysis", {})
        print(f"  📈 Match Score: {analysis.get('match_score', '?')}/100")
        print(f"  ✅ Matching Skills: {', '.join(analysis.get('matching_skills', []))}")
        print(f"  ❌ Missing Skills: {', '.join(analysis.get('missing_skills', []))}")
        print(f"  📋 Key Requirements: {', '.join(analysis.get('key_requirements', []))}")
        print(f"  📝 Summary: {analysis.get('summary', '?')}")
        jobs = result.data["analyzed_jobs"]
    print()

    # ═══════════════════════════════════════════════════════
    # STEP 3: Generare Anschreiben
    # ═══════════════════════════════════════════════════════
    print("✍ STEP 3: Generare Anschreiben mit Claude AI...")
    cl_agent = CoverLetterAgent()
    result = cl_agent.run(jobs=jobs, force_regenerate=True)
    print(f"  {result.summary()}")

    if result.data.get("generated_files"):
        for f in result.data["generated_files"]:
            print(f"  📄 Gespeichert: {f}")
    print()

    # ═══════════════════════════════════════════════════════
    # STEP 4: Verificare Documente
    # ═══════════════════════════════════════════════════════
    print("✔ STEP 4: Dokument-Prüfung...")
    doc_agent = DocumentAgent()
    result = doc_agent.run(action="verify")
    print(f"  {result.summary()}")
    if result.data.get("checks"):
        for check in result.data["checks"]:
            icon = "✅" if check["exists"] else "❌"
            print(f"    {icon} {check['document']}")
    print()

    # ═══════════════════════════════════════════════════════
    # STEP 5: Trimitere Email (LIVE la gmail test)
    # ═══════════════════════════════════════════════════════
    print("📧 STEP 5: Email senden → cucuiet.george@gmail.com")
    print("  ⚠ LIVE — Email wird tatsächlich gesendet!\n")

    email_agent = EmailAgent()
    result = email_agent.run(
        jobs=jobs,
        dry_run=False,  # LIVE!
        provider="gmx",
    )
    print(f"  {result.summary()}")

    if result.data.get("sent"):
        for s in result.data["sent"]:
            print(f"  📧 {s['company']} → {s['email']} — {s['status']}")
    print()

    # ═══════════════════════════════════════════════════════
    # STEP 6: Raport
    # ═══════════════════════════════════════════════════════
    print("📊 STEP 6: Report...")
    tracker = TrackerAgent()
    result = tracker.run(action="report")
    print(f"  {result.summary()}")

    print(f"\n{'═' * 58}")
    print(f"  ✅ TEST PIPELINE COMPLET")
    print(f"  📧 Verifică inbox: cucuiet.george@gmail.com")
    print(f"{'═' * 58}\n")


if __name__ == "__main__":
    main()
