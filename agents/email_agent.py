"""
Email Agent — Trimitere automată emailuri de aplicare.

Responsabilități:
  1. Construiește emailuri cu subject, body, atașamente
  2. Trimite prin GMX/Brevo SMTP
  3. Respectă rate limiting
  4. Loghează fiecare trimitere
  5. Actualizează tracker-ul cu status
"""

from datetime import datetime
from pathlib import Path

from agents.base_agent import BaseAgent, AgentResult, AgentStatus
from generators.email_body_gen import EmailBodyGenerator
from sender.smtp_sender import SmtpSender


class EmailAgent(BaseAgent):
    """Agent pentru trimitere automată de emailuri de aplicare."""

    def __init__(self):
        super().__init__("EmailAgent")
        self.smtp = SmtpSender(self.config, self.project_root)
        self.body_gen = EmailBodyGenerator(self.config, self.project_root)

    def run(
        self,
        jobs: list[dict] | None = None,
        dry_run: bool | None = None,
        provider: str | None = None,
        **kwargs,
    ) -> AgentResult:
        """
        Trimite emailuri de aplicare pentru joburile specificate.

        Args:
            jobs: Lista de joburi pregătite (cu cover_letter_path, etc.)
            dry_run: Override dry run mode
            provider: Override SMTP provider
        """
        start = self._start()
        result = AgentResult(agent_name=self.name, status=AgentStatus.RUNNING)

        if dry_run is None:
            dry_run = self.config.get("email", {}).get("dry_run_default", True)

        if dry_run:
            self.logger.info("⚠ DRY RUN MODE — emailurile NU vor fi trimise efectiv")

        if not jobs:
            result.status = AgentStatus.SUCCESS
            result.message = "Niciun job de procesat"
            return self._finish(result)

        sent_results = []

        for job in jobs:
            result.items_processed += 1
            company = job.get("company", "Unknown")
            position = job.get("title", job.get("position", "Unknown"))
            to_email = job.get("email_hr", job.get("Kontakt Email", ""))
            contact = job.get("contact_person", job.get("Kontakt Person", ""))
            language = job.get("analysis", {}).get("language", "de")

            if not to_email:
                self.logger.warning(f"  ⚠ {company} — lipsă email, skip")
                result.errors.append(f"{company}: lipsă email")
                continue

            self.logger.info(f"  📧 {company} — {position} → {to_email}")

            try:
                # Generează body
                body = self.body_gen.generate(
                    company=company,
                    position=position,
                    contact_person=contact,
                    language=language,
                )

                # Subject
                applicant_name = self.config.get("applicant", {}).get("name", "")
                subject = f"Bewerbung als {position} — {applicant_name}"

                # Colectează atașamente
                attachments = self._collect_attachments(job)

                # Construiește și trimite
                msg = self.smtp.build_email(
                    to_email=to_email,
                    subject=subject,
                    body=body,
                    attachments=attachments,
                )

                success = self.smtp.send(msg, provider=provider, dry_run=dry_run)

                if success:
                    result.items_succeeded += 1
                    status_text = "DRY RUN" if dry_run else "GESENDET"
                    self.logger.info(f"  ✅ {status_text}: {company}")
                    sent_results.append({
                        "company": company,
                        "position": position,
                        "email": to_email,
                        "status": status_text,
                        "timestamp": datetime.now().isoformat(),
                    })
                else:
                    result.errors.append(f"{company}: trimitere eșuată")

            except Exception as e:
                self.logger.error(f"  ❌ {company}: {e}")
                result.errors.append(f"{company}: {e}")

        # Log rezultate
        self._save_send_log(sent_results)

        result.data = {
            "sent": sent_results,
            "dry_run": dry_run,
        }

        if result.errors and result.items_succeeded == 0:
            result.status = AgentStatus.FAILED
            result.message = "Niciun email trimis"
        elif result.errors:
            result.status = AgentStatus.PARTIAL
            result.message = f"{result.items_succeeded}/{result.items_processed} emailuri trimise"
        else:
            result.status = AgentStatus.SUCCESS
            mode = " (DRY RUN)" if dry_run else ""
            result.message = f"{result.items_succeeded} emailuri trimise{mode}"

        return self._finish(result)

    def _collect_attachments(self, job: dict) -> list[Path]:
        """Colectează toate atașamentele pentru un job."""
        attachments = []
        docs_config = self.config.get("documents", {})

        # Cover letter
        cl_path = job.get("cover_letter_path", "")
        if cl_path:
            p = Path(cl_path)
            if p.exists():
                attachments.append(p)

        # CV
        cv_path = self.project_root / docs_config.get(
            "cv_file", "documents/cv/Resume George Sebastian Cucuiet.pdf"
        )
        if cv_path.exists():
            attachments.append(cv_path)

        # Zertifikate dir — adaugă toate PDF-urile
        certs_dir = self.project_root / docs_config.get(
            "certificates_dir", "documents/certificates"
        )
        if certs_dir.exists():
            for cert in sorted(certs_dir.glob("*.pdf")):
                attachments.append(cert)

        return attachments

    def _save_send_log(self, results: list[dict]) -> None:
        """Salvează log-ul trimiterii."""
        log_dir = self.project_root / self.config.get("output", {}).get(
            "logs_dir", "output/logs"
        )
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / "email_send_log.txt"

        with open(log_file, "a", encoding="utf-8") as f:
            for r in results:
                f.write(
                    f"[{r['timestamp']}] {r['status']} — "
                    f"{r['company']} — {r['position']} — {r['email']}\n"
                )
