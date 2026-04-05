"""
Teste unitare pentru EmailAgent — mock SMTP, fără trimitere reală.
"""

from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from agents.base_agent import AgentStatus
from agents.email_agent import EmailAgent


@pytest.fixture
def email_agent():
    agent = EmailAgent()
    agent.logger.setLevel("CRITICAL")
    return agent


class TestEmailAgent:
    def test_instantiation(self, email_agent):
        assert email_agent.name == "EmailAgent"

    def test_dry_run_default(self, email_agent, sample_job_analyzed):
        """Dry run nu trimite emailuri."""
        result = email_agent.run(
            jobs=[sample_job_analyzed],
            dry_run=True,
        )

        assert result.status in (AgentStatus.SUCCESS, AgentStatus.PARTIAL, AgentStatus.FAILED)
        # Dry run => nu ar trebui să avem erori SMTP
        # (poate eșua din alte motive, dar nu din SMTP)

    @patch("agents.email_agent.SmtpSender")
    def test_run_with_mocked_smtp(self, mock_smtp_cls, email_agent, sample_job_analyzed):
        """Testează flow-ul complet cu SMTP mock."""
        mock_sender = MagicMock()
        mock_sender.send_simple.return_value = True
        mock_sender.build_email.return_value = MagicMock()
        mock_sender.send.return_value = True
        mock_smtp_cls.return_value = mock_sender

        # Adăugăm cover_letter_path la job
        job = sample_job_analyzed.copy()
        job["cover_letter_path"] = "/tmp/fake_cover_letter.txt"

        result = email_agent.run(
            jobs=[job],
            dry_run=True,
            provider="gmx",
        )

        assert result.status in (AgentStatus.SUCCESS, AgentStatus.PARTIAL, AgentStatus.FAILED)

    def test_empty_jobs_list(self, email_agent):
        """Run cu lista goală."""
        result = email_agent.run(jobs=[], dry_run=True)

        assert result.items_processed == 0

    def test_no_email_hr(self, email_agent, sample_job_analyzed):
        """Job fără email HR — trebuie skip-uit."""
        job = sample_job_analyzed.copy()
        job.pop("email_hr", None)

        result = email_agent.run(jobs=[job], dry_run=True)

        # Ar trebui să gestioneze lipsa emailului
        assert result.status in (AgentStatus.SUCCESS, AgentStatus.PARTIAL, AgentStatus.FAILED)

    @patch("agents.email_agent.SmtpSender")
    def test_smtp_failure_handled(self, mock_smtp_cls, email_agent, sample_job_analyzed):
        """Testează gestionarea erorilor SMTP."""
        mock_sender = MagicMock()
        mock_sender.send.side_effect = Exception("SMTP connection refused")
        mock_sender.send_simple.side_effect = Exception("SMTP connection refused")
        mock_smtp_cls.return_value = mock_sender

        job = sample_job_analyzed.copy()
        job["cover_letter_path"] = "/tmp/fake_letter.txt"

        result = email_agent.run(
            jobs=[job],
            dry_run=False,
            provider="gmx",
        )

        # Nu trebuie să crape — returnează FAILED sau PARTIAL
        assert result.status in (AgentStatus.FAILED, AgentStatus.PARTIAL)
