#!/usr/bin/env python3
"""
Bewerbung Email Sender via Brevo (formerly Sendinblue) SMTP
============================================================
Trimite emailuri de aplicare cu atașamente PDF de pe george.cucuiet@gmx.de

Setup:
    1. Loghează-te în Brevo → Settings → SMTP & API → Generate SMTP Key
    2. Verifică sender: george.cucuiet@gmx.de în Brevo → Senders
    3. Setează variabila de mediu: export BREVO_SMTP_KEY="xkeysib-..."

Utilizare:
    python3 send_bewerbung_brevo.py                    # trimite qnips (default)
    python3 send_bewerbung_brevo.py --job qnips        # trimite specific job
    python3 send_bewerbung_brevo.py --job all           # trimite toate cu email
    python3 send_bewerbung_brevo.py --dry-run           # preview fără trimitere
    python3 send_bewerbung_brevo.py --test              # trimite test către tine
"""

import smtplib
import os
import sys
import argparse
import mimetypes
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from pathlib import Path
from datetime import datetime

# ── Config ──────────────────────────────────────────────────────────
BREVO_SMTP_SERVER = "smtp-relay.brevo.com"
BREVO_SMTP_PORT = 587
BREVO_SMTP_LOGIN = "george.cucuiet@gmx.de"  # Brevo account email
SENDER_EMAIL = "george.cucuiet@gmx.de"
SENDER_NAME = "George Sebastian Cucuiet"

# Directorul cu fișierele de aplicare
SCRIPT_DIR = Path(__file__).parent.resolve()

# ── Job Definitions ─────────────────────────────────────────────────
JOBS = {
    "qnips": {
        "to_email": "jobs@qnips.io",
        "to_name": "Frau Harrendorf",
        "subject": "Bewerbung als Data & AI Product Engineer (m/w/d) — George Sebastian Cucuiet",
        "body": """Sehr geehrte Frau Harrendorf,

mit großem Interesse bewerbe ich mich auf die ausgeschriebene Position als Data & AI Product Engineer (m/w/d) bei qnips.

Als Python- und AI-Automation-Entwickler mit Erfahrung im Aufbau datengetriebener SaaS-Features bringe ich genau die Kombination aus technischem Produktdenken und KI-Expertise mit, die Sie suchen. In meinen aktuellen Projekten arbeite ich täglich mit LLM-APIs (OpenAI, Claude, Gemini), baue API-Integrationen und automatisierte Datenpipelines mit Python, Django und n8n.

Meine Bewerbungsunterlagen (Anschreiben, Lebenslauf, Zeugnisse und Zertifikate) finden Sie im beigefügten PDF.

Ich freue mich auf Ihre Rückmeldung und stehe gerne für ein persönliches Gespräch zur Verfügung.

Mit freundlichen Grüßen,
George Sebastian Cucuiet

Tel: +49 160 415 5546
E-Mail: george.cucuiet@gmx.de
LinkedIn: linkedin.com/in/george-sebastian-c-212b7455
GitHub: github.com/Geronimo1975""",
        "attachments": [
            "Bewerbung_Komplett_qnips_George_Cucuiet.pdf",
        ],
    },
    # ── Adaugă aici alte joburi cu email direct ──
    # "firma_xyz": {
    #     "to_email": "hr@firma.de",
    #     "to_name": "Sehr geehrte Damen und Herren",
    #     "subject": "Bewerbung als ... — George Sebastian Cucuiet",
    #     "body": "...",
    #     "attachments": ["Anschreiben_....pdf", "Resume George Sebastian Cucuiet.pdf"],
    # },
}


def create_email(job_key: str) -> MIMEMultipart:
    """Creează emailul MIME cu atașamente."""
    job = JOBS[job_key]

    msg = MIMEMultipart()
    msg["From"] = f"{SENDER_NAME} <{SENDER_EMAIL}>"
    msg["To"] = job["to_email"]
    msg["Subject"] = job["subject"]
    msg["Reply-To"] = SENDER_EMAIL

    # Body text
    msg.attach(MIMEText(job["body"], "plain", "utf-8"))

    # Atașamente
    for filename in job.get("attachments", []):
        # Caută în subdirectoarele documents/
        docs_dir = SCRIPT_DIR / "documents"
        search_dirs = [
            docs_dir / "cover_letters" / "generated",
            docs_dir / "cv",
            docs_dir / "certificates",
            docs_dir / "references",
            docs_dir,
            SCRIPT_DIR,
        ]
        filepath = None
        for d in search_dirs:
            candidate = d / filename
            if candidate.exists():
                filepath = candidate
                break
        if filepath is None:
            filepath = SCRIPT_DIR / filename  # fallback
        if not filepath.exists():
            print(f"  ⚠️  Atașament lipsă: {filename}")
            continue

        ctype, encoding = mimetypes.guess_type(str(filepath))
        if ctype is None:
            ctype = "application/octet-stream"
        maintype, subtype = ctype.split("/", 1)

        with open(filepath, "rb") as f:
            attachment = MIMEBase(maintype, subtype)
            attachment.set_payload(f.read())
            encoders.encode_base64(attachment)
            attachment.add_header(
                "Content-Disposition",
                "attachment",
                filename=filename,
            )
            msg.attach(attachment)
            size_mb = filepath.stat().st_size / (1024 * 1024)
            print(f"  📎 {filename} ({size_mb:.1f} MB)")

    return msg


def send_email(msg: MIMEMultipart, dry_run: bool = False) -> bool:
    """Trimite emailul prin Brevo SMTP."""
    smtp_key = os.environ.get("BREVO_SMTP_KEY")
    if not smtp_key:
        print("\n❌ BREVO_SMTP_KEY nu este setat!")
        print("   Pași de configurare:")
        print("   1. Loghează-te în https://app.brevo.com")
        print("   2. Settings → SMTP & API → SMTP tab")
        print("   3. Generează un nou SMTP Key")
        print("   4. export BREVO_SMTP_KEY='xkeysib-...'")
        return False

    to_addr = msg["To"]
    subject = msg["Subject"]

    if dry_run:
        print(f"\n🔍 DRY RUN — Email NU a fost trimis")
        print(f"   To:      {to_addr}")
        print(f"   From:    {msg['From']}")
        print(f"   Subject: {subject}")
        print(f"   Body:    {len(msg.get_payload()[0].get_payload())} caractere")
        return True

    try:
        print(f"\n📤 Conectare la Brevo SMTP...")
        server = smtplib.SMTP(BREVO_SMTP_SERVER, BREVO_SMTP_PORT, timeout=30)
        server.ehlo()
        server.starttls()
        server.ehlo()
        server.login(BREVO_SMTP_LOGIN, smtp_key)

        print(f"📧 Trimitere către {to_addr}...")
        server.sendmail(SENDER_EMAIL, [to_addr], msg.as_string())
        server.quit()

        print(f"✅ Email trimis cu succes către {to_addr}")
        print(f"   Subject: {subject}")

        # Log trimiterea
        log_file = SCRIPT_DIR / "output" / "logs" / "email_send_log.txt"
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(f"[{datetime.now().isoformat()}] SENT to={to_addr} subject={subject}\n")

        return True

    except smtplib.SMTPAuthenticationError:
        print("❌ Autentificare eșuată! Verifică BREVO_SMTP_KEY")
        return False
    except smtplib.SMTPRecipientsRefused:
        print(f"❌ Adresa {to_addr} a fost refuzată de server")
        return False
    except Exception as e:
        print(f"❌ Eroare la trimitere: {e}")
        return False


def send_test():
    """Trimite un email de test către tine."""
    msg = MIMEMultipart()
    msg["From"] = f"{SENDER_NAME} <{SENDER_EMAIL}>"
    msg["To"] = SENDER_EMAIL
    msg["Subject"] = "Test Bewerbung Email — Brevo SMTP"
    msg.attach(MIMEText(
        "Acesta este un email de test trimis prin Brevo SMTP.\n\n"
        "Dacă primești acest email, configurarea funcționează corect.\n\n"
        f"Timestamp: {datetime.now().isoformat()}",
        "plain", "utf-8"
    ))
    return send_email(msg)


def main():
    parser = argparse.ArgumentParser(
        description="Trimite emailuri de aplicare prin Brevo SMTP"
    )
    parser.add_argument(
        "--job", default="qnips",
        help="Job key (qnips, all) sau 'all' pentru toate cu email"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Preview email fără trimitere"
    )
    parser.add_argument(
        "--test", action="store_true",
        help="Trimite email de test către tine"
    )
    args = parser.parse_args()

    print("=" * 60)
    print("  BEWERBUNG EMAIL SENDER — Brevo SMTP")
    print("=" * 60)

    if args.test:
        print("\n🧪 Trimitere email de test...")
        success = send_test()
        sys.exit(0 if success else 1)

    if args.job == "all":
        jobs_to_send = list(JOBS.keys())
    else:
        if args.job not in JOBS:
            print(f"\n❌ Job '{args.job}' nu există. Disponibile: {', '.join(JOBS.keys())}")
            sys.exit(1)
        jobs_to_send = [args.job]

    results = []
    for job_key in jobs_to_send:
        job = JOBS[job_key]
        print(f"\n{'─' * 50}")
        print(f"📋 Job: {job_key}")
        print(f"   To: {job['to_email']}")
        print(f"   Subject: {job['subject']}")

        msg = create_email(job_key)
        success = send_email(msg, dry_run=args.dry_run)
        results.append((job_key, success))

    # Summary
    print(f"\n{'=' * 60}")
    print("  SUMMARY")
    print(f"{'=' * 60}")
    for job_key, success in results:
        status = "✅ TRIMIS" if success else "❌ EȘUAT"
        print(f"  {status} — {job_key} → {JOBS[job_key]['to_email']}")

    failed = sum(1 for _, s in results if not s)
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
