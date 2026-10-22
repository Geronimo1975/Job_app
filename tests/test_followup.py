"""
Teste pentru follow-up — logică pură, store, IMAP (mock) și agent (mock).
Nu lovesc rețeaua: IMAP și SMTP sunt înlocuite cu mock-uri.
"""

from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from sender.followup import (
    due_for_followup,
    followup_subject,
    build_followup_body,
)
from sender.followup_store import FollowupStore
from sender.imap_reader import recent_reply_senders, ImapError


NOW = datetime(2026, 10, 22, 12, 0, 0)


def _entry(days_ago=10, email="hr@acme.de", followed=None):
    return {
        "email": email,
        "sent_at": (NOW - timedelta(days=days_ago)).isoformat(),
        "followup_sent_at": followed,
        "company": "Acme",
        "position": "AI Engineer",
        "language": "de",
    }


class TestDueForFollowup:
    def test_due_when_old_and_no_reply(self):
        assert due_for_followup(_entry(10), set(), NOW, after_days=7) is True

    def test_not_due_before_threshold(self):
        assert due_for_followup(_entry(3), set(), NOW, after_days=7) is False

    def test_skips_if_replied(self):
        assert due_for_followup(_entry(email="hr@acme.de"), {"hr@acme.de"}, NOW, 7) is False

    def test_reply_match_is_case_insensitive(self):
        # Store-ul ține adresa lowercased; replied vine tot lowercased din IMAP.
        assert due_for_followup(_entry(email="hr@acme.de"), {"hr@acme.de"}, NOW, 7) is False

    def test_skips_if_already_followed_up(self):
        e = _entry(10, followed=NOW.isoformat())
        assert due_for_followup(e, set(), NOW, 7) is False

    def test_no_sent_at(self):
        assert due_for_followup({"email": "x@y.de"}, set(), NOW, 7) is False

    def test_no_email(self):
        assert due_for_followup({"sent_at": NOW.isoformat()}, set(), NOW, 7) is False

    def test_bad_sent_at(self):
        assert due_for_followup({"email": "x@y.de", "sent_at": "notadate"}, set(), NOW, 7) is False


class TestBody:
    def test_german_body_has_greeting_and_name(self):
        b = build_followup_body("George", "Acme", "AI Engineer", "de")
        assert "Sehr geehrte" in b and "George" in b and "Acme" in b

    def test_english_body(self):
        b = build_followup_body("George", "Acme", "AI Engineer", "en")
        assert "Best regards" in b and "Acme" in b

    def test_subject_de_en(self):
        assert "Nachfrage" in followup_subject("AI Engineer", "de")
        assert "Following up" in followup_subject("AI Engineer", "en")


class TestStore:
    def test_record_and_read(self, tmp_path):
        s = FollowupStore(tmp_path / "f.json")
        s.record_sent("http://j/1", "hr@acme.de", "Acme", "Dev", "de", NOW)
        e = s.entries()["http://j/1"]
        assert e["email"] == "hr@acme.de"
        assert e["followup_sent_at"] is None

    def test_record_requires_url_and_email(self, tmp_path):
        s = FollowupStore(tmp_path / "f.json")
        s.record_sent("", "hr@acme.de")
        s.record_sent("http://j/1", "")
        assert s.entries() == {}

    def test_email_is_lowercased(self, tmp_path):
        s = FollowupStore(tmp_path / "f.json")
        s.record_sent("http://j/1", "HR@Acme.DE", when=NOW)
        assert s.entries()["http://j/1"]["email"] == "hr@acme.de"

    def test_mark_followed_up_persists(self, tmp_path):
        p = tmp_path / "f.json"
        s = FollowupStore(p)
        s.record_sent("http://j/1", "hr@acme.de", when=NOW)
        s.mark_followed_up("http://j/1", NOW)
        # Reîncărcat de pe disc:
        assert FollowupStore(p).entries()["http://j/1"]["followup_sent_at"] is not None

    def test_corrupt_file_tolerated(self, tmp_path):
        p = tmp_path / "f.json"
        p.write_text("{bad json")
        assert FollowupStore(p).entries() == {}


class TestImapReader:
    def test_missing_config_raises(self):
        with pytest.raises(ImapError):
            recent_reply_senders({}, "pw")

    def test_missing_password_raises(self):
        with pytest.raises(ImapError):
            recent_reply_senders({"server": "imap.x", "login": "a@x"}, "")

    @patch("sender.imap_reader.imaplib.IMAP4_SSL")
    def test_collects_from_addresses(self, mock_ssl):
        conn = MagicMock()
        conn.search.return_value = ("OK", [b"1 2"])
        conn.fetch.side_effect = [
            ("OK", [(b"1", b"From: HR Team <hr@acme.de>\r\n")]),
            ("OK", [(b"2", b"From: recruiter@beta.io\r\n")]),
        ]
        mock_ssl.return_value = conn
        got = recent_reply_senders(
            {"server": "imap.x", "login": "a@x"}, "pw", since_days=30
        )
        assert got == {"hr@acme.de", "recruiter@beta.io"}

    @patch("sender.imap_reader.imaplib.IMAP4_SSL", side_effect=OSError("no net"))
    def test_connection_failure_raises(self, _mock):
        with pytest.raises(ImapError):
            recent_reply_senders({"server": "imap.x", "login": "a@x"}, "pw")


class TestAgent:
    def _agent(self):
        from agents.followup_agent import FollowupAgent
        ag = FollowupAgent()
        return ag

    def test_disabled_does_nothing(self):
        from agents.followup_agent import FollowupAgent
        ag = FollowupAgent()
        ag.config = {"email": {"followup": {"enabled": False}}}
        res = ag.run()
        assert "dezactivat" in res.message

    @patch("agents.followup_agent.recent_reply_senders", side_effect=ImapError("down"))
    def test_aborts_when_imap_fails(self, _mock):
        from agents.followup_agent import FollowupAgent, AgentStatus
        ag = FollowupAgent()
        ag.config = {"email": {"followup": {"enabled": True, "imap": {}}}}
        ag.store.record_sent("http://j/1", "hr@acme.de", when=datetime.now() - timedelta(days=30))
        res = ag.run(dry_run=True)
        assert res.status == AgentStatus.FAILED
        assert res.items_succeeded == 0

    @patch("agents.followup_agent.recent_reply_senders", return_value=set())
    def test_dry_run_does_not_mark(self, _mock):
        from agents.followup_agent import FollowupAgent
        ag = FollowupAgent()
        ag.config = {"email": {"followup": {"enabled": True, "after_days": 7, "imap": {}}}}
        ag.smtp.build_email = MagicMock(return_value="MSG")
        ag.smtp.send = MagicMock(return_value=True)
        ag.store.record_sent("http://j/1", "hr@acme.de", when=datetime.now() - timedelta(days=30))
        res = ag.run(dry_run=True)
        assert res.items_succeeded == 1
        # Dry run NU marchează follow-up — altfel s-ar „consuma" fără trimitere.
        assert ag.store.entries()["http://j/1"]["followup_sent_at"] is None

    @patch("agents.followup_agent.recent_reply_senders", return_value={"hr@acme.de"})
    def test_skips_replied(self, _mock):
        from agents.followup_agent import FollowupAgent
        ag = FollowupAgent()
        ag.config = {"email": {"followup": {"enabled": True, "after_days": 7, "imap": {}}}}
        ag.smtp.send = MagicMock(return_value=True)
        ag.store.record_sent("http://j/1", "hr@acme.de", when=datetime.now() - timedelta(days=30))
        res = ag.run(dry_run=True)
        assert res.items_succeeded == 0
        ag.smtp.send.assert_not_called()
