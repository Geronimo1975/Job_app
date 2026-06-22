# tests/test_poll_step.py
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

from agents.base_agent import AgentResult, AgentStatus


def _args(live=False):
    return SimpleNamespace(live=live, provider=None, step="poll", min_score=30, use_ai=True)


def _ok(name, data=None):
    return AgentResult(agent_name=name, status=AgentStatus.SUCCESS, data=data or {})


def test_poll_no_commands_skips_send():
    import main
    pend = _ok("N8nSyncAgent", {"commands": []})
    with patch("main.N8nSyncAgent") as N8n, \
         patch("main.CoverLetterAgent") as CL, \
         patch("main.EmailAgent") as EM:
        N8n.return_value.run.return_value = pend
        results = main.run_poll(_args())
    CL.assert_not_called()
    EM.assert_not_called()
    assert any(r.agent_name == "N8nSyncAgent" for r in results)


def test_poll_processes_command_and_pushes_back():
    import main
    cmd = {"action": "send", "URL": "u1", "Firma": "X", "Kontakt Email": "hr@x.de",
           "Sprache": "de", "Anschreiben": "Sehr geehrte..."}
    pend = _ok("N8nSyncAgent", {"commands": [cmd]})
    push = _ok("N8nSyncAgent", {"upserted": 1})
    with patch("main.N8nSyncAgent") as N8n, \
         patch("main.CoverLetterAgent") as CL, \
         patch("main.EmailAgent") as EM:
        N8n.return_value.run.side_effect = [pend, push]
        CL.return_value.run.return_value = _ok("CoverLetterAgent",
            {"jobs_with_letters": [{"url": "u1", "company": "X", "email_hr": "hr@x.de",
                                    "language": "de", "cover_letter": "Sehr geehrte..."}]})
        EM.return_value.run.return_value = _ok("EmailAgent", {})
        results = main.run_poll(_args(live=False))
    # A doua chemare N8nSyncAgent.run trebuie să fie push cu status setat
    second_call = N8n.return_value.run.call_args_list[1]
    assert second_call.kwargs["action"] == "push"
    pushed_jobs = second_call.kwargs["jobs"]
    assert pushed_jobs[0]["url"] == "u1"
    EM.return_value.run.assert_called_once()


def test_poll_lifts_sprache_into_analysis():
    import main
    # Fără Anschreiben → needs_letter non-gol → CoverLetterAgent.run este chemat
    cmd = {"action": "send", "URL": "u1", "Firma": "X", "Kontakt Email": "hr@x.de",
           "Sprache": "en"}
    pend = _ok("N8nSyncAgent", {"commands": [cmd]})
    push = _ok("N8nSyncAgent", {"upserted": 1})
    with patch("main.N8nSyncAgent") as N8n, \
         patch("main.CoverLetterAgent") as CL, \
         patch("main.EmailAgent") as EM:
        N8n.return_value.run.side_effect = [pend, push]
        CL.return_value.run.return_value = _ok("CoverLetterAgent",
            {"jobs_with_letters": [{"url": "u1", "company": "X", "email_hr": "hr@x.de",
                                    "language": "en", "analysis": {"language": "en"},
                                    "cover_letter": "Dear..."}]})
        EM.return_value.run.return_value = _ok("EmailAgent", {})
        main.run_poll(_args(live=False))
    # Verificăm că run_poll a ridicat limba în analysis ÎNAINTE ca CoverLetterAgent să ruleze
    cl_jobs = CL.return_value.run.call_args.kwargs["jobs"]
    assert cl_jobs[0]["analysis"]["language"] == "en"
