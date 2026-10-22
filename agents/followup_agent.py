"""
Follow-up Agent — nudge politicos pentru candidaturile fără răspuns.

Face CORECT ce auto-follow-up-ul naiv face greșit: întâi citește inbox-ul prin
IMAP ca să afle cine a răspuns, și NU trimite nimănui care a răspuns deja. Dacă
IMAP-ul nu poate fi interogat, abortează complet — mai bine zero follow-up-uri
decât un nudge trimis peste un fir deja viu.

Dry-run implicit (ca restul pipeline-ului). Rate-limit-ul e cel partajat din
SmtpSender, deci follow-up-urile și candidaturile noi împart aceeași limită.
"""

from __future__ import annotations

import os
from datetime import datetime

from agents.base_agent import AgentResult, AgentStatus, BaseAgent
from sender.smtp_sender import SmtpSender
from sender.followup_store import FollowupStore
from sender.imap_reader import recent_reply_senders, ImapError
from sender.followup import due_for_followup, followup_subject, build_followup_body


class FollowupAgent(BaseAgent):
    def __init__(self):
        super().__init__("FollowupAgent")
        self.smtp = SmtpSender(self.config, self.project_root)
        self.store = FollowupStore(
            self.project_root / "output" / "logs" / "followup_state.json"
        )

    def run(self, dry_run: bool | None = None, provider: str | None = None,
            **kwargs) -> AgentResult:
        self._start()
        result = AgentResult(agent_name=self.name, status=AgentStatus.RUNNING)

        email_cfg = self.config.get("email", {}) or {}
        fcfg = email_cfg.get("followup", {}) or {}
        if not fcfg.get("enabled", False):
            result.status = AgentStatus.SUCCESS
            result.message = "Follow-up dezactivat (email.followup.enabled: false)"
            return self._finish(result)

        if dry_run is None:
            dry_run = email_cfg.get("dry_run_default", True)
        if dry_run:
            self.logger.info("⚠ DRY RUN — follow-up-urile NU se trimit efectiv")

        after_days = int(fcfg.get("after_days", 7))
        since_days = int(fcfg.get("imap_since_days", 30))
        imap_cfg = fcfg.get("imap", {}) or {}

        entries = self.store.entries()
        if not entries:
            result.status = AgentStatus.SUCCESS
            result.message = "Nicio candidatură înregistrată pentru follow-up"
            return self._finish(result)

        # GARDĂ: fără lista de răspunsuri, nu trimitem nimic.
        imap_password = os.environ.get(imap_cfg.get("env_password_key", ""), "")
        try:
            replied = recent_reply_senders(imap_cfg, imap_password, since_days)
            self.logger.info(f"  📥 {len(replied)} expeditori în inbox (ultimele {since_days}z)")
        except ImapError as e:
            result.status = AgentStatus.FAILED
            result.message = f"IMAP indisponibil — abort (zero follow-up): {e}"
            result.errors.append(str(e))
            return self._finish(result)

        now = datetime.now()
        for url, entry in entries.items():
            if not due_for_followup(entry, replied, now, after_days):
                continue
            result.items_processed += 1
            lang = entry.get("language", "de")
            subject = followup_subject(entry.get("position", ""), lang)
            applicant_name = self.config.get("applicant", {}).get("name", "")
            body = build_followup_body(
                applicant_name, entry.get("company", ""),
                entry.get("position", ""), lang,
            )
            try:
                msg = self.smtp.build_email(entry["email"], subject, body)
                if self.smtp.send(msg, provider=provider, dry_run=dry_run):
                    result.items_succeeded += 1
                    tag = "DRY RUN" if dry_run else "FOLLOWUP"
                    self.logger.info(f"  ✅ {tag}: {entry.get('company')} → {entry['email']}")
                    if not dry_run:
                        self.store.mark_followed_up(url, now)
            except RuntimeError as e:  # rate limit atins — oprire ordonată
                self.logger.info(f"  ⏸ {e}")
                result.errors.append(str(e))
                break
            except Exception as e:  # noqa: BLE001
                result.items_failed += 1
                result.errors.append(f"{entry.get('company')}: {e}")

        mode = " (DRY RUN)" if dry_run else ""
        result.status = AgentStatus.SUCCESS if result.items_failed == 0 else AgentStatus.PARTIAL
        result.message = f"{result.items_succeeded} follow-up-uri{mode}"
        return self._finish(result)
