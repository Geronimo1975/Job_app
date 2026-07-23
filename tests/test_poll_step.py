# tests/test_poll_step.py
from types import SimpleNamespace
from unittest.mock import patch

from agents.base_agent import AgentResult, AgentStatus


def _args(live=False):
    return SimpleNamespace(live=live, provider=None, step="poll", min_score=30, use_ai=True)


def _ok(name, data=None):
    return AgentResult(agent_name=name, status=AgentStatus.SUCCESS, data=data or {})


# Comenzile vin din nodul „Filter SENDEN" al workflow-ului JobApp Pending și
# folosesc headerele GERMANE reale ale Sheet-ului. Testele care foloseau „URL"
# / „Anschreiben" validau o schemă inexistentă.
def _cmd(**over):
    cmd = {
        "action": "send", "Apply URL": "u1", "Firma": "X", "Position": "Dev",
        "Kontakt Email": "hr@x.de", "Sprache": "de", "Anschreiben PDF": "",
        "Email Status": "",
    }
    cmd.update(over)
    return cmd


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


def test_poll_live_marcheaza_si_consuma_triggerul():
    """Pe --live, rândurile chiar trimise primesc GESENDET și li se golește col P."""
    import main
    pend = _ok("N8nSyncAgent", {"commands": [_cmd()]})
    push = _ok("N8nSyncAgent", {"upserted": 1})
    with patch("main.N8nSyncAgent") as N8n, \
         patch("main.CoverLetterAgent") as CL, \
         patch("main.EmailAgent") as EM:
        N8n.return_value.run.side_effect = [pend, push]
        CL.return_value.run.return_value = _ok("CoverLetterAgent", {
            "jobs_with_letters": [{"url": "u1", "company": "X", "email_hr": "hr@x.de",
                                   "language": "de", "cover_letter": "Sehr geehrte..."}]})
        EM.return_value.run.return_value = _ok("EmailAgent", {"sent": [{"url": "u1"}]})
        main.run_poll(_args(live=True))

    second = N8n.return_value.run.call_args_list[1]
    assert second.kwargs["action"] == "push"
    job = second.kwargs["jobs"][0]
    assert job["url"] == "u1"
    assert job["email_status"] == "GESENDET"
    assert job["sende_befehl"] == ""      # altfel poll-ul reprocesează la infinit
    assert "status" not in job            # col M aparține Apps Script


def test_poll_dry_run_nu_scrie_in_sheet():
    """Dry-run nu are voie să marcheze rândul ca procesat."""
    import main
    pend = _ok("N8nSyncAgent", {"commands": [_cmd()]})
    with patch("main.N8nSyncAgent") as N8n, \
         patch("main.CoverLetterAgent") as CL, \
         patch("main.EmailAgent") as EM:
        N8n.return_value.run.return_value = pend
        CL.return_value.run.return_value = _ok("CoverLetterAgent", {
            "jobs_with_letters": [{"url": "u1", "company": "X", "cover_letter": "..."}]})
        EM.return_value.run.return_value = _ok("EmailAgent", {"sent": [{"url": "u1"}]})
        main.run_poll(_args(live=False))

    actiuni = [c.kwargs.get("action") for c in N8n.return_value.run.call_args_list]
    assert "push" not in actiuni


def test_poll_nu_marcheaza_joburile_netrimise():
    """Un job fără Kontakt Email e sărit de EmailAgent — nu trebuie marcat."""
    import main
    pend = _ok("N8nSyncAgent", {"commands": [_cmd(), _cmd(**{"Apply URL": "u2",
                                                            "Kontakt Email": ""})]})
    push = _ok("N8nSyncAgent", {"upserted": 1})
    with patch("main.N8nSyncAgent") as N8n, \
         patch("main.CoverLetterAgent") as CL, \
         patch("main.EmailAgent") as EM:
        N8n.return_value.run.side_effect = [pend, push]
        CL.return_value.run.return_value = _ok("CoverLetterAgent", {"jobs_with_letters": []})
        EM.return_value.run.return_value = _ok("EmailAgent", {"sent": [{"url": "u1"}]})
        main.run_poll(_args(live=True))

    jobs = N8n.return_value.run.call_args_list[1].kwargs["jobs"]
    trimis = next(j for j in jobs if j["url"] == "u1")
    netrimis = next(j for j in jobs if j["url"] == "u2")
    assert trimis["email_status"] == "GESENDET"
    assert trimis["sende_befehl"] == ""
    # rândul netrimis păstrează valoarea venită din Sheet ("") și NU capătă
    # sende_befehl, deci col P rămâne SENDEN pentru o încercare ulterioară
    assert netrimis["email_status"] != "GESENDET"
    assert "sende_befehl" not in netrimis


def test_poll_sare_peste_randurile_deja_trimise_de_apps_script():
    """Sistemul A scrie `sent` în col R; poll-ul nu are voie să retrimită."""
    import main
    pend = _ok("N8nSyncAgent", {"commands": [_cmd(**{"Email Status": "sent"})]})
    with patch("main.N8nSyncAgent") as N8n, \
         patch("main.CoverLetterAgent") as CL, \
         patch("main.EmailAgent") as EM:
        N8n.return_value.run.return_value = pend
        main.run_poll(_args(live=True))
    CL.assert_not_called()
    EM.assert_not_called()


def test_poll_pastreaza_joburile_care_aveau_deja_anschreiben():
    """`jobs_with_letters` e doar un subset — nu are voie să înlocuiască lotul."""
    import main
    pend = _ok("N8nSyncAgent", {"commands": [_cmd(), _cmd(**{"Apply URL": "u2"})]})
    push = _ok("N8nSyncAgent", {"upserted": 2})
    with patch("main.N8nSyncAgent") as N8n, \
         patch("main.CoverLetterAgent") as CL, \
         patch("main.EmailAgent") as EM:
        N8n.return_value.run.side_effect = [pend, push]
        CL.return_value.run.return_value = _ok("CoverLetterAgent", {
            "jobs_with_letters": [{"url": "u1", "cover_letter": "generat"}]})
        EM.return_value.run.return_value = _ok("EmailAgent",
                                               {"sent": [{"url": "u1"}, {"url": "u2"}]})
        main.run_poll(_args(live=True))

    urls = {j["url"] for j in EM.return_value.run.call_args.kwargs["jobs"]}
    assert urls == {"u1", "u2"}


def test_poll_lifts_sprache_into_analysis():
    import main
    pend = _ok("N8nSyncAgent", {"commands": [_cmd(**{"Sprache": "en"})]})
    push = _ok("N8nSyncAgent", {"upserted": 1})
    with patch("main.N8nSyncAgent") as N8n, \
         patch("main.CoverLetterAgent") as CL, \
         patch("main.EmailAgent") as EM:
        N8n.return_value.run.side_effect = [pend, push]
        CL.return_value.run.return_value = _ok("CoverLetterAgent", {
            "jobs_with_letters": [{"url": "u1", "company": "X", "email_hr": "hr@x.de",
                                   "language": "en", "analysis": {"language": "en"},
                                   "cover_letter": "Dear..."}]})
        EM.return_value.run.return_value = _ok("EmailAgent", {"sent": []})
        main.run_poll(_args(live=True))

    cl_jobs = CL.return_value.run.call_args.kwargs["jobs"]
    assert cl_jobs[0]["analysis"]["language"] == "en"
