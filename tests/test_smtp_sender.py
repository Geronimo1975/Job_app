"""
Teste unitare pentru SmtpSender — rate limiting persistat pe disc între procese.
"""

import json
import threading
from unittest.mock import MagicMock, patch

import pytest

from sender.smtp_sender import SmtpSender


@pytest.fixture
def sender(mock_config, tmp_path):
    return SmtpSender(mock_config, tmp_path)


class TestRateLimitPersistence:
    def test_fresh_sender_has_zero_sent_today(self, sender):
        assert sender._sent_today == 0

    @patch("sender.smtp_sender.smtplib.SMTP")
    def test_send_persists_count_to_state_file(self, mock_smtp_cls, sender, tmp_path, monkeypatch):
        monkeypatch.setenv("GMX_PASSWORD", "fake-password")
        mock_smtp_cls.return_value = MagicMock()
        msg = sender.build_email("hr@test.de", "Betreff", "Text")

        sender.send(msg, dry_run=False)

        state_file = tmp_path / "output" / "logs" / "email_rate_state.json"
        assert state_file.exists()
        state = json.loads(state_file.read_text())
        assert state["count"] == 1

    @patch("sender.smtp_sender.smtplib.SMTP")
    def test_new_instance_loads_count_from_state_file(self, mock_smtp_cls, mock_config, tmp_path, monkeypatch):
        monkeypatch.setenv("GMX_PASSWORD", "fake-password")
        mock_smtp_cls.return_value = MagicMock()
        first = SmtpSender(mock_config, tmp_path)
        msg = first.build_email("hr@test.de", "Betreff", "Text")
        first.send(msg, dry_run=False)

        second = SmtpSender(mock_config, tmp_path)
        assert second._sent_today == 1

    def test_stale_date_resets_count(self, tmp_path, mock_config):
        state_file = tmp_path / "output" / "logs" / "email_rate_state.json"
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_file.write_text(json.dumps({"date": "2020-01-01", "count": 9}))

        fresh = SmtpSender(mock_config, tmp_path)
        assert fresh._sent_today == 0

    @patch("sender.smtp_sender.smtplib.SMTP")
    def test_rate_limit_enforced_across_instances(self, mock_smtp_cls, mock_config, tmp_path, monkeypatch):
        monkeypatch.setenv("GMX_PASSWORD", "fake-password")
        mock_smtp_cls.return_value = MagicMock()
        limited_config = dict(mock_config)
        limited_config["email"] = dict(limited_config["email"])
        limited_config["email"]["max_emails_per_day"] = 1

        first = SmtpSender(limited_config, tmp_path)
        msg1 = first.build_email("hr@test.de", "Betreff", "Text")
        first.send(msg1, dry_run=False)

        second = SmtpSender(limited_config, tmp_path)
        msg2 = second.build_email("hr2@test.de", "Betreff", "Text")
        with pytest.raises(RuntimeError, match="Rate limit"):
            second.send(msg2, dry_run=False)

    @patch("sender.smtp_sender.smtplib.SMTP")
    def test_concurrent_sends_are_serialized_by_file_lock(
        self, mock_smtp_cls, mock_config, tmp_path, monkeypatch
    ):
        """Doi SmtpSender pe același tmp_path, trimițând 'simultan', cu cap=1:
        lockul de fișier trebuie să serializeze accesul astfel încât exact un
        send să reușească și celălalt să lovească rate limit-ul (nu ambele să
        treacă printr-un read-then-write nesincronizat)."""
        monkeypatch.setenv("GMX_PASSWORD", "fake-password")
        mock_smtp_cls.return_value = MagicMock()

        limited_config = dict(mock_config)
        limited_config["email"] = dict(limited_config["email"])
        limited_config["email"]["max_emails_per_day"] = 1

        first = SmtpSender(limited_config, tmp_path)
        second = SmtpSender(limited_config, tmp_path)
        msg1 = first.build_email("hr1@test.de", "Betreff", "Text")
        msg2 = second.build_email("hr2@test.de", "Betreff", "Text")

        barrier = threading.Barrier(2)
        results: dict[str, object] = {}

        def worker(name, sender, msg):
            barrier.wait()  # pornește ambele thread-uri cât mai simultan
            try:
                results[name] = sender.send(msg, dry_run=False)
            except RuntimeError as exc:
                results[name] = exc

        t1 = threading.Thread(target=worker, args=("first", first, msg1))
        t2 = threading.Thread(target=worker, args=("second", second, msg2))
        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)

        outcomes = list(results.values())
        successes = [r for r in outcomes if r is True]
        errors = [r for r in outcomes if isinstance(r, RuntimeError)]

        assert len(successes) == 1, f"Așteptat exact un send reușit, am primit: {outcomes}"
        assert len(errors) == 1, f"Așteptat exact o eroare de rate limit, am primit: {outcomes}"
        assert "Rate limit" in str(errors[0])

        state_file = tmp_path / "output" / "logs" / "email_rate_state.json"
        state = json.loads(state_file.read_text())
        assert state["count"] == 1
