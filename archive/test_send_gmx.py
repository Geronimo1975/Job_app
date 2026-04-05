#!/usr/bin/env python3
"""
Test: Trimite CV de pe george.cucuiet@gmx.de la cucuiet.george@gmail.com
via GMX SMTP direct (fără Brevo!)

Utilizare:
    1. Activează SMTP în GMX:
       GMX → Einstellungen → POP3/IMAP → "Zugang via POP3 und IMAP erlauben" ✅
    2. Rulează:
       python3 test_send_gmx.py
    3. Introdu parola GMX când ți se cere (sau setează: export GMX_PASSWORD="...")
"""

import smtplib
import os
import sys
import getpass
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from pathlib import Path

# ── Config ──────────────────────────────────────────────────────────
GMX_SMTP_SERVER = "mail.gmx.net"
GMX_SMTP_PORT = 587
SENDER_EMAIL = "george.cucuiet@gmx.de"
SENDER_NAME = "George Sebastian Cucuiet"
TO_EMAIL = "cucuiet.george@gmail.com"

# Directorul cu fișierele
SCRIPT_DIR = Path(__file__).parent.resolve()
CV_FILE = os.path.join("documents", "cv", "Resume George Sebastian Cucuiet.pdf")

def main():
    print("=" * 60)
    print("  TEST EMAIL — GMX SMTP Direct")
    print("=" * 60)
    print(f"  From: {SENDER_EMAIL}")
    print(f"  To:   {TO_EMAIL}")
    print(f"  CV:   {CV_FILE}")
    print("=" * 60)

    # Password
    password = os.environ.get("GMX_PASSWORD")
    if not password:
        password = getpass.getpass(f"\nParola GMX pentru {SENDER_EMAIL}: ")

    # Check CV exists
    cv_path = SCRIPT_DIR / CV_FILE
    if not cv_path.exists():
        print(f"\n❌ CV nu a fost găsit: {cv_path}")
        sys.exit(1)

    # Build email
    msg = MIMEMultipart()
    msg["From"] = f"{SENDER_NAME} <{SENDER_EMAIL}>"
    msg["To"] = TO_EMAIL
    msg["Subject"] = "Test — CV George Sebastian Cucuiet"

    body = """Hallo,

dies ist eine Test-E-Mail, gesendet über GMX SMTP.

Im Anhang finden Sie meinen Lebenslauf.

Mit freundlichen Grüßen,
George Sebastian Cucuiet

Tel: +49 160 415 5546
E-Mail: george.cucuiet@gmx.de"""

    msg.attach(MIMEText(body, "plain", "utf-8"))

    # Attach CV
    with open(cv_path, "rb") as f:
        attachment = MIMEBase("application", "pdf")
        attachment.set_payload(f.read())
        encoders.encode_base64(attachment)
        attachment.add_header(
            "Content-Disposition",
            "attachment",
            filename=cv_path.name,
        )
        msg.attach(attachment)
        size_kb = cv_path.stat().st_size / 1024
        print(f"\n📎 Atașat: {CV_FILE} ({size_kb:.0f} KB)")

    # Send
    try:
        print(f"\n📤 Conectare la GMX SMTP ({GMX_SMTP_SERVER}:{GMX_SMTP_PORT})...")
        server = smtplib.SMTP(GMX_SMTP_SERVER, GMX_SMTP_PORT, timeout=30)
        server.ehlo()
        server.starttls()
        server.ehlo()
        print("🔐 Autentificare...")
        server.login(SENDER_EMAIL, password)
        print(f"📧 Trimitere către {TO_EMAIL}...")
        server.sendmail(SENDER_EMAIL, [TO_EMAIL], msg.as_string())
        server.quit()
        print(f"\n✅ Email trimis cu succes!")
        print(f"   Verifică inbox-ul: {TO_EMAIL}")

    except smtplib.SMTPAuthenticationError:
        print("\n❌ Autentificare eșuată!")
        print("   Verifică:")
        print("   1. Parola GMX este corectă")
        print("   2. SMTP este activat în GMX:")
        print("      GMX → Einstellungen → POP3/IMAP")
        print("      → 'Zugang via POP3 und IMAP erlauben' = aktiviert")
    except Exception as e:
        print(f"\n❌ Eroare: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
