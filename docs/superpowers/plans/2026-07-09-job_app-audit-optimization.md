# Job_app Audit & Optimization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the gaps found during a full-project audit — a crash-on-commit bug, two silent data-integrity bugs, one safety-relevant bug, missing IaC backups for live n8n workflows, stale docs, dead n8n workflow clutter, and no CI — without touching anything outside Job_app's own scope.

**Architecture:** No architectural change. Every task is a small, independently-committed fix or addition inside the existing `BaseAgent`/`AgentResult` pattern, the existing `n8n_workflows/` JSON-as-source-of-truth convention, or plain repo/CI hygiene.

**Tech Stack:** Python 3.12, pytest, httpx, n8n public REST API, GitHub Actions.

## Global Constraints

- Python 3.12+; code comments and commit messages in Romanian (project convention).
- Every commit tagged `JOB-NN`, sequential from `JOB-24` (last used: `JOB-23`).
- Dry-run stays default for email sending — none of these tasks change that.
- Config stays centralized in `config/settings.yaml`; secrets stay in `.env` only.
- Do not touch any n8n workflow belonging to another project on the shared `app.n8n-studio.com` instance (Rotocon, Rankenstein, LinkedIn content, stripe-orders, Keyword Research, etc.). In scope for this plan: the 4 active `JobApp*`-prefixed workflows (read-only in Task 9, backed up in Task 7) and the 4 specific legacy Job_app workflows Task 10 deletes by ID (`naxm3lJSXipBoH5r`, `4jF1tciI4Oj4lIZp`, `TUjxbhfZQwL1U3Mr`, `rw6lXL3JG85DIcmN` — approved for deletion earlier in this session; not `JobApp`-prefixed by name, but Job_app's own retired artifacts, not another project's).
- Do not call `jobapp-ingest` / `jobapp-upload` / `jobapp-trigger` live (they write to the real Sheet/Drive or could trigger a real send). Only `jobapp-pending` (read-only) may be called live, in Task 9.
- No changes to the real Google Sheet or to `data/Job_Application_Tracker.xlsx`.
- No refactor of the `BaseAgent`/`AgentResult` pattern.
- `sender/n8n_client.py::upload_doc` wiring into the pipeline is explicitly OUT of scope for this plan (see Task 8 note) — it needs a product decision about how `_row` is resolved for poll-sourced jobs, not a mechanical fix.

---

### Task 1: Fix `_extract_email` crash in `career_page_scraper.py`

**Context:** The currently-uncommitted diff in `scrapers/career_page_scraper.py` adds a call to `self._extract_email(soup, url, headers)` (line 129) but never defines that method. Verified via `grep -n "_extract_email" scrapers/career_page_scraper.py` — only the call site exists. This crashes with `AttributeError` for any studio without a hardcoded `email_hr` in `config/game_studios.yaml` — verified 14 of 23 studios (61%) have none. The exception is caught per-studio in `scrape()` so it doesn't kill the pipeline, but it silently zeroes out job discovery for those 14 studios. This must be fixed before the diff is committed.

**Files:**
- Modify: `scrapers/career_page_scraper.py`
- Create: `tests/test_career_page_scraper.py`

**Interfaces:**
- Produces: `CareerPageScraper._extract_email(soup, url, headers) -> str`, `CareerPageScraper._find_emails_in_soup(soup) -> list[str]`, `CareerPageScraper._find_impressum_link(soup, base_url) -> str`. Not consumed by any other task in this plan.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_career_page_scraper.py`:

```python
"""
Teste unitare pentru CareerPageScraper — extragere email din pagină/Impressum.
"""

from unittest.mock import MagicMock, patch

import pytest
from bs4 import BeautifulSoup

from scrapers.career_page_scraper import CareerPageScraper


@pytest.fixture
def scraper(mock_config, tmp_path):
    return CareerPageScraper(mock_config, tmp_path)


class TestExtractEmail:
    def test_finds_mailto_link(self, scraper):
        soup = BeautifulSoup(
            '<html><body><a href="mailto:bewerbung@studio.de">Kontakt</a></body></html>',
            "html.parser",
        )
        email = scraper._extract_email(soup, "https://studio.de/careers", {})
        assert email == "bewerbung@studio.de"

    def test_prefers_preferred_prefix_over_first_match(self, scraper):
        soup = BeautifulSoup(
            '<html><body>'
            '<a href="mailto:webmaster@studio.de">Web</a>'
            '<a href="mailto:karriere@studio.de">Karriere</a>'
            '</body></html>',
            "html.parser",
        )
        email = scraper._extract_email(soup, "https://studio.de/careers", {})
        assert email == "karriere@studio.de"

    def test_ignores_image_filename_false_positives(self, scraper):
        soup = BeautifulSoup(
            "<html><body><p>logo@2x.png ist ein bestand</p></body></html>",
            "html.parser",
        )
        email = scraper._extract_email(soup, "https://studio.de/careers", {})
        assert email == ""

    @patch("scrapers.career_page_scraper.httpx.get")
    def test_falls_back_to_impressum_page(self, mock_get, scraper):
        main_soup = BeautifulSoup(
            '<html><body><a href="/impressum">Impressum</a></body></html>',
            "html.parser",
        )
        impressum_response = MagicMock()
        impressum_response.text = '<html><body><a href="mailto:hr@studio.de">HR</a></body></html>'
        impressum_response.raise_for_status.return_value = None
        mock_get.return_value = impressum_response

        email = scraper._extract_email(main_soup, "https://studio.de/careers", {})

        assert email == "hr@studio.de"
        mock_get.assert_called_once()

    def test_returns_empty_when_nothing_found(self, scraper):
        soup = BeautifulSoup("<html><body><p>Keine Kontaktdaten hier.</p></body></html>", "html.parser")
        email = scraper._extract_email(soup, "https://studio.de/careers", {})
        assert email == ""
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `source .venv/bin/activate && python -m pytest tests/test_career_page_scraper.py -v`
Expected: FAIL/ERROR on every test with `AttributeError: 'CareerPageScraper' object has no attribute '_extract_email'`

- [ ] **Step 3: Implement the missing methods**

In `scrapers/career_page_scraper.py`, insert these three methods immediately after `_load_studios` (currently ends at line 74, right before `def scrape(self, ...)` at line 76):

```python
    def _extract_email(self, soup: "BeautifulSoup", url: str, headers: dict) -> str:
        """Extrage un email de contact din pagină; dacă lipsește, încearcă Impressum."""
        candidates = self._find_emails_in_soup(soup)

        if not candidates:
            impressum_url = self._find_impressum_link(soup, url)
            if impressum_url:
                try:
                    resp = httpx.get(impressum_url, headers=headers, timeout=15, follow_redirects=True)
                    resp.raise_for_status()
                    candidates = self._find_emails_in_soup(BeautifulSoup(resp.text, "html.parser"))
                except Exception:
                    candidates = []

        if not candidates:
            return ""

        for prefix in self.PREFERRED_PREFIXES:
            for email in candidates:
                if email.lower().split("@", 1)[0].startswith(prefix):
                    return email
        return candidates[0]

    def _find_emails_in_soup(self, soup: "BeautifulSoup") -> list[str]:
        """Caută adrese email în link-uri mailto: și în textul vizibil al paginii."""
        found: list[str] = []
        for a in soup.find_all("a", href=True):
            if a["href"].lower().startswith("mailto:"):
                addr = a["href"][7:].split("?", 1)[0].strip()
                if addr and addr not in found:
                    found.append(addr)
        for match in self.EMAIL_RE.finditer(soup.get_text(" ")):
            addr = match.group(0)
            if addr.lower().endswith(self._EMAIL_JUNK_SUFFIX) or addr in found:
                continue
            found.append(addr)
        return found

    def _find_impressum_link(self, soup: "BeautifulSoup", base_url: str) -> str:
        """Găsește link-ul spre pagina de Impressum, dacă există."""
        from urllib.parse import urljoin
        for a in soup.find_all("a", href=True):
            text = a.get_text(strip=True).lower()
            if "impressum" in text or "impressum" in a["href"].lower() or "imprint" in text:
                return urljoin(base_url, a["href"])
        return ""

```

- [ ] **Step 4: Run tests to verify they pass**

Run: `source .venv/bin/activate && python -m pytest tests/test_career_page_scraper.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Run full suite to check for regressions**

Run: `source .venv/bin/activate && python -m pytest -q`
Expected: all tests pass (98+ passed — 97 existing + 5 new, minus none broken)

- [ ] **Step 6: Commit**

```bash
git add scrapers/career_page_scraper.py tests/test_career_page_scraper.py
git commit -m "fix(scraper): implementează _extract_email lipsă — crash pe 61% din studiouri (JOB-24)"
```

---

### Task 2: Fix silent `gefunden_am` data loss in n8n sync

**Context:** `sender/n8n_mapping.py:39` sets `row["Datum gefunden"] = job.get("gefunden_am", "")` outside `FIELD_MAP`. No scraper ever sets `gefunden_am` — verified via `grep -rn "gefunden_am" scrapers/ agents/ sender/ main.py`, only hit is that one read site. Because `FIELD_MAP` has no entry for it, `sheet_row_to_job` (which derives its reverse map from `FIELD_MAP`) silently drops "Datum gefunden" when reading pending commands back from n8n. Since the live `jobapp_ingest.json` workflow uses `autoMapInputData`, every push writes whatever key it's given — including blank — so any value already in the Sheet's "Datum gefunden" column gets overwritten with blank on every sync.

**Files:**
- Modify: `sender/n8n_mapping.py`
- Modify: `scrapers/career_page_scraper.py`
- Modify: `scrapers/jobspy_scraper.py`
- Test: `tests/test_n8n_mapping.py`
- Test: `tests/test_career_page_scraper.py`
- Create: `tests/test_jobspy_scraper.py`

**Interfaces:**
- Consumes: `CareerPageScraper._scrape_studio` job dict shape from Task 1 (unaffected by Task 1's changes — same dict keys).
- Produces: `gefunden_am` key now present in every job dict produced by both scrapers, and round-tripped correctly through `job_to_sheet_row`/`sheet_row_to_job`. Not consumed by later tasks.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_n8n_mapping.py`:

```python


def test_job_to_sheet_row_maps_gefunden_am():
    row = job_to_sheet_row({"url": "u", "gefunden_am": "2026-07-09"})
    assert row["Datum gefunden"] == "2026-07-09"


def test_sheet_row_to_job_roundtrips_gefunden_am():
    job = sheet_row_to_job({"Apply URL": "u", "Datum gefunden": "2026-07-09"})
    assert job["gefunden_am"] == "2026-07-09"
```

Append to `tests/test_career_page_scraper.py` (new class, after `TestExtractEmail`):

```python


class TestScrapeStudioMetadata:
    @patch("scrapers.career_page_scraper.httpx.get")
    def test_scrape_studio_sets_gefunden_am(self, mock_get, scraper):
        response = MagicMock()
        response.text = '<html><body><a href="/jobs/python-dev">Python Developer Position</a></body></html>'
        response.raise_for_status.return_value = None
        mock_get.return_value = response

        jobs = scraper._scrape_studio({
            "name": "TestStudio",
            "career_url": "https://teststudio.de/jobs",
            "email_hr": "hr@teststudio.de",
        })

        assert len(jobs) == 1
        assert "gefunden_am" in jobs[0]
        import re
        assert re.match(r"^\d{4}-\d{2}-\d{2}$", jobs[0]["gefunden_am"])
```

Create `tests/test_jobspy_scraper.py`:

```python
"""
Teste unitare pentru JobSpyScraper._normalize_job — funcție pură, fără rețea.
"""

import re

from scrapers.jobspy_scraper import JobSpyScraper


def test_normalize_job_includes_gefunden_am(mock_config, tmp_path):
    scraper = JobSpyScraper(mock_config, tmp_path)
    row = {
        "job_url": "https://example.com/job/1",
        "title": "Python Developer",
        "company": "TestCo",
        "site": "indeed",
    }

    job = scraper._normalize_job(row, keyword="Python", location="Berlin")

    assert job is not None
    assert "gefunden_am" in job
    assert re.match(r"^\d{4}-\d{2}-\d{2}$", job["gefunden_am"])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `source .venv/bin/activate && python -m pytest tests/test_n8n_mapping.py tests/test_career_page_scraper.py tests/test_jobspy_scraper.py -v`
Expected: the 4 new tests FAIL (`KeyError: 'gefunden_am'` / `AssertionError`), existing tests in those files still pass.

- [ ] **Step 3: Fix `sender/n8n_mapping.py`**

Change `FIELD_MAP` — add one entry after `"language": "Sprache",`:

```python
FIELD_MAP = {
    "company": "Firma",
    "title": "Position",
    "location": "Standort",
    "url": "Apply URL",
    "source": "Quelle",
    "email_hr": "Kontakt Email",
    "contact_person": "Kontakt Person",
    "language": "Sprache",
    "gefunden_am": "Datum gefunden",
    "status": "Status",
    "cover_letter": "Anschreiben PDF",
    "gesendet_am": "Gesendet am",
    "email_status": "Email Status",
    "notizen": "Notizen",
}
```

Remove the now-redundant line from `job_to_sheet_row` (the loop over `FIELD_MAP` already handles it):

```python
def job_to_sheet_row(job: dict) -> dict:
    """Convertește un job intern într-un rând pentru Sheet (chei germane)."""
    row: dict = {}
    for internal_key, german in FIELD_MAP.items():
        row[german] = job.get(internal_key, "")

    # Câmpuri derivate din analiză (doar coloane care există în Sheet)
    analysis = job.get("analysis", {}) or {}
    score = analysis.get("match_score", job.get("match_score", ""))
    row["Match Score"] = score if score is not None else ""

    return row
```

(i.e. delete the line `row["Datum gefunden"] = job.get("gefunden_am", "")` that used to precede `return row`.)

- [ ] **Step 4: Set `gefunden_am` in `scrapers/career_page_scraper.py`**

In the job dict inside `_scrape_studio` (the block starting `job = {`), add one line after `"scraped_at": datetime.now().isoformat(),`:

```python
                job = {
                    "id": f"career_{name}_{hash(href)}",
                    "source": "career_page",
                    "platform": name,
                    "title": text[:200],
                    "company": name,
                    "location": studio.get("location", ""),
                    "url": href,
                    "description": "",
                    "email_hr": email,
                    "language": studio.get("language", "en"),
                    "scraped_at": datetime.now().isoformat(),
                    "gefunden_am": datetime.now().strftime("%Y-%m-%d"),
                    "applied": False,
                    "status": "new",
                }
```

- [ ] **Step 5: Set `gefunden_am` in `scrapers/jobspy_scraper.py`**

In `_normalize_job`'s returned dict, add one line after `"scraped_at": datetime.now().isoformat(),`:

```python
            return {
                "id": f"jobspy_{hash(str(row.get('job_url', '')))}",
                "source": "jobspy",
                "platform": str(row.get("site", "unknown")),
                "title": str(row.get("title", "")),
                "company": str(row.get("company", "")),
                "location": str(row.get("location", location)),
                "url": str(row.get("job_url", "")),
                "description": str(row.get("description", ""))[:5000],
                "salary_min": row.get("min_amount"),
                "salary_max": row.get("max_amount"),
                "date_posted": str(row.get("date_posted", "")),
                "scraped_at": datetime.now().isoformat(),
                "gefunden_am": datetime.now().strftime("%Y-%m-%d"),
                "search_keyword": keyword,
                "search_location": location,
                "applied": False,
                "status": "new",
            }
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `source .venv/bin/activate && python -m pytest tests/test_n8n_mapping.py tests/test_career_page_scraper.py tests/test_jobspy_scraper.py -v`
Expected: PASS (all tests in all three files)

- [ ] **Step 7: Run full suite to check for regressions**

Run: `source .venv/bin/activate && python -m pytest -q`
Expected: all tests pass

- [ ] **Step 8: Commit**

```bash
git add sender/n8n_mapping.py scrapers/career_page_scraper.py scrapers/jobspy_scraper.py \
        tests/test_n8n_mapping.py tests/test_career_page_scraper.py tests/test_jobspy_scraper.py
git commit -m "fix(n8n): mapare Datum gefunden în FIELD_MAP + populează la scrape (JOB-25)"
```

---

### Task 3: Fix email rate-limit not surviving across poll processes

**Context:** `sender/smtp_sender.py` tracks `self._sent_today` as an in-memory counter (line 30), never persisted. Confirmed no file/DB write anywhere in the file (`grep -n "_sent_today" sender/smtp_sender.py` → only the 3 in-class hits). `EmailAgent` — and therefore a fresh `SmtpSender` — is constructed from scratch on every `main.py` invocation. `scripts/com.jobapp.poll.plist` runs `python3 main.py --step poll` every `StartInterval=600` (10 min) as a brand-new process each time (confirmed in the plist). So `max_emails_per_day: 10` (settings.yaml) is enforced per-process, not per-day — the real ceiling across a day is effectively unbounded for an automated outbound-email system.

**Files:**
- Modify: `sender/smtp_sender.py`
- Create: `tests/test_smtp_sender.py`

**Interfaces:**
- Produces: `SmtpSender._load_sent_today() -> int`, `SmtpSender._save_sent_today() -> None`, `SmtpSender._rate_state_file: Path` (`output/logs/email_rate_state.json`). Not consumed by later tasks.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_smtp_sender.py`:

```python
"""
Teste unitare pentru SmtpSender — rate limiting persistat pe disc între procese.
"""

import json
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `source .venv/bin/activate && python -m pytest tests/test_smtp_sender.py -v`
Expected: `test_fresh_sender_has_zero_sent_today` passes trivially (already true); the other 4 FAIL — no `email_rate_state.json` gets written since nothing calls it yet.

- [ ] **Step 3: Implement persistence in `sender/smtp_sender.py`**

Replace the whole import block at the top of the file (adds `import json` and `from datetime import datetime`, keeps everything else unchanged):

```python
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
```

Replace `__init__`:

```python
    def __init__(self, config: dict, project_root: Path):
        self.config = config
        self.project_root = project_root
        self.email_config = config.get("email", {})
        self.applicant = config.get("applicant", {})
        self._password_cache: dict[str, str] = {}
        self._rate_state_file = project_root / "output" / "logs" / "email_rate_state.json"
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
```

In `send()`, right after `self._sent_today += 1`:

```python
            self._sent_today += 1
            self._save_sent_today()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `source .venv/bin/activate && python -m pytest tests/test_smtp_sender.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Run full suite to check for regressions**

Run: `source .venv/bin/activate && python -m pytest -q`
Expected: all tests pass

- [ ] **Step 6: Commit**

```bash
git add sender/smtp_sender.py tests/test_smtp_sender.py
git commit -m "fix(email): persistă rate-limit zilnic pe disc între procese poll (JOB-26)"
```

---

### Task 4: Fix `DocumentAgent` cleanup reaching into `.venv`

**Context:** `agents/document_agent.py:244-252` (`_cleanup_temp_files`) runs `self.project_root.rglob("__pycache__")` and `rglob("*.tmp")` with no exclusions. `.venv/` lives directly under `project_root` (confirmed: `source .venv/bin/activate` is the documented activation path from repo root). Calling `DocumentAgent().run(action="cleanup")` deletes `__pycache__` for every installed dependency under `.venv/lib/python3.12/site-packages/**`, not just the project's own temp files.

**Files:**
- Modify: `agents/document_agent.py`
- Test: `tests/test_document_agent.py`

**Interfaces:**
- Produces: `DocumentAgent._CLEANUP_EXCLUDE: set[str]`, `DocumentAgent._is_excluded(path) -> bool`. Not consumed by later tasks.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_document_agent.py` (inside `class TestDocumentAgent`, add as a new method — note the import for `patch` already exists at the top of the file):

```python

    def test_cleanup_skips_venv_directory(self, tmp_project):
        """Cleanup nu trebuie să șteargă __pycache__ din .venv (doar fișierele proiectului)."""
        venv_pycache = tmp_project / ".venv" / "lib" / "somepkg" / "__pycache__"
        venv_pycache.mkdir(parents=True)
        (venv_pycache / "mod.cpython-312.pyc").touch()

        project_pycache = tmp_project / "agents" / "__pycache__"
        project_pycache.mkdir(parents=True)
        (project_pycache / "mod.cpython-312.pyc").touch()

        with patch("agents.base_agent.PROJECT_ROOT", tmp_project):
            agent = DocumentAgent()
            agent.logger.setLevel("CRITICAL")
            result = agent.run(action="cleanup")

        assert venv_pycache.exists()
        assert not project_pycache.exists()
        assert result.status == AgentStatus.SUCCESS
```

- [ ] **Step 2: Run test to verify it fails**

Run: `source .venv/bin/activate && python -m pytest tests/test_document_agent.py::TestDocumentAgent::test_cleanup_skips_venv_directory -v`
Expected: FAIL — `assert venv_pycache.exists()` fails because the current code deletes it too.

- [ ] **Step 3: Implement the exclusion in `agents/document_agent.py`**

Add a class attribute right after the class docstring, before `__init__`:

```python
class DocumentAgent(BaseAgent):
    """Agent pentru organizare și management documente."""

    _CLEANUP_EXCLUDE = {".venv", "venv", ".git", "node_modules"}

    def __init__(self):
```

Replace `_cleanup_temp_files` (and add `_is_excluded` right before it):

```python
    def _is_excluded(self, path: Path) -> bool:
        """True dacă path-ul trece prin .venv/.git/node_modules — nu curățăm dependințe."""
        return any(part in self._CLEANUP_EXCLUDE for part in path.parts)

    def _cleanup_temp_files(self, **kwargs) -> AgentResult:
        """Curăță fișierele temporare (.tmp, __pycache__, etc.) — exclude .venv/.git."""
        result = AgentResult(agent_name=self.name, status=AgentStatus.RUNNING)
        cleaned = []

        # .tmp files
        for tmp in self.project_root.rglob("*.tmp"):
            if self._is_excluded(tmp):
                continue
            tmp.unlink()
            cleaned.append(str(tmp))
            self.logger.info(f"  🗑 Șters: {tmp.name}")

        # __pycache__
        for cache in self.project_root.rglob("__pycache__"):
            if self._is_excluded(cache):
                continue
            shutil.rmtree(cache)
            cleaned.append(str(cache))

        result.data = {"cleaned": cleaned}
        result.items_processed = len(cleaned)
        result.items_succeeded = len(cleaned)
        result.status = AgentStatus.SUCCESS
        result.message = f"{len(cleaned)} fișiere temporare șterse"
        return result
```

- [ ] **Step 4: Run test to verify it passes**

Run: `source .venv/bin/activate && python -m pytest tests/test_document_agent.py -v`
Expected: PASS (all tests in file, including the new one)

- [ ] **Step 5: Run full suite to check for regressions**

Run: `source .venv/bin/activate && python -m pytest -q`
Expected: all tests pass

- [ ] **Step 6: Commit**

```bash
git add agents/document_agent.py tests/test_document_agent.py
git commit -m "fix(cleanup): exclude .venv/.git din DocumentAgent cleanup (JOB-27)"
```

---

### Task 5: Remove no-op `--use-ai` CLI flag

**Context:** In `main.py`, `--use-ai` defaults to `True` and nothing ever sets it `False` except `--no-ai` explicitly overriding it afterward — the flag itself is unreachable dead surface. Confirmed via `grep -rn "use.ai\|use_ai"` that `args.use_ai` (the attribute, not the CLI flag) is used once downstream (`main.py:192`) and that `use_ai` as an `AnalyzerAgent.run()` keyword (unrelated name, unaffected) is exercised throughout the test suite — this change touches only the `argparse` registration in `main()`, nothing else.

**Files:**
- Modify: `main.py`

**Interfaces:**
- None — standalone CLI cleanup, `args.use_ai` attribute keeps the same name/type downstream.

- [ ] **Step 1: Edit `main.py`**

Replace (currently lines 366-374):

```python
    parser.add_argument("--live", action="store_true", help="Trimite emailuri efectiv (implicit: dry-run)")
    parser.add_argument("--provider", choices=["gmx", "brevo"], default=None, help="SMTP provider")
    parser.add_argument("--min-score", type=int, default=30, help="Scor minim de match (default: 30)")
    parser.add_argument("--use-ai", action="store_true", default=True, help="Folosește AI pentru analiză")
    parser.add_argument("--no-ai", action="store_true", help="Dezactivează AI")

    args = parser.parse_args()
    if args.no_ai:
        args.use_ai = False
```

with:

```python
    parser.add_argument("--live", action="store_true", help="Trimite emailuri efectiv (implicit: dry-run)")
    parser.add_argument("--provider", choices=["gmx", "brevo"], default=None, help="SMTP provider")
    parser.add_argument("--min-score", type=int, default=30, help="Scor minim de match (default: 30)")
    parser.add_argument("--no-ai", action="store_true", help="Dezactivează AI (implicit: AI activ)")

    args = parser.parse_args()
    args.use_ai = not args.no_ai
```

- [ ] **Step 2: Verify manually**

Run: `source .venv/bin/activate && python3 main.py --help`
Expected: help text no longer lists `--use-ai`, still lists `--no-ai`.

Run: `source .venv/bin/activate && python3 -c "
import sys; sys.argv = ['main.py', '--step', 'report']
from main import main
"`
(This just confirms the module still imports and argparses cleanly — `--step report` doesn't need AI/SMTP secrets.)
Expected: no traceback from argument parsing.

- [ ] **Step 3: Run full suite to check for regressions**

Run: `source .venv/bin/activate && python -m pytest -q`
Expected: all tests pass (in particular `tests/test_poll_step.py`, which builds `SimpleNamespace(..., use_ai=True)` directly and never touches argparse)

- [ ] **Step 4: Commit**

```bash
git add main.py
git commit -m "chore(cli): elimină flag-ul no-op --use-ai (JOB-28)"
```

---

### Task 6: Commit the tracked Apps Script source

**Context:** `scripts/jobapp_appscript.gs` is the production Apps Script source (bound to the live Sheet, front-end trigger for the "send"/"Danke" workflow). It currently sits untracked in the working tree. CLAUDE.md's current claim that it's "NOT in repo" becomes stale the moment this is committed — Task 7 fixes that claim.

**Files:**
- Create (in git): `scripts/jobapp_appscript.gs` (already exists on disk, untracked)

**Interfaces:**
- None.

- [ ] **Step 1: Commit**

```bash
git add scripts/jobapp_appscript.gs
git commit -m "docs(n8n): adaugă sursa Apps Script trackuită (JOB-29)"
```

- [ ] **Step 2: Verify**

Run: `git status --porcelain scripts/`
Expected: no output (clean).

---

### Task 7: Back up `JobApp Trigger` + `JobApp Upload` n8n workflows, update deploy script & README

**Context:** The live n8n instance (`app.n8n-studio.com`) has 4 active `JobApp*` workflows — Ingest, Pending, Trigger, Upload — confirmed via `GET /api/v1/workflows`. Only Ingest and Pending have JSON backups in `n8n_workflows/`; Trigger and Upload (added in commits JOB-21/22) have none, and `scripts/deploy_n8n_workflows.py` doesn't know about them either — if either is lost/edited destructively in the n8n UI, there's currently no way to redeploy from git. Full live definitions were fetched and confirmed via `GET /api/v1/workflows/{id}` for both `cINDCLxxAeFi2UrB` (Trigger) and `WBiiK6V89Lc8KX6z` (Upload); the JSON below is those definitions trimmed to the same minimal shape already used by `jobapp_ingest.json`/`jobapp_pending.json` (name/settings/nodes/connections only — no volatile `id`/`versionId`/`createdAt`/`tags`/`pinData`).

Also fixes: `n8n_workflows/README.md`'s "Pre-condiții" section currently describes a `JOBAPP_SECRET` n8n environment variable and Sheet `1p0u4XcMxOWex2-OTfIxnvpun3NVFkrqh3DvLWhRBkO0` / tab "Job Applications" — both stale. The live workflows actually use a Header Auth credential (`JobApp Webhook Secret`, id `Ko0T1HawgzBeFN0s`) and Sheet `1fTjvs6GEYTzfwuOVsoJh-KAWW3EAB_bxV3m7UM1WM6M` / tab "Bewerbungen" (confirmed directly from the live `jobapp_ingest.json` fetch and CLAUDE.md).

Also removes: `deploy_n8n_workflows.py`'s SSH-workflow deactivation step (`SSH_WORKFLOW_ID = "naxm3lJSXipBoH5r"`) — that workflow gets deleted outright in Task 10, so a future deploy run would otherwise 404 on that deactivate call.

**Files:**
- Create: `n8n_workflows/jobapp_trigger.json`
- Create: `n8n_workflows/jobapp_upload.json`
- Modify: `scripts/deploy_n8n_workflows.py`
- Modify: `n8n_workflows/README.md`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `n8n_workflows/` now contains all 4 live workflow definitions; `deploy_n8n_workflows.py`'s `WORKFLOWS` list now has 4 entries. Task 10 depends on this being committed first (backups must exist before the dead workflows are deleted).

- [ ] **Step 1: Create `n8n_workflows/jobapp_trigger.json`**

```json
{
  "name": "JobApp Trigger",
  "settings": { "executionOrder": "v1" },
  "nodes": [
    {
      "parameters": {
        "httpMethod": "POST",
        "path": "jobapp-trigger",
        "responseMode": "responseNode",
        "options": {}
      },
      "id": "wh",
      "name": "Webhook",
      "type": "n8n-nodes-base.webhook",
      "typeVersion": 2,
      "position": [260, 300],
      "webhookId": "jobapp-trigger"
    },
    {
      "parameters": {
        "respondWith": "json",
        "responseBody": "={{ { \"ok\": true, \"pong\": ($json.body.action === 'ping'), \"action\": $json.body.action, \"received\": true } }}"
      },
      "id": "resp",
      "name": "Respond",
      "type": "n8n-nodes-base.respondToWebhook",
      "typeVersion": 1,
      "position": [480, 300]
    }
  ],
  "connections": {
    "Webhook": { "main": [[{ "node": "Respond", "type": "main", "index": 0 }]] }
  }
}
```

- [ ] **Step 2: Create `n8n_workflows/jobapp_upload.json`**

```json
{
  "name": "JobApp Upload",
  "settings": { "executionOrder": "v1" },
  "nodes": [
    {
      "parameters": {
        "httpMethod": "POST",
        "path": "jobapp-upload",
        "authentication": "headerAuth",
        "responseMode": "responseNode",
        "options": {}
      },
      "id": "Webhook",
      "name": "Webhook",
      "type": "n8n-nodes-base.webhook",
      "typeVersion": 2,
      "position": [200, 300],
      "webhookId": "jobapp-upload",
      "credentials": { "httpHeaderAuth": { "id": "Ko0T1HawgzBeFN0s", "name": "JobApp Webhook Secret" } }
    },
    {
      "parameters": {
        "jsCode": "const b=$json.body||{};\nreturn [{ json:{ filename:b.filename, row:b.row, apply_url:b.apply_url }, binary:{ data:{ data:b.content_base64, mimeType:'application/pdf', fileName:b.filename } } }];"
      },
      "id": "Prep",
      "name": "Prep",
      "type": "n8n-nodes-base.code",
      "typeVersion": 2,
      "position": [400, 300]
    },
    {
      "parameters": {
        "authentication": "predefinedCredentialType",
        "nodeCredentialType": "googleSheetsOAuth2Api",
        "method": "POST",
        "url": "https://www.googleapis.com/upload/drive/v3/files?uploadType=media&fields=id",
        "sendBody": true,
        "contentType": "binaryData",
        "inputDataFieldName": "data",
        "sendHeaders": true,
        "specifyHeaders": "keypair",
        "headerParameters": {
          "parameters": [
            { "name": "Content-Type", "value": "application/pdf" }
          ]
        },
        "options": {}
      },
      "id": "DriveUpload",
      "name": "DriveUpload",
      "type": "n8n-nodes-base.httpRequest",
      "typeVersion": 4.2,
      "position": [600, 300],
      "credentials": { "googleSheetsOAuth2Api": { "id": "lwLv81Aaab8h0Zm3", "name": "Google Sheets account" } }
    },
    {
      "parameters": {
        "authentication": "predefinedCredentialType",
        "nodeCredentialType": "googleSheetsOAuth2Api",
        "method": "PATCH",
        "url": "=https://www.googleapis.com/drive/v3/files/{{ $json.id }}?fields=id,webViewLink",
        "sendBody": true,
        "contentType": "json",
        "specifyBody": "json",
        "jsonBody": "={{ JSON.stringify({ name: $('Prep').item.json.filename }) }}",
        "options": {}
      },
      "id": "DriveRename",
      "name": "DriveRename",
      "type": "n8n-nodes-base.httpRequest",
      "typeVersion": 4.2,
      "position": [800, 300],
      "credentials": { "googleSheetsOAuth2Api": { "id": "lwLv81Aaab8h0Zm3", "name": "Google Sheets account" } }
    },
    {
      "parameters": {
        "authentication": "predefinedCredentialType",
        "nodeCredentialType": "googleSheetsOAuth2Api",
        "method": "PUT",
        "url": "=https://sheets.googleapis.com/v4/spreadsheets/1fTjvs6GEYTzfwuOVsoJh-KAWW3EAB_bxV3m7UM1WM6M/values/Bewerbungen!O{{ $('Prep').item.json.row }}?valueInputOption=RAW",
        "sendBody": true,
        "contentType": "json",
        "specifyBody": "json",
        "jsonBody": "={{ JSON.stringify({ values: [[ $json.webViewLink ]] }) }}",
        "options": {}
      },
      "id": "SheetWrite",
      "name": "SheetWrite",
      "type": "n8n-nodes-base.httpRequest",
      "typeVersion": 4.2,
      "position": [1000, 300],
      "credentials": { "googleSheetsOAuth2Api": { "id": "lwLv81Aaab8h0Zm3", "name": "Google Sheets account" } }
    },
    {
      "parameters": {
        "respondWith": "json",
        "responseBody": "={{ { ok:true, link: $('DriveRename').item.json.webViewLink, fileId: $('DriveRename').item.json.id } }}"
      },
      "id": "Respond",
      "name": "Respond",
      "type": "n8n-nodes-base.respondToWebhook",
      "typeVersion": 1,
      "position": [1200, 300]
    }
  ],
  "connections": {
    "Webhook": { "main": [[{ "node": "Prep", "type": "main", "index": 0 }]] },
    "Prep": { "main": [[{ "node": "DriveUpload", "type": "main", "index": 0 }]] },
    "DriveUpload": { "main": [[{ "node": "DriveRename", "type": "main", "index": 0 }]] },
    "DriveRename": { "main": [[{ "node": "SheetWrite", "type": "main", "index": 0 }]] },
    "SheetWrite": { "main": [[{ "node": "Respond", "type": "main", "index": 0 }]] }
  }
}
```

- [ ] **Step 3: Update `scripts/deploy_n8n_workflows.py`**

Replace the whole file:

```python
#!/usr/bin/env python3
"""
Deploy workflow-urile JobApp în n8n via public API.

Citește N8N_API_KEY din .env. Idempotent: dacă un workflow cu același nume există,
îl actualizează în loc să-l dubleze.

Rulare:
    python3 scripts/deploy_n8n_workflows.py
"""
import json
from pathlib import Path

import httpx

ROOT = Path(__file__).parent.parent.resolve()
BASE = "https://app.n8n-studio.com/api/v1"
WORKFLOWS = ["jobapp_ingest.json", "jobapp_pending.json", "jobapp_upload.json", "jobapp_trigger.json"]


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
            if r.status_code >= 300:
                raise SystemExit(f"  ❌ update {name} eșuat: HTTP {r.status_code} — {r.text[:200]}")
            print(f"  ♻ update {name}: HTTP {r.status_code}")
        else:
            r = httpx.post(f"{BASE}/workflows", headers=h, json=wf, timeout=30)
            if r.status_code >= 300:
                raise SystemExit(f"  ❌ create {name} eșuat: HTTP {r.status_code} — {r.text[:200]}")
            wid = r.json().get("id")
            if not wid:
                raise SystemExit(f"  ❌ create {name}: răspuns fără id — {r.text[:200]}")
            print(f"  ➕ create {name}: HTTP {r.status_code} (id={wid})")
        # activează
        ra = httpx.post(f"{BASE}/workflows/{wid}/activate", headers=h, timeout=30)
        print(f"     activate: HTTP {ra.status_code}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Update `n8n_workflows/README.md`**

Replace the whole file:

```markdown
# n8n Workflows — JobApp

Patru workflow-uri pentru integrarea Job_app ↔ Google Sheets, deploiate pe
`https://app.n8n-studio.com` (instanță n8n personală, comună mai multor proiecte —
nu atinge workflow-uri fără prefixul `JobApp`):

- `jobapp_ingest.json` — webhook `POST /webhook/jobapp-ingest` → upsert în Sheet (match pe `Apply URL`).
- `jobapp_pending.json` — webhook `POST /webhook/jobapp-pending` → rândurile `SENDEN`.
- `jobapp_upload.json` — webhook `POST /webhook/jobapp-upload` → urcă PDF în Google Drive + scrie link-ul în col O „Anschreiben PDF". Implementat și testat (`sender/n8n_client.py::upload_doc`), dar **deocamdată neapelat din pipeline** — niciun agent nu-l invocă încă (motivul concret pentru care execuțiile live pe acest workflow sunt zero).
- `jobapp_trigger.json` — webhook `POST /webhook/jobapp-trigger` → stub ping/pong. **Nu e apelat de nimic azi**: Python nu are client pentru el, iar Apps Script-ul lovește `job.n8n-studio.com/trigger` (alt serviciu, FastAPI pe VPS). Păstrat activ + în git pentru istoric/eventuală reutilizare.

## Deploy
```bash
python3 scripts/deploy_n8n_workflows.py
```

## Pre-condiții în n8n
- Credențialul **Header Auth** `JobApp Webhook Secret` (id `Ko0T1HawgzBeFN0s`) trebuie să aibă
  valoarea header-ului = `.env::N8N_WEBHOOK_SECRET`. (`$env` e blocat în nodurile Code pe
  această instanță, de-aia Header Auth nativ și nu o variabilă de mediu n8n.)
- Credențialul Google Sheets `lwLv81Aaab8h0Zm3` valid; Sheet-ul
  `1fTjvs6GEYTzfwuOVsoJh-KAWW3EAB_bxV3m7UM1WM6M`, tab `Bewerbungen`, headerele germane pe rândul 1.

## Fallback (dacă activarea prin API eșuează)
Importă manual cele 4 fișiere JSON din UI (Import from File) și activează-le.

## Istoric curățenie
- 2026-07-09 (audit JOB-23): șterse 4 workflow-uri n8n inactive/abandonate — 2× „Job Tracker →
  Google Sheets Sync" (SSH, retras), „Automate LinkedIn job search... Easy-Apply" (prototip
  separat, altă arhitectură), „Bewerbung Email Sender — Brevo" (alternativă manuală, nefolosită;
  trimiterea rămâne în Python via `sender/smtp_sender.py`).
```

- [ ] **Step 5: Verify JSON is well-formed**

Run: `python3 -c "import json; json.load(open('n8n_workflows/jobapp_trigger.json')); json.load(open('n8n_workflows/jobapp_upload.json')); print('OK')"`
Expected: `OK`

- [ ] **Step 6: Run full suite to check for regressions**

Run: `source .venv/bin/activate && python -m pytest -q`
Expected: all tests pass (none of these files are imported by the test suite, this is a sanity check)

- [ ] **Step 7: Commit**

```bash
git add n8n_workflows/jobapp_trigger.json n8n_workflows/jobapp_upload.json \
        scripts/deploy_n8n_workflows.py n8n_workflows/README.md
git commit -m "feat(n8n): backup JobApp Trigger+Upload + update deploy script (JOB-30)"
```

---

### Task 8: Update CLAUDE.md to reflect current reality

**Context:** Two claims in CLAUDE.md are now stale. First, "Apps Script-ul (bound la Sheet, NU în repo)" — false after Task 6. Second, the n8n workflow inventory line lists only `JobApp Ingest/Pending` + `JobApp Trigger (endpoint Apps Script)` — incomplete (missing `JobApp Upload`) and factually wrong about Trigger (it is NOT the Apps Script endpoint; that's the separate `job.n8n-studio.com/trigger` FastAPI service — confirmed via `grep -rn "trigger"` returning zero Python call sites for the n8n trigger path, and the Apps Script source itself hardcodes `WEBHOOK_URL = "https://job.n8n-studio.com/trigger"`).

**Files:**
- Modify: `CLAUDE.md`

**Interfaces:**
- Consumes: state produced by Tasks 6 and 7 (this task documents the end-state of both).

- [ ] **Step 1: Fix the Apps Script line**

In the "Două sisteme de trigger pe ACELAȘI Sheet" section, replace:

```
Apps Script-ul (bound la Sheet, NU în repo) e front-end-ul producției: declanșează și pe URL paste în col E (scrape) și pe col AB (Danke). La eșec scrie `error` în M + motivul în col W.
```

with:

```
Apps Script-ul (bound la Sheet, sursă trackuită la `scripts/jobapp_appscript.gs`) e front-end-ul producției: declanșează și pe URL paste în col E (scrape) și pe col AB (Danke). La eșec scrie `error` în M + motivul în col W.
```

- [ ] **Step 2: Fix the n8n workflow inventory line**

In the "n8n — detalii critice" section, replace:

```
- Workflow-uri: `JobApp Ingest`/`Pending` (upsert/read Sheet), `JobApp Trigger` (endpoint Apps Script). Sheet real: doc `1fTjvs6...`, tab `Bewerbungen` (28 coloane), Google Sheets OAuth credential în n8n.
```

with:

```
- Workflow-uri active: `JobApp Ingest`/`Pending` (upsert/read Sheet), `JobApp Upload` (PDF → Google Drive, scrie link-ul în col O „Anschreiben PDF" — vezi `sender/n8n_client.py::upload_doc`, deocamdată neapelat din pipeline). `JobApp Trigger` e un stub ping/pong **neconectat** — NU e endpoint-ul Apps Script (acela e `job.n8n-studio.com/trigger`, alt serviciu, vezi mai jos); păstrat activ + în git (`n8n_workflows/jobapp_trigger.json`) pentru istoric/eventuală reutilizare. Sheet real: doc `1fTjvs6...`, tab `Bewerbungen` (28 coloane), Google Sheets OAuth credential în n8n.
```

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: actualizează CLAUDE.md — Apps Script trackuit, 4 workflow-uri n8n (JOB-31)"
```

---

### Task 9: Live read-only smoke test of `jobapp-pending`

**Context:** All 4 `JobApp*` webhooks show zero recorded executions ever, despite being active since June 30. `jobapp-pending` is the only one of the four that's safe to call live — it reads the Sheet and returns matching rows, without writing anything. This task only verifies connectivity/auth/routing; it does not change any code.

**Files:** none.

**Interfaces:** none.

- [ ] **Step 1: Call the live webhook**

Run:
```bash
set -a; source .env; set +a
curl -s -w "\nHTTP_STATUS:%{http_code}\n" -X POST \
  -H "X-JobApp-Secret: $N8N_WEBHOOK_SECRET" \
  -H "Content-Type: application/json" \
  -d '{"action":"get_pending"}' \
  "https://app.n8n-studio.com/webhook/jobapp-pending"
```

Expected: `HTTP_STATUS:200` and a JSON body shaped `{"ok": true, "commands": [...]}` (the array may be empty — that's a pass, not a failure).

- [ ] **Step 2: Record the result**

No commit. Report the HTTP status and response shape back in the task summary — if it's not 200, stop and report before continuing to Task 10 (a broken live path is worth surfacing before deleting anything).

---

### Task 10: Delete the 4 confirmed-dead n8n workflows

**Context:** User-confirmed for deletion (all already inactive, zero runtime impact today): 2 duplicate SSH-based "Job Tracker → Google Sheets Sync" workflows (`naxm3lJSXipBoH5r`, `4jF1tciI4Oj4lIZp`), the standalone "Automate LinkedIn job search and applications with Claude AI and Google Sheets" Easy-Apply prototype (`TUjxbhfZQwL1U3Mr`), and the manual-trigger-only "Bewerbung Email Sender — Brevo" (`rw6lXL3JG85DIcmN`). This must run *after* Task 7 is committed (backups for the workflows being *kept* must exist first — these 4 are not being backed up since they're being deleted, not preserved).

**Files:** none (live n8n API action) + `n8n_workflows/README.md` (already updated with the cleanup note in Task 7 — this task just makes that note true).

**Interfaces:** none.

- [ ] **Step 1: Delete the 4 workflows**

Run:
```bash
set -a; source .env; set +a
for id in naxm3lJSXipBoH5r 4jF1tciI4Oj4lIZp TUjxbhfZQwL1U3Mr rw6lXL3JG85DIcmN; do
  echo "DELETE $id"
  curl -s -o /dev/null -w "  HTTP %{http_code}\n" -X DELETE \
    -H "X-N8N-API-KEY: $N8N_API_KEY" \
    "https://app.n8n-studio.com/api/v1/workflows/$id"
done
```

Expected: `HTTP 200` printed for all 4.

- [ ] **Step 2: Verify deletion and that JobApp workflows are untouched**

Run:
```bash
set -a; source .env; set +a
curl -s -H "X-N8N-API-KEY: $N8N_API_KEY" "https://app.n8n-studio.com/api/v1/workflows?limit=250" \
  | python3 -c "
import json, sys
data = json.load(sys.stdin)
ids = {w['id'] for w in data.get('data', [])}
deleted = {'naxm3lJSXipBoH5r', '4jF1tciI4Oj4lIZp', 'TUjxbhfZQwL1U3Mr', 'rw6lXL3JG85DIcmN'}
still_there = ids & deleted
print('still present (should be empty set):', still_there)
jobapp = {w['name']: w['active'] for w in data.get('data', []) if w['name'].startswith('JobApp')}
print('JobApp workflows (should all be True, 4 entries):', jobapp)
"
```

Expected: `still present (should be empty set): set()` and 4 `JobApp*` entries all `True`.

- [ ] **Step 3: Report**

No commit (the README already documents this from Task 7; nothing further to stage). Report the verification output in the task summary.

---

### Task 11: Add GitHub Actions CI

**Context:** No `.github/workflows` exists — confirmed via `find . -maxdepth 1 -iname ".github"` returning nothing. The repo has a GitHub remote (`https://github.com/Geronimo1975/Job_app.git`, confirmed via `git remote -v`). `tests/conftest.py` confirms the full suite runs on mock fixtures (`MOCK_CONFIG`, `tmp_project`) with no real network/secrets required.

**Files:**
- Create: `.github/workflows/tests.yml`

**Interfaces:** none.

- [ ] **Step 1: Create the workflow file**

```yaml
name: Tests

on:
  push:
    branches: [main]
  pull_request:

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - name: Install dependencies
        run: pip install -r requirements.txt
      - name: Run tests
        run: python -m pytest -q
```

- [ ] **Step 2: Verify YAML is well-formed**

Run: `python3 -c "import yaml; yaml.safe_load(open('.github/workflows/tests.yml')); print('OK')"`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/tests.yml
git commit -m "ci: adaugă GitHub Actions pentru pytest pe push/PR (JOB-32)"
```

- [ ] **Step 4: Report**

Note in the task summary that the workflow will only actually execute on the next `git push` to GitHub — this plan does not push (pushing is a separate, explicit decision, not implied by "commit").

---

### Task 12: Final verification

**Context:** Confirms the whole plan lands in a clean, green state.

**Files:** none.

**Interfaces:** none.

- [ ] **Step 1: Full test suite**

Run: `source .venv/bin/activate && python -m pytest -q`
Expected: all tests pass (97 original + 5 from Task 1 + 2 from Task 2 mapping + 1 from Task 2 career + 1 from Task 2 jobspy + 5 from Task 3 + 1 from Task 4 = 112 passed)

- [ ] **Step 2: Git status**

Run: `git status --porcelain`
Expected: empty (everything from Tasks 1-2, 3-8, 11 committed; Tasks 9-10 are live-only with no file changes).

- [ ] **Step 3: Confirm JOB-NN sequence**

Run: `git log --oneline -12`
Expected: 9 new commits visible on top of `JOB-23`, tags `JOB-24` through `JOB-32` in sequence (Tasks 1-8 → `JOB-24`-`JOB-31`, Task 11 → `JOB-32`; Tasks 9 and 10 are live-action/verification only and produce no commits — Task 10's only file change, the README cleanup note, was already committed in Task 7).

- [ ] **Step 4: Report to user**

Summarize: which of the 12 tasks landed, the live smoke-test result from Task 9, confirmation the 4 dead workflows are gone and the 4 `JobApp*` workflows are untouched/active, and a reminder that `sender/n8n_client.py::upload_doc` remains unwired into the pipeline by design (flagged, not fixed — needs a product decision on how `_row` is resolved for poll-sourced jobs).
