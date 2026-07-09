"""
SMTP Sender — Trimitere emailuri prin GMX sau Brevo SMTP.

Suportă:
  - Trimitere cu atașamente (PDF-uri)
  - Rate limiting (max N emailuri/zi)
  - Dry run mode
  - Logging complet
"""

import fcntl
import getpass
import json
import os
import smtplib
import time
from datetime import datetime
from email import encoders
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path


class SmtpSender:
    """Sender de emailuri prin SMTP."""

    def __init__(self, config: dict, project_root: Path):
        self.config = config
        self.project_root = project_root
        self.email_config = config.get("email", {})
        self.applicant = config.get("applicant", {})
        self._password_cache: dict[str, str] = {}
        self._rate_state_file = project_root / "output" / "logs" / "email_rate_state.json"
        self._rate_lock_file = project_root / "output" / "logs" / "email_rate_state.lock"
        self._sent_today = self._load_sent_today()

    def _load_sent_today(self) -> int:
        """Citește contorul de azi din fișierul de stare (persistă peste procese noi)."""
        if not self._rate_state_file.exists():
            return 0
        try:
            state = json.loads(self._rate_state_file.read_text())
        except (json.JSONDecodeError, OSError):
            return 0
        today = datetime.now().strftime("%Y-%m-%d")
        return state.get("count", 0) if state.get("date") == today else 0

    def _save_sent_today(self) -> None:
        """Scrie contorul curent pe disc, ca următorul proces (ex. launchd poll) să-l vadă."""
        self._rate_state_file.parent.mkdir(parents=True, exist_ok=True)
        self._rate_state_file.write_text(json.dumps({
            "date": datetime.now().strftime("%Y-%m-%d"),
            "count": self._sent_today,
        }))

    @property
    def active_provider(self) -> str:
        return self.email_config.get("active_provider", "gmx")

    @property
    def max_per_day(self) -> int:
        return self.email_config.get("max_emails_per_day", 10)

    @property
    def delay_sec(self) -> int:
        return self.email_config.get("delay_between_emails_sec", 30)

    def _get_smtp_config(self, provider: str | None = None) -> dict:
        """Returnează configurația SMTP pentru provider."""
        provider = provider or self.active_provider
        providers = self.email_config.get("providers", {})
        if provider not in providers:
            raise ValueError(f"SMTP provider necunoscut: {provider}")
        return providers[provider]

    def _get_password(self, provider: str | None = None) -> str:
        """Obține parola din env var sau prompt."""
        provider = provider or self.active_provider
        if provider in self._password_cache:
            return self._password_cache[provider]

        cfg = self._get_smtp_config(provider)
        env_key = cfg.get("env_password_key", "")
        password = os.environ.get(env_key, "")

        if not password:
            password = getpass.getpass(
                f"Parola {provider.upper()} pentru {cfg['login']}: "
            )

        self._password_cache[provider] = password
        return password

    def build_email(
        self,
        to_email: str,
        subject: str,
        body: str,
        attachments: list[Path] | None = None,
        reply_to: str | None = None,
    ) -> MIMEMultipart:
        """
        Construiește un email MIME complet.

        Args:
            to_email: Adresa destinatar
            subject: Subject line
            body: Textul emailului
            attachments: Lista de fișiere de atașat
            reply_to: Reply-To header (opțional)

        Returns:
            MIMEMultipart message object.
        """
        sender_name = self.applicant.get("name", "")
        sender_email = self.applicant.get("email", "")

        msg = MIMEMultipart()
        msg["From"] = f"{sender_name} <{sender_email}>"
        msg["To"] = to_email
        msg["Subject"] = subject
        if reply_to:
            msg["Reply-To"] = reply_to

        msg.attach(MIMEText(body, "plain", "utf-8"))

        # Atașamente
        for filepath in (attachments or []):
            if not filepath.exists():
                print(f"  ⚠ Fișier lipsă, skip: {filepath}")
                continue

            with open(filepath, "rb") as f:
                # Detectează MIME type
                suffix = filepath.suffix.lower()
                if suffix == ".pdf":
                    mime_main, mime_sub = "application", "pdf"
                elif suffix in (".jpg", ".jpeg"):
                    mime_main, mime_sub = "image", "jpeg"
                elif suffix == ".png":
                    mime_main, mime_sub = "image", "png"
                else:
                    mime_main, mime_sub = "application", "octet-stream"

                att = MIMEBase(mime_main, mime_sub)
                att.set_payload(f.read())
                encoders.encode_base64(att)
                att.add_header(
                    "Content-Disposition", "attachment", filename=filepath.name
                )
                msg.attach(att)

        return msg

    def send(
        self,
        msg: MIMEMultipart,
        provider: str | None = None,
        dry_run: bool = False,
    ) -> bool:
        """
        Trimite un email prin SMTP.

        Args:
            msg: Email MIME complet
            provider: SMTP provider (opțional, default din config)
            dry_run: Dacă True, nu trimite efectiv

        Returns:
            True dacă a reușit.
        """
        provider = provider or self.active_provider

        # Rate limiting check
        if self._sent_today >= self.max_per_day:
            raise RuntimeError(
                f"Rate limit atins: {self._sent_today}/{self.max_per_day} emailuri azi"
            )

        if dry_run:
            print(f"  🔍 DRY RUN — Email către {msg['To']} NU a fost trimis")
            return True

        cfg = self._get_smtp_config(provider)
        password = self._get_password(provider)

        # Blocare exclusivă pe fișier: evită lost-update dacă rulează două
        # procese aproape simultan (ex. poll launchd suprapus cu --live manual).
        self._rate_lock_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self._rate_lock_file, "a") as lockf:
            fcntl.flock(lockf, fcntl.LOCK_EX)
            try:
                # Re-verifică sub lock, cu valoarea proaspătă de pe disc —
                # self._sent_today încărcat la construcție poate fi stale.
                self._sent_today = self._load_sent_today()
                if self._sent_today >= self.max_per_day:
                    raise RuntimeError(
                        f"Rate limit atins: {self._sent_today}/{self.max_per_day} emailuri azi"
                    )

                server = smtplib.SMTP(cfg["server"], cfg["port"], timeout=30)
                try:
                    server.ehlo()
                    server.starttls()
                    server.ehlo()
                    server.login(cfg["login"], password)
                    server.sendmail(
                        self.applicant.get("email", cfg["login"]),
                        [msg["To"]],
                        msg.as_string(),
                    )
                    self._sent_today += 1
                    self._save_sent_today()
                finally:
                    try:
                        server.quit()
                    except Exception:
                        pass
            finally:
                fcntl.flock(lockf, fcntl.LOCK_UN)

        # Delay între emailuri
        if self.delay_sec > 0:
            time.sleep(self.delay_sec)

        return True

    def send_simple(
        self,
        to_email: str,
        subject: str,
        body: str,
        attachments: list[Path] | None = None,
        dry_run: bool = False,
    ) -> bool:
        """Shortcut: construiește și trimite un email într-un singur pas."""
        msg = self.build_email(to_email, subject, body, attachments)
        return self.send(msg, dry_run=dry_run)
