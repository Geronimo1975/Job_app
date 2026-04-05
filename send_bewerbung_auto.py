#!/usr/bin/env python3
"""
Bewerbung Auto-Sender — Citeste din Job_Application_Tracker.xlsx
================================================================
Trimite emailuri automat pentru toate joburile cu:
  - Kontakt Email completat
  - Email Status = BEREIT sau TEST TRIMIS
  - Sende Befehl = SENDEN

Workflow:
  1. Deschide Tracker → caută rânduri cu Sende Befehl = "SENDEN"
  2. Construiește email din coloanele: Subject, Body, Attachments
  3. Trimite prin GMX SMTP (sau Brevo SMTP)
  4. Actualizează tracker: Email Status → GESENDET, Sende Befehl → GESENDET

Utilizare:
    python3 send_bewerbung_auto.py                  # procesează tracker
    python3 send_bewerbung_auto.py --dry-run         # preview fără trimitere
    python3 send_bewerbung_auto.py --smtp brevo      # folosește Brevo SMTP
    python3 send_bewerbung_auto.py --test            # trimite test email

Setup GMX:
    1. Activează SMTP: GMX → Einstellungen → POP3/IMAP → erlauben
    2. export GMX_PASSWORD="your-password"

Setup Brevo (alternativ):
    1. Brevo → Settings → SMTP & API → Generate SMTP Key
    2. Verifică sender: george.cucuiet@gmx.de
    3. export BREVO_SMTP_KEY="xkeysib-..."
"""

import smtplib
import os
import sys
import argparse
import getpass
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from pathlib import Path
from datetime import datetime

try:
    import openpyxl
except ImportError:
    print("❌ openpyxl lipsește. Instalează: pip install openpyxl")
    sys.exit(1)

# ── Config ──────────────────────────────────────────────────────────
SENDER_EMAIL = "george.cucuiet@gmx.de"
SENDER_NAME = "George Sebastian Cucuiet"

SMTP_CONFIGS = {
    "gmx": {
        "server": "mail.gmx.net",
        "port": 587,
        "login": SENDER_EMAIL,
        "env_key": "GMX_PASSWORD",
    },
    "brevo": {
        "server": "smtp-relay.brevo.com",
        "port": 587,
        "login": SENDER_EMAIL,
        "env_key": "BREVO_SMTP_KEY",
    },
}

SCRIPT_DIR = Path(__file__).parent.resolve()
TRACKER_FILE = SCRIPT_DIR / "data" / "Job_Application_Tracker.xlsx"
DOCS_FOLDER = SCRIPT_DIR / "documents"

# ── Column mapping (1-indexed) ──────────────────────────────────────
COL = {
    "nr": 1,
    "firma": 2,
    "position": 3,
    "kontakt_email": 7,
    "kontakt_person": 8,
    "status": 11,
    "anschreiben_name": 12,
    "email_inhalt": 15,
    "email_status": 17,
    "anschreiben_pdf": 18,
    "cv_pdf": 19,
    "zeugnisse": 20,
    "zertifikate": 21,
    "komplett_pdf": 22,
    "sende_befehl": 23,
}


def get_cell(ws, row, col_name):
    val = ws.cell(row=row, column=COL[col_name]).value
    return str(val).strip() if val else ""


def resolve_attachments(row_data):
    """Rezolvă fișierele de atașat din coloanele tracker-ului."""
    files = []

    # Subdirectoare unde căutăm fișierele
    search_dirs = [
        DOCS_FOLDER / "cover_letters" / "generated",
        DOCS_FOLDER / "cv",
        DOCS_FOLDER / "certificates",
        DOCS_FOLDER / "references",
        DOCS_FOLDER,
        SCRIPT_DIR,
    ]

    def find_file(filename):
        for d in search_dirs:
            path = d / filename
            if path.exists():
                return path
        return None

    # Dacă există Komplett PDF → doar acela
    komplett = row_data.get("komplett_pdf", "")
    if komplett:
        path = find_file(komplett)
        if path:
            return [path]

    # Altfel: Anschreiben + CV + Zeugnisse + Zertifikate
    for key in ["anschreiben_pdf", "cv_pdf", "zeugnisse", "zertifikate"]:
        val = row_data.get(key, "")
        if not val:
            continue
        # Suportă multiple fișiere separate prin ";"
        for filename in val.split(";"):
            filename = filename.strip()
            if not filename:
                continue
            path = find_file(filename)
            if path:
                files.append(path)
            else:
                print(f"    ⚠️  Fișier lipsă: {filename}")
    return files


def build_subject(firma, position, kontakt_person):
    """Construiește subject line."""
    return f"Bewerbung als {position} — {SENDER_NAME}"


def build_body(firma, position, kontakt_person, email_inhalt):
    """Construiește body-ul emailului."""
    if email_inhalt and email_inhalt != "N/A" and "Apply via" not in email_inhalt:
        # Dacă email_inhalt conține deja textul complet (mai mult de 50 char)
        if len(email_inhalt) > 100:
            return email_inhalt

    # Generează body standard
    anrede = f"Sehr geehrte/r {kontakt_person}" if kontakt_person else "Sehr geehrte Damen und Herren"

    return f"""{anrede},

mit großem Interesse bewerbe ich mich auf die ausgeschriebene Position als {position} bei {firma}.

Als Python- und AI-Automation-Entwickler mit Erfahrung in Django, LLM-APIs (OpenAI, Claude, Gemini), und Workflow-Automatisierung (n8n, Make.com) bringe ich die technische Expertise mit, die für diese Rolle erforderlich ist.

Meine Bewerbungsunterlagen finden Sie im Anhang.

Ich freue mich auf Ihre Rückmeldung und stehe gerne für ein persönliches Gespräch zur Verfügung.

Mit freundlichen Grüßen,
{SENDER_NAME}

Tel: +49 160 415 5546
E-Mail: {SENDER_EMAIL}
LinkedIn: linkedin.com/in/george-sebastian-c-212b7455
GitHub: github.com/Geronimo1975"""


def create_email(row_data):
    """Creează emailul MIME complet."""
    msg = MIMEMultipart()
    msg["From"] = f"{SENDER_NAME} <{SENDER_EMAIL}>"
    msg["To"] = row_data["kontakt_email"]
    msg["Subject"] = build_subject(
        row_data["firma"], row_data["position"], row_data["kontakt_person"]
    )
    msg["Reply-To"] = SENDER_EMAIL

    body = build_body(
        row_data["firma"],
        row_data["position"],
        row_data["kontakt_person"],
        row_data.get("email_inhalt", ""),
    )
    msg.attach(MIMEText(body, "plain", "utf-8"))

    # Attachments
    attachments = resolve_attachments(row_data)
    for filepath in attachments:
        with open(filepath, "rb") as f:
            att = MIMEBase("application", "pdf")
            att.set_payload(f.read())
            encoders.encode_base64(att)
            att.add_header("Content-Disposition", "attachment", filename=filepath.name)
            msg.attach(att)
            size_kb = filepath.stat().st_size / 1024
            print(f"    📎 {filepath.name} ({size_kb:.0f} KB)")

    return msg


def send_smtp(msg, smtp_type="gmx"):
    """Trimite email prin SMTP."""
    cfg = SMTP_CONFIGS[smtp_type]
    password = os.environ.get(cfg["env_key"])

    if not password:
        password = getpass.getpass(f"\n🔐 Parola {smtp_type.upper()} pentru {cfg['login']}: ")

    server = smtplib.SMTP(cfg["server"], cfg["port"], timeout=30)
    server.ehlo()
    server.starttls()
    server.ehlo()
    server.login(cfg["login"], password)
    server.sendmail(SENDER_EMAIL, [msg["To"]], msg.as_string())
    server.quit()
    return True


def process_tracker(dry_run=False, smtp_type="gmx"):
    """Procesează tracker-ul și trimite emailuri."""
    if not TRACKER_FILE.exists():
        print(f"❌ Tracker nu a fost găsit: {TRACKER_FILE}")
        return False

    wb = openpyxl.load_workbook(TRACKER_FILE)
    ws = wb.active
    jobs_to_send = []

    print(f"\n📊 Scanare tracker: {TRACKER_FILE.name}")
    print(f"   Rânduri: {ws.max_row - 1} joburi\n")

    for row in range(2, ws.max_row + 1):
        sende = get_cell(ws, row, "sende_befehl")
        email = get_cell(ws, row, "kontakt_email")
        firma = get_cell(ws, row, "firma")

        if sende.upper() == "SENDEN" and email:
            row_data = {key: get_cell(ws, row, key) for key in COL}
            row_data["_row"] = row
            jobs_to_send.append(row_data)
            print(f"  ✅ Row {row}: {firma} → {email}")
        elif sende.upper() == "SENDEN" and not email:
            print(f"  ⚠️  Row {row}: {firma} — SENDEN aber kein Email!")
        else:
            status_icon = "📧" if email else "—"
            print(f"  {status_icon} Row {row}: {firma} — Sende={sende or 'NEIN'}")

    if not jobs_to_send:
        print("\n📭 Keine Emails zum Senden.")
        print("   Setze 'Sende Befehl' auf 'SENDEN' im Tracker.")
        return True

    print(f"\n{'=' * 60}")
    print(f"  {len(jobs_to_send)} EMAIL(S) ZU SENDEN")
    print(f"{'=' * 60}")

    results = []
    for job in jobs_to_send:
        row = job["_row"]
        firma = job["firma"]
        to_email = job["kontakt_email"]

        print(f"\n{'─' * 50}")
        print(f"  📋 {firma} — {job['position']}")
        print(f"  📧 An: {to_email}")

        try:
            msg = create_email(job)

            if dry_run:
                print(f"  🔍 DRY RUN — Email NICHT gesendet")
                results.append((firma, True, "DRY RUN"))
                continue

            send_smtp(msg, smtp_type)
            print(f"  ✅ GESENDET!")

            # Update tracker
            from openpyxl.styles import PatternFill, Font
            ws.cell(row=row, column=COL["email_status"]).value = "GESENDET"
            ws.cell(row=row, column=COL["email_status"]).fill = PatternFill('solid', fgColor='4CAF50')
            ws.cell(row=row, column=COL["email_status"]).font = Font(name='Arial', size=9, bold=True, color='FFFFFF')
            ws.cell(row=row, column=COL["sende_befehl"]).value = "GESENDET"
            ws.cell(row=row, column=COL["sende_befehl"]).fill = PatternFill('solid', fgColor='4CAF50')
            ws.cell(row=row, column=COL["sende_befehl"]).font = Font(name='Arial', size=10, bold=True, color='FFFFFF')
            ws.cell(row=row, column=COL["status"]).value = "Email gesendet"

            results.append((firma, True, "GESENDET"))

        except smtplib.SMTPAuthenticationError:
            print(f"  ❌ Autentificare eșuată!")
            ws.cell(row=row, column=COL["email_status"]).value = "FEHLER - Auth"
            results.append((firma, False, "AUTH ERROR"))
        except Exception as e:
            print(f"  ❌ Eroare: {e}")
            ws.cell(row=row, column=COL["email_status"]).value = f"FEHLER"
            results.append((firma, False, str(e)[:30]))

    # Save tracker
    if not dry_run:
        wb.save(TRACKER_FILE)
        print(f"\n💾 Tracker actualizat: {TRACKER_FILE.name}")

    # Log
    log_file = SCRIPT_DIR / "output" / "logs" / "email_send_log.txt"
    with open(log_file, "a", encoding="utf-8") as f:
        for firma, success, detail in results:
            status = "OK" if success else "FAIL"
            f.write(f"[{datetime.now().isoformat()}] {status} — {firma} — {detail}\n")

    # Summary
    print(f"\n{'=' * 60}")
    print(f"  ZUSAMMENFASSUNG")
    print(f"{'=' * 60}")
    for firma, success, detail in results:
        icon = "✅" if success else "❌"
        print(f"  {icon} {firma} — {detail}")

    return all(s for _, s, _ in results)


def send_test(smtp_type="gmx"):
    """Trimite un email de test."""
    msg = MIMEMultipart()
    msg["From"] = f"{SENDER_NAME} <{SENDER_EMAIL}>"
    msg["To"] = "cucuiet.george@gmail.com"
    msg["Subject"] = "Test Bewerbung Auto-Sender"
    msg.attach(MIMEText(
        f"Test email trimis prin {smtp_type.upper()} SMTP.\n\n"
        f"Timestamp: {datetime.now().isoformat()}\n"
        f"Dacă primești acest email, sistemul funcționează corect.",
        "plain", "utf-8"
    ))

    # Attach CV as test
    cv = DOCS_FOLDER / "cv" / "Resume George Sebastian Cucuiet.pdf"
    if cv.exists():
        with open(cv, "rb") as f:
            att = MIMEBase("application", "pdf")
            att.set_payload(f.read())
            encoders.encode_base64(att)
            att.add_header("Content-Disposition", "attachment", filename=cv.name)
            msg.attach(att)
            print(f"  📎 {cv.name} ({cv.stat().st_size / 1024:.0f} KB)")

    try:
        send_smtp(msg, smtp_type)
        print(f"\n✅ Test email trimis la cucuiet.george@gmail.com")
        return True
    except Exception as e:
        print(f"\n❌ Eroare: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(description="Bewerbung Auto-Sender")
    parser.add_argument("--dry-run", action="store_true", help="Preview fără trimitere")
    parser.add_argument("--smtp", choices=["gmx", "brevo"], default="gmx", help="SMTP provider")
    parser.add_argument("--test", action="store_true", help="Trimite test email")
    args = parser.parse_args()

    print("=" * 60)
    print("  BEWERBUNG AUTO-SENDER")
    print(f"  SMTP: {args.smtp.upper()} | From: {SENDER_EMAIL}")
    print(f"  Docs: {DOCS_FOLDER}")
    print("=" * 60)

    if not DOCS_FOLDER.exists():
        print(f"\n❌ Folder lipsă: {DOCS_FOLDER}")
        print("   Creează folderul și adaugă documentele.")
        sys.exit(1)

    if args.test:
        success = send_test(args.smtp)
        sys.exit(0 if success else 1)

    success = process_tracker(dry_run=args.dry_run, smtp_type=args.smtp)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
