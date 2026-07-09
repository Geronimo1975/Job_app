# Job_app ↔ Google Sheets ↔ n8n Integration — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Conectează pipeline-ul Job_app la un Google Sheet (sursă de adevăr) prin n8n, cu Python care face push prin webhook și polling pentru comenzile `SENDEN`, păstrând Excel ca backup.

**Architecture:** Python NU atinge Google direct. `N8nClient` (httpx) vorbește cu 2 webhook-uri n8n (`jobapp-ingest`, `jobapp-pending`) peste HTTPS cu un secret partajat. n8n scrie/citește Google Sheet `1p0u4...` (tab „Job Applications"). Un strat de mapping traduce între dict-ul intern (englez) și headerele germane ale Sheet-ului. Excel local rămâne dual-write ca backup. Trigger-ul vine prin `main.py --step poll` rulat de cron/launchd.

**Tech Stack:** Python 3.12, httpx (deja în requirements), pytest + unittest.mock, n8n public API (curl), openpyxl (existent).

## Global Constraints

- Python 3.12+; comentarii/mesaje în limba română (convenție repo).
- Fiecare agent moștenește `agents/base_agent.py::BaseAgent` și întoarce `AgentResult` cu `AgentStatus`.
- `n8n.enabled=false` ⟹ comportament identic cu cel actual (zero regresie); erorile n8n NU pică pipeline-ul (PARTIAL, nu FAILED).
- Cheia de upsert în Sheet = coloana `URL`. Cheile payload-ului = headerele germane **exact** (case-sensitive): `URL`, `Firma`, `Position`, `Standort`, `Quelle`, `Kontakt Email`, `Kontakt Person`, `Sprache`, `Gefunden am`, `Status`, `Anschreiben`, `Match Score`, `Keywords Match`, `Email Inhalt`, `Gesendet am`, `Email Status`, `Sende Befehl`, `Notizen`.
- n8n: `https://app.n8n-studio.com` (HTTPS/443). Sheet doc `1p0u4XcMxOWex2-OTfIxnvpun3NVFkrqh3DvLWhRBkO0`, tab `Job Applications`. Cred Google `lwLv81Aaab8h0Zm3`, cred Brevo `aK6Bete8OTRMNACY`.
- Secrete în `.env` (gitignored): `N8N_WEBHOOK_SECRET`, `N8N_API_KEY` (doar pentru deploy, nu runtime).
- Spec: `docs/superpowers/specs/2026-06-22-job_app-n8n-sheets-integration-design.md`.

---

### Task 1: Mapping intern ↔ Sheet (funcții pure)

**Files:**
- Create: `sender/n8n_mapping.py`
- Test: `tests/test_n8n_mapping.py`

**Interfaces:**
- Consumes: nimic (funcții pure).
- Produces:
  - `job_to_sheet_row(job: dict) -> dict` — internal job → dict cu chei = headere germane.
  - `sheet_row_to_job(row: dict) -> dict` — comandă n8n (chei germane) → internal job.
  - `FIELD_MAP: dict[str, str]` — internal_key → german_header.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_n8n_mapping.py
from sender.n8n_mapping import job_to_sheet_row, sheet_row_to_job


def test_job_to_sheet_row_maps_core_and_analysis():
    job = {
        "url": "https://x/job/1", "company": "Studio X", "title": "Python Dev",
        "location": "Berlin", "source": "jobspy", "email_hr": "hr@x.de",
        "contact_person": "Frau Y", "language": "de", "status": "Bereit",
        "cover_letter": "Sehr geehrte...",
        "analysis": {"match_score": 72, "matching_skills": ["Python", "Django"]},
    }
    row = job_to_sheet_row(job)
    assert row["URL"] == "https://x/job/1"
    assert row["Firma"] == "Studio X"
    assert row["Position"] == "Python Dev"
    assert row["Kontakt Email"] == "hr@x.de"
    assert row["Sprache"] == "de"
    assert row["Status"] == "Bereit"
    assert row["Match Score"] == 72
    assert row["Keywords Match"] == "Python, Django"
    assert row["Anschreiben"] == "Sehr geehrte..."


def test_sheet_row_to_job_inverse_core():
    cmd = {
        "action": "send", "URL": "https://x/job/1", "Firma": "Studio X",
        "Position": "Python Dev", "Kontakt Email": "hr@x.de", "Sprache": "de",
        "Anschreiben": "Sehr geehrte...",
    }
    job = sheet_row_to_job(cmd)
    assert job["url"] == "https://x/job/1"
    assert job["company"] == "Studio X"
    assert job["title"] == "Python Dev"
    assert job["email_hr"] == "hr@x.de"
    assert job["language"] == "de"
    assert job["cover_letter"] == "Sehr geehrte..."


def test_job_to_sheet_row_tolerates_missing_fields():
    row = job_to_sheet_row({"url": "u"})
    assert row["URL"] == "u"
    assert row["Firma"] == ""
    assert row["Match Score"] == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_n8n_mapping.py -v`
Expected: FAIL cu `ModuleNotFoundError: No module named 'sender.n8n_mapping'`

- [ ] **Step 3: Write minimal implementation**

```python
# sender/n8n_mapping.py
"""
Mapping între dict-ul intern de job (chei engleze) și headerele germane ale
Google Sheet-ului „Job Applications". Funcții pure, fără I/O.
"""

# internal_key -> german_header (1:1, valori simple)
FIELD_MAP = {
    "company": "Firma",
    "title": "Position",
    "location": "Standort",
    "url": "URL",
    "source": "Quelle",
    "email_hr": "Kontakt Email",
    "contact_person": "Kontakt Person",
    "language": "Sprache",
    "status": "Status",
    "cover_letter": "Anschreiben",
    "email_inhalt": "Email Inhalt",
    "gesendet_am": "Gesendet am",
    "email_status": "Email Status",
    "notizen": "Notizen",
}

# header german -> internal_key (invers)
_REVERSE = {v: k for k, v in FIELD_MAP.items()}


def job_to_sheet_row(job: dict) -> dict:
    """Convertește un job intern într-un rând pentru Sheet (chei germane)."""
    row: dict = {}
    for internal_key, german in FIELD_MAP.items():
        row[german] = job.get(internal_key, "")

    # Câmpuri derivate din analiză
    analysis = job.get("analysis", {}) or {}
    score = analysis.get("match_score", job.get("match_score", ""))
    row["Match Score"] = score if score is not None else ""
    skills = analysis.get("matching_skills", [])
    row["Keywords Match"] = ", ".join(skills) if skills else job.get("keywords_match", "")

    row["Gefunden am"] = job.get("gefunden_am", "")
    return row


def sheet_row_to_job(row: dict) -> dict:
    """Convertește o comandă n8n (chei germane) într-un job intern."""
    job: dict = {}
    for german, value in row.items():
        if german in _REVERSE:
            job[_REVERSE[german]] = value
    return job
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_n8n_mapping.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add sender/n8n_mapping.py tests/test_n8n_mapping.py
git commit -m "feat(n8n): adaugă mapping intern <-> headere Sheet (JOB-11)"
```

---

### Task 2: Config `n8n:` + `N8nClient` (transport HTTP)

**Files:**
- Modify: `config/settings.yaml` (adaugă bloc `n8n:` la final)
- Modify: `.env.example` (adaugă `N8N_WEBHOOK_SECRET`)
- Create: `sender/n8n_client.py`
- Test: `tests/test_n8n_client.py`

**Interfaces:**
- Consumes: `sender.n8n_mapping.job_to_sheet_row` (Task 1); config dict via `load_config`.
- Produces:
  - `class N8nClient` cu `__init__(self, config: dict)` și atribute `enabled: bool`, `base_url: str`, `secret: str`.
  - `ingest(self, jobs: list[dict]) -> dict` → `{"ok": bool, "upserted": int, ...}` sau `{"ok": False, "skipped": True, "upserted": 0}` când disabled.
  - `get_pending(self) -> list[dict]` → listă de comenzi (dict cu chei germane); `[]` când disabled.

- [ ] **Step 1: Adaugă blocul `n8n:` în `config/settings.yaml`**

La finalul fișierului `config/settings.yaml` adaugă:

```yaml

# ── n8n Integration ─────────────────────────────────────────
n8n:
  enabled: true
  base_url: "https://app.n8n-studio.com"
  paths:
    ingest: "/webhook/jobapp-ingest"
    pending: "/webhook/jobapp-pending"
  secret_env: "N8N_WEBHOOK_SECRET"
  poll_interval_min: 10
  timeout_sec: 20
```

- [ ] **Step 2: Adaugă secretul în `.env.example`**

În `.env.example` adaugă la final:

```
# n8n webhook shared secret (header X-JobApp-Secret)
N8N_WEBHOOK_SECRET=
```

- [ ] **Step 3: Write the failing test**

```python
# tests/test_n8n_client.py
from unittest.mock import patch, MagicMock

import httpx
import pytest

from sender.n8n_client import N8nClient

BASE_CFG = {
    "n8n": {
        "enabled": True,
        "base_url": "https://n8n.example.com",
        "paths": {"ingest": "/webhook/jobapp-ingest", "pending": "/webhook/jobapp-pending"},
        "secret_env": "N8N_WEBHOOK_SECRET",
        "timeout_sec": 5,
    }
}


def _resp(status=200, payload=None):
    m = MagicMock(spec=httpx.Response)
    m.status_code = status
    m.json.return_value = payload or {}
    m.raise_for_status.return_value = None
    return m


def test_ingest_posts_mapped_rows_with_secret(monkeypatch):
    monkeypatch.setenv("N8N_WEBHOOK_SECRET", "s3cr3t")
    client = N8nClient(BASE_CFG)
    with patch("sender.n8n_client.httpx.post", return_value=_resp(200, {"ok": True, "upserted": 1})) as p:
        out = client.ingest([{"url": "u1", "company": "X"}])
    assert out == {"ok": True, "upserted": 1}
    args, kwargs = p.call_args
    assert kwargs["headers"]["X-JobApp-Secret"] == "s3cr3t"
    assert kwargs["json"]["event"] == "jobs_upsert"
    assert kwargs["json"]["jobs"][0]["URL"] == "u1"
    assert kwargs["json"]["jobs"][0]["Firma"] == "X"


def test_get_pending_returns_commands(monkeypatch):
    monkeypatch.setenv("N8N_WEBHOOK_SECRET", "s3cr3t")
    client = N8nClient(BASE_CFG)
    payload = {"ok": True, "commands": [{"action": "send", "URL": "u1"}]}
    with patch("sender.n8n_client.httpx.post", return_value=_resp(200, payload)):
        cmds = client.get_pending()
    assert cmds == [{"action": "send", "URL": "u1"}]


def test_disabled_client_is_noop(monkeypatch):
    cfg = {"n8n": dict(BASE_CFG["n8n"], enabled=False)}
    client = N8nClient(cfg)
    with patch("sender.n8n_client.httpx.post") as p:
        assert client.ingest([{"url": "u"}]) == {"ok": False, "skipped": True, "upserted": 0}
        assert client.get_pending() == []
    p.assert_not_called()


def test_retries_once_on_5xx_then_raises(monkeypatch):
    monkeypatch.setenv("N8N_WEBHOOK_SECRET", "s")
    client = N8nClient(BASE_CFG)
    with patch("sender.n8n_client.httpx.post", return_value=_resp(503, {})) as p:
        with pytest.raises(RuntimeError):
            client.ingest([{"url": "u"}])
    assert p.call_count == 2
```

- [ ] **Step 4: Run test to verify it fails**

Run: `pytest tests/test_n8n_client.py -v`
Expected: FAIL cu `ModuleNotFoundError: No module named 'sender.n8n_client'`

- [ ] **Step 5: Write minimal implementation**

```python
# sender/n8n_client.py
"""
N8nClient — transport HTTP către webhook-urile n8n (ingest + pending).

Nu atinge Google direct: trimite/cere date de la n8n, care deține Sheet-ul.
Respectă flag-ul n8n.enabled (no-op când e dezactivat).
"""

import os

import httpx

from sender.n8n_mapping import job_to_sheet_row


class N8nClient:
    """Client subțire peste webhook-urile n8n."""

    def __init__(self, config: dict):
        cfg = config.get("n8n", {}) or {}
        self.enabled: bool = bool(cfg.get("enabled", False))
        self.base_url: str = str(cfg.get("base_url", "")).rstrip("/")
        self.paths: dict = cfg.get("paths", {}) or {}
        self.timeout: int = int(cfg.get("timeout_sec", 20))
        secret_env = cfg.get("secret_env", "N8N_WEBHOOK_SECRET")
        self.secret: str = os.environ.get(secret_env, "")

    def _post(self, path_key: str, payload: dict) -> dict:
        url = f"{self.base_url}{self.paths.get(path_key, '')}"
        headers = {"X-JobApp-Secret": self.secret, "Content-Type": "application/json"}
        last_err = None
        for _ in range(2):  # 1 încercare + 1 retry
            try:
                resp = httpx.post(url, json=payload, headers=headers, timeout=self.timeout)
                if resp.status_code >= 500:
                    last_err = RuntimeError(f"n8n 5xx: {resp.status_code}")
                    continue
                resp.raise_for_status()
                return resp.json()
            except (httpx.TransportError, httpx.HTTPStatusError) as e:
                last_err = e
                continue
        raise RuntimeError(f"n8n request eșuat ({path_key}): {last_err}")

    def ingest(self, jobs: list[dict]) -> dict:
        if not self.enabled:
            return {"ok": False, "skipped": True, "upserted": 0}
        rows = [job_to_sheet_row(j) for j in jobs]
        return self._post("ingest", {"event": "jobs_upsert", "jobs": rows})

    def get_pending(self) -> list[dict]:
        if not self.enabled:
            return []
        data = self._post("pending", {"action": "get_pending"})
        return data.get("commands", [])
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/test_n8n_client.py -v`
Expected: PASS (4 passed)

- [ ] **Step 7: Commit**

```bash
git add sender/n8n_client.py config/settings.yaml .env.example tests/test_n8n_client.py
git commit -m "feat(n8n): N8nClient + config n8n block (JOB-12)"
```

---

### Task 3: `N8nSyncAgent`

**Files:**
- Create: `agents/n8n_sync_agent.py`
- Test: `tests/test_n8n_sync_agent.py`

**Interfaces:**
- Consumes: `sender.n8n_client.N8nClient` (Task 2); `BaseAgent`/`AgentResult`/`AgentStatus`.
- Produces:
  - `class N8nSyncAgent(BaseAgent)` cu `run(self, action: str = "push", jobs: list[dict] | None = None, **kwargs) -> AgentResult`.
  - `action="push"` → `result.data = {"upserted": N}`; `action="pending"` → `result.data = {"commands": [...]}`.
  - La eroare de rețea n8n → `AgentStatus.PARTIAL` (NU FAILED), eroarea în `result.errors`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_n8n_sync_agent.py
from unittest.mock import patch, MagicMock

import pytest

from agents.base_agent import AgentStatus


@pytest.fixture
def agent(tmp_path, monkeypatch):
    # Redirecționează PROJECT_ROOT + config minimal
    import yaml
    (tmp_path / "config").mkdir()
    (tmp_path / "output" / "logs").mkdir(parents=True)
    cfg = {
        "n8n": {"enabled": True, "base_url": "https://n8n.example.com",
                "paths": {"ingest": "/i", "pending": "/p"},
                "secret_env": "N8N_WEBHOOK_SECRET", "timeout_sec": 5},
        "output": {"logs_dir": "output/logs", "log_level": "WARNING"},
    }
    (tmp_path / "config" / "settings.yaml").write_text(yaml.dump(cfg))
    monkeypatch.setattr("agents.base_agent.PROJECT_ROOT", tmp_path)
    from agents.n8n_sync_agent import N8nSyncAgent
    return N8nSyncAgent()


def test_push_success(agent):
    with patch.object(agent.client, "ingest", return_value={"ok": True, "upserted": 2}):
        res = agent.run(action="push", jobs=[{"url": "u1"}, {"url": "u2"}])
    assert res.status == AgentStatus.SUCCESS
    assert res.data["upserted"] == 2


def test_pending_success(agent):
    cmds = [{"action": "send", "URL": "u1"}]
    with patch.object(agent.client, "get_pending", return_value=cmds):
        res = agent.run(action="pending")
    assert res.status == AgentStatus.SUCCESS
    assert res.data["commands"] == cmds


def test_n8n_down_is_partial_not_failed(agent):
    with patch.object(agent.client, "ingest", side_effect=RuntimeError("boom")):
        res = agent.run(action="push", jobs=[{"url": "u1"}])
    assert res.status == AgentStatus.PARTIAL
    assert res.errors


def test_unknown_action_failed(agent):
    res = agent.run(action="nope")
    assert res.status == AgentStatus.FAILED
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_n8n_sync_agent.py -v`
Expected: FAIL cu `ModuleNotFoundError: No module named 'agents.n8n_sync_agent'`

- [ ] **Step 3: Write minimal implementation**

```python
# agents/n8n_sync_agent.py
"""
N8nSyncAgent — sincronizare cu n8n (push rezultate / pull comenzi pending).

Erorile n8n NU pică pipeline-ul: întoarce PARTIAL, pentru că Excel rămâne backup.
"""

from agents.base_agent import BaseAgent, AgentResult, AgentStatus
from sender.n8n_client import N8nClient


class N8nSyncAgent(BaseAgent):
    """Agent pentru comunicarea cu webhook-urile n8n."""

    def __init__(self):
        super().__init__("N8nSyncAgent")
        self.client = N8nClient(self.config)

    def run(self, action: str = "push", jobs: list[dict] | None = None, **kwargs) -> AgentResult:
        self._start()
        result = AgentResult(agent_name=self.name, status=AgentStatus.RUNNING)

        if action == "push":
            result = self._push(jobs or [])
        elif action == "pending":
            result = self._pending()
        else:
            result.status = AgentStatus.FAILED
            result.message = f"Acțiune necunoscută: {action}"

        return self._finish(result)

    def _push(self, jobs: list[dict]) -> AgentResult:
        result = AgentResult(agent_name=self.name, status=AgentStatus.RUNNING)
        if not jobs:
            result.status = AgentStatus.SUCCESS
            result.message = "Niciun job de trimis la n8n"
            return result
        try:
            out = self.client.ingest(jobs)
            upserted = out.get("upserted", 0)
            result.data = {"upserted": upserted}
            result.items_processed = len(jobs)
            result.items_succeeded = upserted
            if out.get("skipped"):
                result.status = AgentStatus.SUCCESS
                result.message = "n8n dezactivat — push omis"
            else:
                result.status = AgentStatus.SUCCESS
                result.message = f"{upserted} joburi sincronizate în Sheet via n8n"
        except Exception as e:  # rețea/HTTP — nu pică pipeline-ul
            result.status = AgentStatus.PARTIAL
            result.message = "n8n indisponibil — date păstrate doar în Excel local"
            result.errors.append(str(e))
            self.logger.warning(f"n8n push eșuat: {e}")
        return result

    def _pending(self) -> AgentResult:
        result = AgentResult(agent_name=self.name, status=AgentStatus.RUNNING)
        try:
            commands = self.client.get_pending()
            result.data = {"commands": commands}
            result.items_processed = len(commands)
            result.items_succeeded = len(commands)
            result.status = AgentStatus.SUCCESS
            result.message = f"{len(commands)} comenzi pending de la n8n"
        except Exception as e:
            result.status = AgentStatus.PARTIAL
            result.message = "n8n indisponibil — nu pot prelua comenzi pending"
            result.errors.append(str(e))
            self.logger.warning(f"n8n pending eșuat: {e}")
        return result
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_n8n_sync_agent.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add agents/n8n_sync_agent.py tests/test_n8n_sync_agent.py
git commit -m "feat(n8n): N8nSyncAgent push/pending (JOB-13)"
```

---

### Task 4: Integrare `main.py` — dual-write push + `--step poll`

**Files:**
- Modify: `main.py` (import, dual-write după pași, ramură `poll`, funcție `run_poll`)
- Test: `tests/test_poll_step.py`

**Interfaces:**
- Consumes: `N8nSyncAgent` (Task 3), `sheet_row_to_job` (Task 1), `CoverLetterAgent`, `EmailAgent` (existenți).
- Produces: `run_poll(args) -> list[AgentResult]` (testabilă cu agenți mock-uiți). Adaugă `"poll"` în `choices` pentru `--step`.

- [ ] **Step 1: Write the failing test**

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_poll_step.py -v`
Expected: FAIL cu `AttributeError: module 'main' has no attribute 'run_poll'`

- [ ] **Step 3: Adaugă importurile în `main.py`**

În `main.py`, după linia `from agents.tracker_agent import TrackerAgent` (linia 46), adaugă:

```python
from agents.n8n_sync_agent import N8nSyncAgent
from sender.n8n_mapping import sheet_row_to_job
```

- [ ] **Step 4: Adaugă funcția `run_poll` în `main.py`**

Înainte de `def main():` (linia ~280), adaugă:

```python
def run_poll(args) -> list:
    """Trigger prin polling: cere comenzi SENDEN de la n8n, procesează, push status.

    Pași:
      1. N8nSyncAgent(pending) → comenzi (chei germane).
      2. Mapează în joburi interne; sare peste cele deja GESENDET.
      3. Generează Anschreiben lipsă + trimite email (dry-run/live).
      4. Push status înapoi la n8n (Sheet) + Excel rămâne backup via EmailAgent.
    """
    logger = setup_logger("Poll")
    dry_run = not args.live
    results = []

    n8n = N8nSyncAgent()
    pending = n8n.run(action="pending")
    print_result(pending)
    results.append(pending)

    commands = pending.data.get("commands", [])
    # Filtrează defensiv joburile deja trimise
    jobs = [
        sheet_row_to_job(c)
        for c in commands
        if str(c.get("Email Status", "")).upper() != "GESENDET"
    ]

    if not jobs:
        print("  ⬜ Nicio comandă SENDEN de procesat.")
        return results

    print(f"  📥 {len(jobs)} comenzi SENDEN de procesat.")

    # Generează Anschreiben pentru joburile fără cover_letter
    needs_letter = [j for j in jobs if not j.get("cover_letter")]
    if needs_letter:
        cl = CoverLetterAgent()
        cl_res = cl.run(jobs=needs_letter)
        print_result(cl_res)
        results.append(cl_res)
        if cl_res.status != AgentStatus.FAILED:
            jobs = cl_res.data.get("jobs_with_letters", jobs)

    # Trimite emailuri
    em = EmailAgent()
    em_res = em.run(jobs=jobs, dry_run=dry_run, provider=args.provider)
    print_result(em_res)
    results.append(em_res)

    # Marchează status și push înapoi la n8n
    if em_res.status != AgentStatus.FAILED:
        for job in jobs:
            job["status"] = "GESENDET" if not dry_run else job.get("status", "Bereit")
            job["email_status"] = "GESENDET" if not dry_run else "DRY_RUN"
            job["gesendet_am"] = datetime.now().strftime("%Y-%m-%d") if not dry_run else ""
        push = n8n.run(action="push", jobs=jobs)
        print_result(push)
        results.append(push)

    return results
```

- [ ] **Step 5: Adaugă dual-write push în pipeline-ul existent**

În `main.py`, în interiorul lui `run_pipeline`, imediat după blocul STEP 6 (după actualizarea Tracker, linia ~252, înainte de STEP 7), adaugă:

```python
    # STEP 6b: Sync rezultate la n8n / Google Sheets (dual-write, non-blocant)
    if args.step in ("scrape", "analyze", "generate", "send", "full") and pipeline_jobs:
        print("\n☁ STEP 6b: Sync → n8n / Google Sheets...")
        n8n = N8nSyncAgent()
        n8n_result = n8n.run(action="push", jobs=pipeline_jobs)
        print_result(n8n_result)
        results.append(n8n_result)
```

- [ ] **Step 6: Adaugă `poll` în choices și dispecerizare**

În `main.py`, în `main()`, modifică argumentul `--step` (linia ~287) pentru a include `poll`:

```python
    parser.add_argument(
        "--step",
        choices=["scrape", "analyze", "generate", "organize", "verify", "send", "report", "full", "poll"],
        default="full",
        help="Step de executat (default: full)",
    )
```

Și în `main()`, înlocuiește `run_pipeline(args)` (linia ~302) cu dispecerizare:

```python
    try:
        if args.step == "poll":
            run_poll(args)
        else:
            run_pipeline(args)
    except KeyboardInterrupt:
        print("\n\n⏹ Pipeline oprit de utilizator.")
        sys.exit(130)
    except Exception as e:
        print(f"\n❌ Eroare fatală: {e}")
        sys.exit(1)
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `pytest tests/test_poll_step.py -v`
Expected: PASS (2 passed)

- [ ] **Step 8: Rulează întreaga suită (verifică zero regresie)**

Run: `pytest -q`
Expected: PASS — toate testele existente + cele noi verzi.

- [ ] **Step 9: Commit**

```bash
git add main.py tests/test_poll_step.py
git commit -m "feat(n8n): dual-write push + --step poll în orchestrator (JOB-14)"
```

---

### Task 5: Workflow-uri n8n (JSON) + script de deploy + dezactivare SSH

**Files:**
- Create: `n8n_workflows/jobapp_ingest.json`
- Create: `n8n_workflows/jobapp_pending.json`
- Create: `scripts/deploy_n8n_workflows.py`
- Create: `n8n_workflows/README.md`

**Interfaces:**
- Consumes: `.env` (`N8N_API_KEY`); n8n public API la `https://app.n8n-studio.com/api/v1`.
- Produces: 2 workflow-uri active în n8n + workflow-ul SSH vechi dezactivat.

> Notă: nodurile Google Sheets folosesc cred `lwLv81Aaab8h0Zm3`, doc `1p0u4XcMxOWex2-OTfIxnvpun3NVFkrqh3DvLWhRBkO0`, tab `Job Applications`. Secretul se verifică într-un nod Code pe `headers['x-jobapp-secret']`.

- [ ] **Step 1: Creează `n8n_workflows/jobapp_ingest.json`**

```json
{
  "name": "JobApp Ingest",
  "settings": { "executionOrder": "v1" },
  "nodes": [
    {
      "parameters": {
        "httpMethod": "POST",
        "path": "jobapp-ingest",
        "responseMode": "responseNode",
        "options": {}
      },
      "id": "wh-ingest",
      "name": "Webhook",
      "type": "n8n-nodes-base.webhook",
      "typeVersion": 2,
      "position": [260, 300],
      "webhookId": "jobapp-ingest"
    },
    {
      "parameters": {
        "jsCode": "const secret = $env.JOBAPP_SECRET || '';\nconst got = ($json.headers && $json.headers['x-jobapp-secret']) || '';\nif (!secret || got !== secret) { throw new Error('unauthorized'); }\nconst jobs = ($json.body && $json.body.jobs) || [];\nreturn jobs.map(j => ({ json: j }));"
      },
      "id": "code-ingest",
      "name": "Validate & Expand",
      "type": "n8n-nodes-base.code",
      "typeVersion": 2,
      "position": [480, 300]
    },
    {
      "parameters": {
        "operation": "appendOrUpdate",
        "documentId": { "__rl": true, "mode": "id", "value": "1p0u4XcMxOWex2-OTfIxnvpun3NVFkrqh3DvLWhRBkO0" },
        "sheetName": { "__rl": true, "mode": "name", "value": "Job Applications" },
        "columns": {
          "mappingMode": "autoMapInputData",
          "matchingColumns": ["URL"]
        },
        "options": {}
      },
      "id": "gs-upsert",
      "name": "Write to Google Sheets",
      "type": "n8n-nodes-base.googleSheets",
      "typeVersion": 4,
      "position": [700, 300],
      "credentials": { "googleSheetsOAuth2Api": { "id": "lwLv81Aaab8h0Zm3", "name": "Google Sheets account" } }
    },
    {
      "parameters": {
        "respondWith": "json",
        "responseBody": "={{ { \"ok\": true, \"upserted\": $items().length } }}"
      },
      "id": "respond-ingest",
      "name": "Respond",
      "type": "n8n-nodes-base.respondToWebhook",
      "typeVersion": 1,
      "position": [920, 300]
    }
  ],
  "connections": {
    "Webhook": { "main": [[{ "node": "Validate & Expand", "type": "main", "index": 0 }]] },
    "Validate & Expand": { "main": [[{ "node": "Write to Google Sheets", "type": "main", "index": 0 }]] },
    "Write to Google Sheets": { "main": [[{ "node": "Respond", "type": "main", "index": 0 }]] }
  }
}
```

- [ ] **Step 2: Creează `n8n_workflows/jobapp_pending.json`**

```json
{
  "name": "JobApp Pending",
  "settings": { "executionOrder": "v1" },
  "nodes": [
    {
      "parameters": {
        "httpMethod": "POST",
        "path": "jobapp-pending",
        "responseMode": "responseNode",
        "options": {}
      },
      "id": "wh-pending",
      "name": "Webhook",
      "type": "n8n-nodes-base.webhook",
      "typeVersion": 2,
      "position": [260, 300],
      "webhookId": "jobapp-pending"
    },
    {
      "parameters": {
        "jsCode": "const secret = $env.JOBAPP_SECRET || '';\nconst got = ($json.headers && $json.headers['x-jobapp-secret']) || '';\nif (!secret || got !== secret) { throw new Error('unauthorized'); }\nreturn [{ json: {} }];"
      },
      "id": "code-auth",
      "name": "Auth",
      "type": "n8n-nodes-base.code",
      "typeVersion": 2,
      "position": [460, 300]
    },
    {
      "parameters": {
        "operation": "read",
        "documentId": { "__rl": true, "mode": "id", "value": "1p0u4XcMxOWex2-OTfIxnvpun3NVFkrqh3DvLWhRBkO0" },
        "sheetName": { "__rl": true, "mode": "name", "value": "Job Applications" },
        "options": {}
      },
      "id": "gs-read",
      "name": "Read Sheet",
      "type": "n8n-nodes-base.googleSheets",
      "typeVersion": 4,
      "position": [660, 300],
      "credentials": { "googleSheetsOAuth2Api": { "id": "lwLv81Aaab8h0Zm3", "name": "Google Sheets account" } }
    },
    {
      "parameters": {
        "jsCode": "const rows = items.map(i => i.json);\nconst pending = rows.filter(r => String(r['Sende Befehl']||'').toUpperCase()==='SENDEN' && String(r['Email Status']||'').toUpperCase()!=='GESENDET');\nconst commands = pending.map(r => ({ action: 'send', URL: r['URL'], Firma: r['Firma'], Position: r['Position'], 'Kontakt Email': r['Kontakt Email'], Sprache: r['Sprache'], Anschreiben: r['Anschreiben'] }));\nreturn [{ json: { ok: true, commands } }];"
      },
      "id": "code-filter",
      "name": "Filter SENDEN",
      "type": "n8n-nodes-base.code",
      "typeVersion": 2,
      "position": [860, 300]
    },
    {
      "parameters": { "respondWith": "json", "responseBody": "={{ $json }}" },
      "id": "respond-pending",
      "name": "Respond",
      "type": "n8n-nodes-base.respondToWebhook",
      "typeVersion": 1,
      "position": [1060, 300]
    }
  ],
  "connections": {
    "Webhook": { "main": [[{ "node": "Auth", "type": "main", "index": 0 }]] },
    "Auth": { "main": [[{ "node": "Read Sheet", "type": "main", "index": 0 }]] },
    "Read Sheet": { "main": [[{ "node": "Filter SENDEN", "type": "main", "index": 0 }]] },
    "Filter SENDEN": { "main": [[{ "node": "Respond", "type": "main", "index": 0 }]] }
  }
}
```

- [ ] **Step 3: Creează `scripts/deploy_n8n_workflows.py`**

```python
#!/usr/bin/env python3
"""
Deploy workflow-urile JobApp în n8n via public API + dezactivează workflow-ul SSH vechi.

Citește N8N_API_KEY din .env. Idempotent: dacă un workflow cu același nume există,
îl actualizează în loc să-l dubleze.

Rulare:
    python3 scripts/deploy_n8n_workflows.py
"""
import json
import os
from pathlib import Path

import httpx

ROOT = Path(__file__).parent.parent.resolve()
BASE = "https://app.n8n-studio.com/api/v1"
SSH_WORKFLOW_ID = "naxm3lJSXipBoH5r"  # „Job Tracker → Google Sheets Sync" (stricat)
WORKFLOWS = ["jobapp_ingest.json", "jobapp_pending.json"]


def _api_key() -> str:
    for line in (ROOT / ".env").read_text().splitlines():
        if line.startswith("N8N_API_KEY="):
            return line.split("=", 1)[1].strip()
    raise SystemExit("N8N_API_KEY lipsă în .env")


def main():
    key = _api_key()
    h = {"X-N8N-API-KEY": key, "accept": "application/json", "content-type": "application/json"}

    existing = httpx.get(f"{BASE}/workflows?limit=200", headers=h, timeout=30).json().get("data", [])
    by_name = {w["name"]: w["id"] for w in existing}

    for fname in WORKFLOWS:
        wf = json.loads((ROOT / "n8n_workflows" / fname).read_text())
        name = wf["name"]
        if name in by_name:
            wid = by_name[name]
            r = httpx.put(f"{BASE}/workflows/{wid}", headers=h, json=wf, timeout=30)
            print(f"  ♻ update {name}: HTTP {r.status_code}")
        else:
            r = httpx.post(f"{BASE}/workflows", headers=h, json=wf, timeout=30)
            wid = r.json().get("id")
            print(f"  ➕ create {name}: HTTP {r.status_code} (id={wid})")
        # activează
        ra = httpx.post(f"{BASE}/workflows/{wid}/activate", headers=h, timeout=30)
        print(f"     activate: HTTP {ra.status_code}")

    # Dezactivează workflow-ul SSH stricat
    rd = httpx.post(f"{BASE}/workflows/{SSH_WORKFLOW_ID}/deactivate", headers=h, timeout=30)
    print(f"  ⏸ deactivate SSH sync: HTTP {rd.status_code}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Creează `n8n_workflows/README.md`**

```markdown
# n8n Workflows — JobApp

Două workflow-uri pentru integrarea Job_app ↔ Google Sheets:

- `jobapp_ingest.json` — webhook `POST /webhook/jobapp-ingest` → upsert în Sheet (match pe `URL`).
- `jobapp_pending.json` — webhook `POST /webhook/jobapp-pending` → rândurile `SENDEN`.

## Deploy
```bash
python3 scripts/deploy_n8n_workflows.py
```

## Pre-condiții în n8n
- Variabila de mediu `JOBAPP_SECRET` setată pe instanța n8n = valoarea din `.env::N8N_WEBHOOK_SECRET`.
- Credențialul Google Sheets `lwLv81Aaab8h0Zm3` valid; Sheet-ul `1p0u4...` are tab-ul `Job Applications` cu headerele germane pe rândul 1.

## Fallback (dacă activarea prin API eșuează)
Importă manual cele 2 fișiere JSON din UI (Import from File) și activează-le.
```

- [ ] **Step 5: Validează JSON-ul local (fără rețea)**

Run:
```bash
python3 -c "import json,glob; [json.load(open(f)) for f in glob.glob('n8n_workflows/*.json')]; print('JSON valid')"
```
Expected: `JSON valid`

- [ ] **Step 6: Deploy + smoke test (necesită rețea + JOBAPP_SECRET setat în n8n)**

Run:
```bash
python3 scripts/deploy_n8n_workflows.py
# smoke test pending (înlocuiește SECRET):
curl -sS -X POST https://app.n8n-studio.com/webhook/jobapp-pending \
  -H "X-JobApp-Secret: $N8N_WEBHOOK_SECRET" -H "content-type: application/json" \
  -d '{"action":"get_pending"}'
```
Expected: deploy → HTTP 200 pe create/activate; curl → JSON `{"ok":true,"commands":[...]}`.

> Dacă activarea prin API întoarce eroare (versiune n8n), folosește fallback-ul manual din README și re-rulează doar smoke test-ul.

- [ ] **Step 7: Commit**

```bash
git add n8n_workflows/ scripts/deploy_n8n_workflows.py
git commit -m "feat(n8n): workflow-uri ingest/pending + deploy script + deactivate SSH (JOB-15)"
```

---

### Task 6: Scheduling (launchd) + documentație

**Files:**
- Create: `scripts/com.jobapp.poll.plist`
- Modify: `CLAUDE.md` (secțiune nouă „n8n / Google Sheets" + comanda `poll`)
- Modify: `.env` (corectează host n8n; comentariu) — doar local, nu se comite (gitignored)

**Interfaces:**
- Consumes: `main.py --step poll` (Task 4).
- Produces: job launchd care rulează `poll` la `poll_interval_min`.

- [ ] **Step 1: Creează `scripts/com.jobapp.poll.plist`**

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.jobapp.poll</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/zsh</string>
    <string>-lc</string>
    <string>cd /Users/Geronimo/Projects/Job_app &amp;&amp; source .venv/bin/activate &amp;&amp; python3 main.py --step poll >> output/logs/poll_launchd.log 2>&amp;1</string>
  </array>
  <key>StartInterval</key>
  <integer>600</integer>
  <key>RunAtLoad</key>
  <false/>
</dict>
</plist>
```

- [ ] **Step 2: Documentează în `CLAUDE.md`**

În `CLAUDE.md`, după secțiunea „Rulare" (după blocul de comenzi `python3 main.py ...`), adaugă:

```markdown
### n8n / Google Sheets (integrare)

Trackerul e oglindit într-un Google Sheet prin n8n (sursă de adevăr = Sheet).
Python nu atinge Google direct; vorbește cu n8n via webhook.

```bash
python3 main.py --step poll        # Preia comenzile SENDEN din Sheet și trimite
python3 scripts/deploy_n8n_workflows.py   # (Re)deploy workflow-uri n8n
```

- Config: bloc `n8n:` în `config/settings.yaml` (`enabled`, `base_url`, `paths`, `poll_interval_min`).
- Secrete `.env`: `N8N_WEBHOOK_SECRET` (header webhook), `N8N_API_KEY` (doar deploy).
- Dezactivare integrare: `n8n.enabled: false` → pipeline-ul rulează doar cu Excel local.
- Polling automat: încarcă `scripts/com.jobapp.poll.plist` cu
  `launchctl load -w ~/Library/LaunchAgents/com.jobapp.poll.plist`.
```

- [ ] **Step 3: Corectează host-ul n8n în `.env` (local)**

În `.env`, asigură-te că workflow-urile/clientul folosesc HTTPS. Liniile `N8N_HOST/PORT`
descriu instanța veche; clientul Python folosește `config/settings.yaml::n8n.base_url`
(`https://app.n8n-studio.com`), deci nu depinde de `N8N_HOST`. Adaugă comentariul:

```
# NOTĂ: instanța n8n răspunde pe HTTPS/443 (https://app.n8n-studio.com).
# Runtime-ul Python folosește config/settings.yaml::n8n.base_url, nu N8N_HOST/PORT.
N8N_WEBHOOK_SECRET=<generează un token aleator, ex. openssl rand -hex 24>
```
Și setează aceeași valoare ca variabilă de mediu `JOBAPP_SECRET` pe instanța n8n.

- [ ] **Step 4: Verifică manual `--step poll` end-to-end (dry-run)**

Run:
```bash
source .venv/bin/activate
python3 main.py --step poll        # dry-run implicit
```
Expected: rulează fără excepție; raportează numărul de comenzi SENDEN (0 dacă Sheet-ul e gol)
și un sumar de agenți. Niciun email real trimis (dry-run).

- [ ] **Step 5: Commit**

```bash
git add scripts/com.jobapp.poll.plist CLAUDE.md
git commit -m "docs(n8n): scheduling launchd + documentație poll (JOB-16)"
```

---

## Self-Review (completat de autor)

**1. Spec coverage:**
- §3 arhitectură/dual-write → Task 4 (push) + Task 2/3 (client/agent). ✓
- §4 contract ingest/pending → Task 2 (client) + Task 5 (workflow-uri n8n). ✓
- §5 schema Sheet / mapping germane → Task 1 (mapping) + Task 5 (matchOn URL). ✓
- §6 componente Python (client, agent, poll, config, scheduling) → Task 2,3,4,6. ✓
- §7 workflow-uri n8n + dezactivare SSH → Task 5. ✓
- §8 securitate (secret, HTTPS) → Task 2 (header), Task 5 (Code auth). ✓
- §9 testare → Task 1-4 (unit) + Task 5/6 (smoke manual). ✓
- §11 criterii succes 1-6 → acoperite (poll gol = Task 6 step 4; SENDEN flow = Task 5/6; enabled=false = Task 4 step 8; SSH off = Task 5). ✓

**2. Placeholder scan:** fără TBD/„handle edge cases"; tot codul e complet. ✓

**3. Type consistency:** `job_to_sheet_row`/`sheet_row_to_job` (Task 1) folosite identic în Task 2/4; `N8nClient.ingest/get_pending` (Task 2) ↔ `N8nSyncAgent` (Task 3) ↔ `run_poll` (Task 4); cheile `commands`/`upserted` consistente între agent și teste. ✓
