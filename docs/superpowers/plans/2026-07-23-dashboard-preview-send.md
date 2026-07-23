# Dashboard Preview & Send Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Execută (generează email + Anschreiben + PDF-uri) → Preview editabil → Transmite direct prin `SmtpSender` (dry-run implicit / LIVE explicit) → `GESENDET` în Sheet. Fluxul SENDEN dispare. UI cu meniu lateral (Joburi / Trimise).

**Architecture:** O punte nouă `jobs/generation_bridge.py` refolosește `generators/` + `sender/smtp_sender.py` (read-only). `sheet_bridge.py` pierde tot ce ținea de SENDEN/pending și primește `mark_sent_in_sheet`. Stări: `draft → generated → sent` (+ `error`).

**Tech Stack:** Django 6, `CoverLetterGenerator`, `EmailBodyGenerator`, `pdf_gen`, `SmtpSender`, `N8nClient.get_known_urls()` — toate mock-uite în teste.

## Global Constraints

- Comentarii/mesaje în română; commit-uri cu tag `JOB-45`.
- `sender/`, `generators/`, `agents/` NU se modifică. Col P și col M nu se mai emit NICIODATĂ din dashboard.
- Toate testele mock-uite (`@patch("jobs.generation_bridge.<X>")` etc.) — fără rețea/AI/SMTP real.
- Chei interne FIELD_MAP pentru Sheet: `url, email_status, gesendet_am` la marcare; upsert-ul de date rămâne ca înainte (fără `sende_befehl`).
- Suitele rămân verzi: `python dashboard/manage.py test jobs` + `python -m pytest -q`.

---

### Task 1: Model (câmpuri pachet + stări noi) + migrație de date

**Files:**
- Modify: `dashboard/jobs/models.py`, `dashboard/jobs/tests.py`
- Create (generat + editat): `dashboard/jobs/migrations/0004_*.py`

**Interfaces:**
- Produces: câmpurile `email_subject, email_body, anschreiben_text, anschreiben_pdf_path, bewerbung_pdf_path, attachments_json, generated_at, sent_at, send_error`; `seen_in_pending` DISPARE; stările vechi resetate la `draft`.

- [ ] **Step 1: Testul care eșuează** — în `JobModelTests`, înlocuiește `test_new_sheet_fields_defaults` cu:
```python
    def test_package_fields_defaults(self):
        job = Job.objects.create(url="https://a.co/9", title="X")
        self.assertEqual(job.send_state, "draft")
        self.assertEqual(job.email_subject, "")
        self.assertEqual(job.email_body, "")
        self.assertEqual(job.anschreiben_text, "")
        self.assertEqual(job.attachments_json, [])
        self.assertIsNone(job.generated_at)
        self.assertIsNone(job.sent_at)
        self.assertEqual(job.send_error, "")
        self.assertFalse(hasattr(job, "seen_in_pending"))
```
Run: `python dashboard/manage.py test jobs.tests.JobModelTests` → FAIL.

- [ ] **Step 2: Modelul** — în `Job`, înlocuiește blocul `send_state`/`sheet_pushed_at`/`seen_in_pending` cu:
```python
    # Fluxul Preview & Send: draft → generated (pachet complet) → sent (+ error).
    send_state = models.CharField(max_length=20, default="draft")
    sheet_pushed_at = models.DateTimeField(null=True, blank=True)
    # Pachetul generat („Execută"): editabil în Preview, trimis la „Transmite".
    email_subject = models.CharField(max_length=300, blank=True)
    email_body = models.TextField(blank=True)
    anschreiben_text = models.TextField(blank=True)
    anschreiben_pdf_path = models.CharField(max_length=500, blank=True)
    bewerbung_pdf_path = models.CharField(max_length=500, blank=True)
    attachments_json = models.JSONField(default=list, blank=True)
    generated_at = models.DateTimeField(null=True, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    send_error = models.TextField(blank=True)
```

- [ ] **Step 3: Migrația** — `python dashboard/manage.py makemigrations jobs`, apoi editează fișierul generat: adaugă la `operations` un `migrations.RunPython` care resetează stările vechi:
```python
def _reset_old_states(apps, schema_editor):
    Job = apps.get_model("jobs", "Job")
    Job.objects.filter(
        send_state__in=["senden_pushed", "processed", "unconfirmed"]
    ).update(send_state="draft")
```
(`migrations.RunPython(_reset_old_states, migrations.RunPython.noop)` la finalul listei.)

- [ ] **Step 4: Verde + commit**
```bash
python dashboard/manage.py test jobs.tests.JobModelTests
git add dashboard/jobs/models.py dashboard/jobs/tests.py dashboard/jobs/migrations/
git commit -m "feat(dashboard): câmpuri pachet email/Anschreiben + stări Preview&Send (JOB-45)"
```
(Restul suitei încă pică pe `seen_in_pending` — se repară în Task 2/4.)

---

### Task 2: `sheet_bridge` — curățare SENDEN + `mark_sent_in_sheet`

**Files:**
- Modify: `dashboard/jobs/sheet_bridge.py`, `dashboard/jobs/tests.py`

**Interfaces:**
- Produces: `push_to_sheet(job) -> dict` (fără `senden`), `mark_sent_in_sheet(job) -> dict`, `job_to_internal(job) -> dict` (fără `sende_befehl`), `load_pipeline_config`, `_client_or_error` (refolosit de Task 3).
- DISPAR: `poll_mode`, `refresh_states`, `preview_sheet_row`, `_POLL_PLIST_CANDIDATES`.

- [ ] **Step 1: Testele** — în `SheetBridgeTests`: șterge `test_job_to_internal_with_senden`, `test_preview_sheet_row_has_german_headers`, `test_refresh_*` (toate 3), `test_poll_mode_checks_installed_plist_first`; scoate `seen_in_pending` din `_job`; actualizează apelurile `push_to_sheet(job, senden=...)` → `push_to_sheet(job)`; în `test_push_ok_calls_ingest_with_senden` (redenumit `test_push_ok_never_emits_sende_befehl`):
```python
        res = sheet_bridge.push_to_sheet(self._job())
        self.assertTrue(res["ok"])
        sent_jobs = mock_client_cls.return_value.ingest.call_args[0][0]
        self.assertNotIn("sende_befehl", sent_jobs[0])
        self.assertNotIn("status", sent_jobs[0])
```
Adaugă:
```python
    @patch.dict(os.environ, {"N8N_WEBHOOK_SECRET": "s3cret"})
    @patch("jobs.sheet_bridge.N8nClient")
    @patch("jobs.sheet_bridge.load_pipeline_config",
           return_value={"n8n": {"enabled": True, "secret_env": "N8N_WEBHOOK_SECRET"}})
    def test_mark_sent_writes_gesendet(self, _cfg, mock_client_cls):
        from jobs import sheet_bridge
        import datetime
        job = self._job()
        job.sent_at = datetime.datetime(2026, 7, 23, 12, 0, tzinfo=datetime.timezone.utc)
        res = sheet_bridge.mark_sent_in_sheet(job)
        self.assertTrue(res["ok"])
        payload = mock_client_cls.return_value.ingest.call_args[0][0][0]
        self.assertEqual(payload["email_status"], "GESENDET")
        self.assertEqual(payload["url"], job.url)
        self.assertTrue(payload["gesendet_am"].startswith("2026-07-23"))

    @patch("jobs.sheet_bridge.load_pipeline_config", return_value={"n8n": {"enabled": False}})
    def test_mark_sent_disabled_fails(self, _cfg):
        from jobs import sheet_bridge
        res = sheet_bridge.mark_sent_in_sheet(self._job())
        self.assertFalse(res["ok"])
```
Run → FAIL (semnături vechi / funcție inexistentă).

- [ ] **Step 2: Implementarea** — în `sheet_bridge.py`: șterge `_POLL_PLIST_CANDIDATES`, `poll_mode`, `refresh_states`, `preview_sheet_row`; `job_to_internal(job)` pierde parametrul `sende_befehl` (și blocul lui); `push_to_sheet(job)` pierde parametrul + apelul devine `client.ingest([job_to_internal(job)])`. Adaugă:
```python
def mark_sent_in_sheet(job) -> dict:
    """După un LIVE reușit: col R „Email Status"=GESENDET + „Gesendet am".

    GESENDET e recunoscut de ambele sisteme existente → nimeni nu retrimite.
    """
    if not str(job.url or "").strip():
        return {"ok": False, "error": "jobul nu are Apply URL."}
    try:
        cfg = load_pipeline_config()
    except Exception as e:
        return {"ok": False, "error": f"config necititbil: {e}"}
    client, err = _client_or_error(cfg)
    if client is None:
        return {"ok": False, "error": err}
    moment = timezone.localtime(job.sent_at or timezone.now())
    payload = {
        "url": job.url,
        "email_status": "GESENDET",
        "gesendet_am": moment.strftime("%Y-%m-%d %H:%M"),
    }
    try:
        client.ingest([payload])
    except Exception as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True}
```

- [ ] **Step 3: Verde + commit**
```bash
python dashboard/manage.py test jobs.tests.SheetBridgeTests
git add dashboard/jobs/sheet_bridge.py dashboard/jobs/tests.py
git commit -m "refactor(dashboard): sheet_bridge fără SENDEN/pending + mark_sent_in_sheet (JOB-45)"
```

---

### Task 3: `generation_bridge.py` — generare pachet + trimitere SMTP

**Files:**
- Create: `dashboard/jobs/generation_bridge.py`
- Modify: `dashboard/jobs/tests.py` (clasa `GenerationBridgeTests`)

**Interfaces:**
- Produces: `generate_package(job) -> dict` (`{ok, email_subject, email_body, anschreiben_text, anschreiben_pdf_path, bewerbung_pdf_path, attachments, error?}`), `regenerate_pdfs(job) -> dict`, `send_package(job, live, provider=None) -> dict` (`{ok, live, error?}`), `already_sent_in_sheet(job) -> str` (`"da"`/`"nu"`/`"necunoscut (...)"`).

- [ ] **Step 1: Testele care eșuează**
```python
class GenerationBridgeTests(TestCase):
    def _job(self, **kw):
        base = dict(url="https://acme.de/jobs/1", title="Python Dev", company="ACME",
                    email_hr="hr@acme.de", language="de", description="Beschreibung")
        base.update(kw)
        return Job.objects.create(**base)

    @patch("jobs.generation_bridge.assemble_bewerbung")
    @patch("jobs.generation_bridge.text_to_pdf")
    @patch("jobs.generation_bridge.EmailBodyGenerator")
    @patch("jobs.generation_bridge.CoverLetterGenerator")
    @patch("jobs.generation_bridge.load_pipeline_config", return_value={"applicant": {"name": "George"}})
    def test_generate_package_populates_all_fields(self, _cfg, mock_cl, mock_eb, mock_pdf, mock_asm):
        import tempfile
        from jobs import generation_bridge
        with tempfile.TemporaryDirectory() as td:
            txt = Path(td) / "Anschreiben_ACME.txt"
            txt.write_text("Sehr geehrte...", encoding="utf-8")
            mock_cl.return_value.generate.return_value = "Sehr geehrte..."
            mock_cl.return_value.save_cover_letter.return_value = txt
            mock_eb.return_value.generate.return_value = "Body email"
            res = generation_bridge.generate_package(self._job())
        self.assertTrue(res["ok"])
        self.assertEqual(res["email_subject"], "Bewerbung als Python Dev — George")
        self.assertEqual(res["email_body"], "Body email")
        self.assertEqual(res["anschreiben_text"], "Sehr geehrte...")
        self.assertTrue(res["anschreiben_pdf_path"].endswith(".pdf"))
        mock_pdf.assert_called_once()

    @patch("jobs.generation_bridge.CoverLetterGenerator")
    @patch("jobs.generation_bridge.load_pipeline_config", return_value={})
    def test_generate_package_ai_error_is_error(self, _cfg, mock_cl):
        from jobs import generation_bridge
        mock_cl.return_value.generate.side_effect = Exception("AI down")
        res = generation_bridge.generate_package(self._job())
        self.assertFalse(res["ok"])
        self.assertIn("AI down", res["error"])

    @patch("jobs.generation_bridge.SmtpSender")
    @patch("jobs.generation_bridge.load_pipeline_config", return_value={})
    def test_send_package_dry_run(self, _cfg, mock_smtp):
        from jobs import generation_bridge
        mock_smtp.return_value.send.return_value = True
        job = self._job(email_subject="S", email_body="B")
        res = generation_bridge.send_package(job, live=False)
        self.assertTrue(res["ok"]); self.assertFalse(res["live"])
        self.assertTrue(mock_smtp.return_value.send.call_args.kwargs["dry_run"])

    @patch("jobs.generation_bridge.SmtpSender")
    @patch("jobs.generation_bridge.load_pipeline_config", return_value={})
    def test_send_package_live(self, _cfg, mock_smtp):
        from jobs import generation_bridge
        mock_smtp.return_value.send.return_value = True
        res = generation_bridge.send_package(self._job(email_subject="S", email_body="B"), live=True)
        self.assertTrue(res["ok"]); self.assertTrue(res["live"])
        self.assertFalse(mock_smtp.return_value.send.call_args.kwargs["dry_run"])

    @patch("jobs.generation_bridge.load_pipeline_config", return_value={})
    def test_send_package_without_email_fails(self, _cfg):
        from jobs import generation_bridge
        res = generation_bridge.send_package(self._job(email_hr=""), live=True)
        self.assertFalse(res["ok"]); self.assertIn("Kontakt Email", res["error"])

    @patch("jobs.generation_bridge.SmtpSender")
    @patch("jobs.generation_bridge.load_pipeline_config", return_value={})
    def test_send_package_refused_is_error(self, _cfg, mock_smtp):
        from jobs import generation_bridge
        mock_smtp.return_value.send.return_value = False
        res = generation_bridge.send_package(self._job(email_subject="S", email_body="B"), live=True)
        self.assertFalse(res["ok"]); self.assertIn("refuzat", res["error"])

    @patch.dict(os.environ, {"N8N_WEBHOOK_SECRET": "s"})
    @patch("jobs.generation_bridge.N8nClient")
    @patch("jobs.generation_bridge.load_pipeline_config",
           return_value={"n8n": {"enabled": True, "secret_env": "N8N_WEBHOOK_SECRET"}})
    def test_already_sent_checks_known_urls(self, _cfg, mock_client):
        from jobs import generation_bridge
        mock_client.return_value.get_known_urls.return_value = ["https://acme.de/jobs/1"]
        self.assertEqual(generation_bridge.already_sent_in_sheet(self._job()), "da")
        mock_client.return_value.get_known_urls.return_value = []
        self.assertEqual(generation_bridge.already_sent_in_sheet(self._job(url="https://x.de/2")), "nu")

    @patch("jobs.generation_bridge.load_pipeline_config", side_effect=Exception("boom"))
    def test_already_sent_unknown_on_error(self, _cfg):
        from jobs import generation_bridge
        self.assertIn("necunoscut", generation_bridge.already_sent_in_sheet(self._job()))
```
(`from pathlib import Path` există deja în tests prin alte importuri — dacă nu, adaugă-l sus.) Run → FAIL (modul inexistent).

- [ ] **Step 2: Implementarea** — creează `dashboard/jobs/generation_bridge.py`:
```python
"""Puntea de generare + trimitere: refolosește generators/ + sender/ (read-only).

„Execută" produce pachetul (email + Anschreiben + PDF-uri + atașamente),
„Transmite" îl trimite prin SmtpSender (rate-limit pe disc, partajat cu poll-ul).
Nicio funcție nu ridică excepții spre views — eșecul e codat în {ok, error}.
"""
from pathlib import Path

from django.utils import timezone

from generators.cover_letter_gen import CoverLetterGenerator
from generators.email_body_gen import EmailBodyGenerator
from generators.pdf_gen import assemble_bewerbung, text_to_pdf
from sender.n8n_client import N8nClient
from sender.smtp_sender import SmtpSender

from .sheet_bridge import _client_or_error, load_pipeline_config

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _docs_paths(cfg: dict) -> tuple[Path, list[Path]]:
    """CV-ul + certificatele, cu aceleași chei de config ca EmailAgent."""
    docs = cfg.get("documents", {}) or {}
    cv = _REPO_ROOT / docs.get("cv_file", "documents/cv/Resume George Sebastian Cucuiet.pdf")
    certs_dir = _REPO_ROOT / docs.get("certificates_dir", "documents/certificates")
    certs = sorted(certs_dir.glob("*.pdf")) if certs_dir.exists() else []
    return cv, certs


def _collect_attachments(bewerbung_pdf: str, anschreiben_pdf: str, cfg: dict) -> list[str]:
    """Prioritatea din EmailAgent: Komplett singur; altfel Anschreiben+CV+certificate."""
    if bewerbung_pdf and Path(bewerbung_pdf).exists():
        return [bewerbung_pdf]
    out: list[str] = []
    if anschreiben_pdf and Path(anschreiben_pdf).exists():
        out.append(anschreiben_pdf)
    cv, certs = _docs_paths(cfg)
    if cv.exists():
        out.append(str(cv))
    out.extend(str(p) for p in certs)
    return out


def generate_package(job) -> dict:
    """Generează emailul + Anschreiben + PDF-urile pentru un job."""
    try:
        cfg = load_pipeline_config()
    except Exception as e:
        return {"ok": False, "error": f"config necititbil: {e}"}
    company = job.company or "Unknown"
    position = job.title or "Unknown"
    language = job.language or "de"
    try:
        gen = CoverLetterGenerator(cfg, _REPO_ROOT)
        text = gen.generate(
            company=company, position=position,
            job_description=job.description or "",
            location=job.location or "", language=language,
        )
        txt_path = Path(gen.save_cover_letter(text, company, position))
    except Exception as e:
        return {"ok": False, "error": f"generare Anschreiben eșuată: {e}"}

    anschreiben_pdf = ""
    bewerbung_pdf = ""
    try:
        pdf_path = txt_path.with_suffix(".pdf")
        text_to_pdf(text, pdf_path)
        anschreiben_pdf = str(pdf_path)
        cv, certs = _docs_paths(cfg)
        if cv.exists():
            komplett = pdf_path.with_name(
                pdf_path.stem.replace("Anschreiben_", "Bewerbung_Komplett_") + ".pdf"
            )
            assemble_bewerbung(text, cv, komplett, certificates=certs)
            bewerbung_pdf = str(komplett)
    except Exception as e:
        # PDF-ul e best-effort (convenția CoverLetterAgent) — textul rămâne valid
        print(f"  ⚠ PDF eșuat: {e}")

    try:
        body = EmailBodyGenerator(cfg, _REPO_ROOT).generate(
            company=company, position=position,
            contact_person=job.contact_person or "", language=language,
        )
    except Exception as e:
        return {"ok": False, "error": f"generare body email eșuată: {e}"}

    applicant = (cfg.get("applicant") or {}).get("name", "")
    return {
        "ok": True,
        "email_subject": f"Bewerbung als {position} — {applicant}",
        "email_body": body,
        "anschreiben_text": text,
        "anschreiben_pdf_path": anschreiben_pdf,
        "bewerbung_pdf_path": bewerbung_pdf,
        "attachments": _collect_attachments(bewerbung_pdf, anschreiben_pdf, cfg),
    }


def regenerate_pdfs(job) -> dict:
    """După editarea Anschreiben-ului în Preview: PDF-urile reflectă textul editat."""
    if not job.anschreiben_pdf_path:
        return {"ok": False, "error": "niciun PDF generat — rulează Execută întâi."}
    try:
        cfg = load_pipeline_config()
        pdf_path = Path(job.anschreiben_pdf_path)
        text_to_pdf(job.anschreiben_text, pdf_path)
        if job.bewerbung_pdf_path:
            cv, certs = _docs_paths(cfg)
            if cv.exists():
                assemble_bewerbung(job.anschreiben_text, cv,
                                   Path(job.bewerbung_pdf_path), certificates=certs)
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": f"regenerare PDF eșuată: {e}"}


def send_package(job, live: bool, provider: str | None = None) -> dict:
    """Trimite pachetul prin SmtpSender. live=False → dry-run (nimic nu pleacă)."""
    to_email = (job.email_hr or "").strip()
    if not to_email:
        return {"ok": False, "live": live, "error": "jobul nu are Kontakt Email."}
    try:
        cfg = load_pipeline_config()
    except Exception as e:
        return {"ok": False, "live": live, "error": f"config necititbil: {e}"}
    attachments = [Path(p) for p in (job.attachments_json or []) if Path(p).exists()]
    try:
        smtp = SmtpSender(cfg, _REPO_ROOT)
        msg = smtp.build_email(
            to_email=to_email, subject=job.email_subject,
            body=job.email_body, attachments=attachments,
        )
        ok = smtp.send(msg, provider=provider, dry_run=not live)
    except Exception as e:
        return {"ok": False, "live": live, "error": str(e)}
    if not ok:
        return {"ok": False, "live": live,
                "error": "SMTP a refuzat trimiterea (rate limit zilnic sau eroare — vezi log)."}
    return {"ok": True, "live": live}


def already_sent_in_sheet(job) -> str:
    """Gardă advisory pre-LIVE: e URL-ul deja done/sent/GESENDET în Sheet?"""
    try:
        cfg = load_pipeline_config()
        client, err = _client_or_error(cfg)
        if client is None:
            return f"necunoscut ({err})"
        known = set(client.get_known_urls())
        return "da" if job.url.strip() in known else "nu"
    except Exception as e:
        return f"necunoscut ({e})"
```

- [ ] **Step 3: Verde + commit**
```bash
python dashboard/manage.py test jobs.tests.GenerationBridgeTests
git add dashboard/jobs/generation_bridge.py dashboard/jobs/tests.py
git commit -m "feat(dashboard): generation_bridge — pachet email/Anschreiben + trimitere SMTP (JOB-45)"
```

---

### Task 4: Views + rute (execute/preview/transmit/serve/sent; dispare SENDEN)

**Files:**
- Modify: `dashboard/jobs/views.py`, `dashboard/jobs/urls.py`, `dashboard/jobs/tests.py`

**Interfaces:**
- Produces: rute `execute_job`, `preview_job`, `transmit_job`, `serve_doc` (`<kind>` ∈ anschreiben|bewerbung), `serve_attachment` (`<int:idx>`), `sent_list`. DISPAR: `send_job`, `reset_job`, `refresh_states` (+ importurile `poll_mode`/`preview_sheet_row`/`refresh_states`, `_send_blockers`, `_row_hash`).
- Views noi consumă: `generate_package`, `regenerate_pdfs`, `send_package`, `already_sent_in_sheet`, `mark_sent_in_sheet`.

- [ ] **Step 1: Testele** — șterge din `SheetViewTests`: `test_send_job_*` (toate), `test_reset_job_returns_to_draft`, `test_refresh_view_*` (toate 3), `test_delete_blocked_when_senden_armed`, helperul `_confirm_hash`. Adaugă clasa:
```python
class PreviewSendViewTests(TestCase):
    def _job(self, **kw):
        base = dict(url="https://acme.de/jobs/1", title="Python Dev", company="ACME",
                    email_hr="hr@acme.de", send_state="generated",
                    email_subject="S", email_body="B", anschreiben_text="A",
                    attachments_json=[])
        base.update(kw)
        return Job.objects.create(**base)

    @patch("jobs.views.generate_package", return_value={
        "ok": True, "email_subject": "Subj", "email_body": "Body",
        "anschreiben_text": "Text", "anschreiben_pdf_path": "/tmp/a.pdf",
        "bewerbung_pdf_path": "", "attachments": ["/tmp/a.pdf"]})
    def test_execute_populates_and_redirects_to_preview(self, _gen):
        job = self._job(send_state="draft")
        resp = self.client.post(reverse("execute_job", args=[job.pk]))
        self.assertEqual(resp.status_code, 302)
        job.refresh_from_db()
        self.assertEqual(job.send_state, "generated")
        self.assertEqual(job.email_subject, "Subj")
        self.assertIsNotNone(job.generated_at)

    @patch("jobs.views.generate_package", return_value={"ok": False, "error": "AI down"})
    def test_execute_error_sets_error_state(self, _gen):
        job = self._job(send_state="draft")
        self.client.post(reverse("execute_job", args=[job.pk]))
        job.refresh_from_db()
        self.assertEqual(job.send_state, "error")
        self.assertIn("AI down", job.send_error)

    @patch("jobs.views.generate_package")
    def test_execute_blocked_on_sent(self, mock_gen):
        job = self._job(send_state="sent")
        self.client.post(reverse("execute_job", args=[job.pk]))
        mock_gen.assert_not_called()

    def test_preview_get_shows_package(self):
        job = self._job()
        resp = self.client.get(reverse("preview_job", args=[job.pk]))
        self.assertContains(resp, "hr@acme.de")
        self.assertContains(resp, "B")

    @patch("jobs.views.regenerate_pdfs", return_value={"ok": True})
    def test_preview_post_saves_edits_and_regenerates(self, mock_regen):
        job = self._job(anschreiben_pdf_path="/tmp/a.pdf")
        resp = self.client.post(reverse("preview_job", args=[job.pk]), {
            "email_subject": "Nou", "email_body": "Body nou", "anschreiben_text": "Text nou",
        })
        self.assertEqual(resp.status_code, 302)
        job.refresh_from_db()
        self.assertEqual(job.email_subject, "Nou")
        self.assertEqual(job.anschreiben_text, "Text nou")
        mock_regen.assert_called_once()

    @patch("jobs.views.already_sent_in_sheet", return_value="nu")
    def test_transmit_get_shows_confirm(self, _chk):
        job = self._job()
        resp = self.client.get(reverse("transmit_job", args=[job.pk]))
        self.assertContains(resp, "LIVE")
        self.assertContains(resp, "hr@acme.de")

    @patch("jobs.views.send_package", return_value={"ok": True, "live": False})
    @patch("jobs.views.already_sent_in_sheet", return_value="nu")
    def test_transmit_dry_run_keeps_generated(self, _chk, mock_send):
        job = self._job()
        self.client.post(reverse("transmit_job", args=[job.pk]), {})  # fără live
        self.assertFalse(mock_send.call_args.kwargs["live"])
        job.refresh_from_db()
        self.assertEqual(job.send_state, "generated")

    @patch("jobs.views.mark_sent_in_sheet", return_value={"ok": True})
    @patch("jobs.views.send_package", return_value={"ok": True, "live": True})
    @patch("jobs.views.already_sent_in_sheet", return_value="nu")
    def test_transmit_live_marks_sent_and_sheet(self, _chk, mock_send, mock_mark):
        job = self._job()
        self.client.post(reverse("transmit_job", args=[job.pk]), {"live": "on"})
        self.assertTrue(mock_send.call_args.kwargs["live"])
        mock_mark.assert_called_once()
        job.refresh_from_db()
        self.assertEqual(job.send_state, "sent")
        self.assertIsNotNone(job.sent_at)

    @patch("jobs.views.send_package")
    @patch("jobs.views.already_sent_in_sheet", return_value="da")
    def test_transmit_live_blocked_when_already_sent(self, _chk, mock_send):
        job = self._job()
        self.client.post(reverse("transmit_job", args=[job.pk]), {"live": "on"})
        mock_send.assert_not_called()

    @patch("jobs.views.send_package", return_value={"ok": False, "live": True, "error": "rate limit"})
    @patch("jobs.views.already_sent_in_sheet", return_value="nu")
    def test_transmit_failure_sets_error(self, _chk, _send):
        job = self._job()
        self.client.post(reverse("transmit_job", args=[job.pk]), {"live": "on"})
        job.refresh_from_db()
        self.assertEqual(job.send_state, "error")
        self.assertIn("rate limit", job.send_error)

    @patch("jobs.views.send_package")
    def test_transmit_blocked_wrong_state(self, mock_send):
        job = self._job(send_state="draft")
        self.client.post(reverse("transmit_job", args=[job.pk]), {"live": "on"})
        mock_send.assert_not_called()

    def test_serve_doc_missing_404(self):
        job = self._job()  # fără anschreiben_pdf_path
        resp = self.client.get(reverse("serve_doc", args=[job.pk, "anschreiben"]))
        self.assertEqual(resp.status_code, 404)

    def test_serve_doc_serves_real_file(self):
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            f.write(b"%PDF-1.4 test")
        job = self._job(anschreiben_pdf_path=f.name)
        resp = self.client.get(reverse("serve_doc", args=[job.pk, "anschreiben"]))
        self.assertEqual(resp.status_code, 200)

    def test_serve_attachment_out_of_range_404(self):
        job = self._job(attachments_json=["/tmp/nu-exista.pdf"])
        resp = self.client.get(reverse("serve_attachment", args=[job.pk, 5]))
        self.assertEqual(resp.status_code, 404)

    def test_sent_list_filters(self):
        self._job(url="https://a.co/1", send_state="sent", title="Trimis")
        self._job(url="https://a.co/2", send_state="generated", title="Netrimis")
        resp = self.client.get(reverse("sent_list"))
        self.assertContains(resp, "Trimis")
        self.assertNotContains(resp, "Netrimis")
```
Run → FAIL (`NoReverseMatch`).

- [ ] **Step 2: Implementarea** — `views.py`: șterge `send_job`, `reset_job`, `refresh_states_view`, `_send_blockers`, `_row_hash`, importurile `hashlib/json/poll_mode/preview_sheet_row/refresh_states`; din `delete_job` scoate blocul `senden_pushed`. Importuri noi:
```python
from django.http import FileResponse, Http404
from .generation_bridge import (already_sent_in_sheet, generate_package,
                                regenerate_pdfs, send_package)
from .sheet_bridge import mark_sent_in_sheet, push_to_sheet
```
Views noi:
```python
def execute_job(request, pk):
    """„Execută" — generează pachetul (email + Anschreiben + PDF-uri). Sincron, ~10-30s."""
    if request.method != "POST":
        return redirect("index")
    job = get_object_or_404(Job, pk=pk)
    if job.send_state == "sent":
        messages.error(request, "Jobul e deja trimis — nu regenerez peste un pachet trimis.")
        return redirect("index")
    res = generate_package(job)
    if res["ok"]:
        job.email_subject = res["email_subject"]
        job.email_body = res["email_body"]
        job.anschreiben_text = res["anschreiben_text"]
        job.anschreiben_pdf_path = res["anschreiben_pdf_path"]
        job.bewerbung_pdf_path = res["bewerbung_pdf_path"]
        job.attachments_json = res["attachments"]
        job.send_state = "generated"
        job.generated_at = timezone.now()
        job.send_error = ""
        job.save()
        messages.success(request, "Pachet generat — verifică-l în Preview înainte de transmitere.")
        return redirect("preview_job", pk=pk)
    job.send_state = "error"
    job.send_error = res["error"]
    job.save(update_fields=["send_state", "send_error"])
    messages.error(request, f"Generare eșuată: {res['error']}")
    return redirect("index")


def preview_job(request, pk):
    """Preview editabil: email (subiect+body) și Anschreiben; PDF-urile se refac la salvare."""
    job = get_object_or_404(Job, pk=pk)
    if request.method == "POST":
        job.email_subject = request.POST.get("email_subject", job.email_subject)
        job.email_body = request.POST.get("email_body", job.email_body)
        new_text = request.POST.get("anschreiben_text", job.anschreiben_text)
        text_changed = new_text != job.anschreiben_text
        job.anschreiben_text = new_text
        job.save(update_fields=["email_subject", "email_body", "anschreiben_text"])
        if text_changed and job.anschreiben_pdf_path:
            res = regenerate_pdfs(job)
            if res["ok"]:
                messages.success(request, "Editări salvate; PDF-urile refăcute din textul editat.")
            else:
                messages.warning(request, f"Editări salvate, dar PDF-ul NU s-a refăcut: {res['error']}")
        else:
            messages.success(request, "Editări salvate.")
        return redirect("preview_job", pk=pk)
    attachments = [
        {"idx": i, "name": Path(p).name, "exists": Path(p).exists()}
        for i, p in enumerate(job.attachments_json or [])
    ]
    return render(request, "jobs/preview.html", {"job": job, "attachments": attachments,
                                                 "section": "jobs"})


def transmit_job(request, pk):
    """Confirmare + trimitere: dry-run implicit, LIVE doar cu comutatorul bifat."""
    job = get_object_or_404(Job, pk=pk)
    if request.method == "POST":
        if job.send_state != "generated":
            messages.error(request, f"Starea e „{job.send_state}” — doar pachetele „generated” se transmit.")
            return redirect("index")
        if not (job.email_hr or "").strip():
            messages.error(request, "Fără Kontakt Email — completează-l întâi în pagina jobului.")
            return redirect("job_detail", pk=pk)
        live = request.POST.get("live") == "on"
        if live and already_sent_in_sheet(job) == "da":
            messages.error(request, "Sheet-ul arată jobul DEJA trimis (done/sent/GESENDET) — LIVE blocat.")
            return redirect("transmit_job", pk=pk)
        res = send_package(job, live=live)
        if res["ok"] and live:
            job.send_state = "sent"
            job.sent_at = timezone.now()
            job.send_error = ""
            job.save(update_fields=["send_state", "sent_at", "send_error"])
            mark = mark_sent_in_sheet(job)
            if mark["ok"]:
                messages.success(request, "Email TRIMIS + Sheet marcat GESENDET.")
            else:
                messages.warning(request, f"Email TRIMIS, dar Sheet-ul NU s-a marcat ({mark['error']}) "
                                          "— completează manual col R „Email Status”=GESENDET.")
            return redirect("sent_list")
        if res["ok"]:
            messages.info(request, "Dry-run OK — nimic nu a fost trimis. Bifează LIVE când ești gata.")
            return redirect("transmit_job", pk=pk)
        job.send_state = "error"
        job.send_error = res["error"]
        job.save(update_fields=["send_state", "send_error"])
        messages.error(request, f"Trimitere eșuată: {res['error']}")
        return redirect("index")
    attachments = [
        {"idx": i, "name": Path(p).name, "exists": Path(p).exists()}
        for i, p in enumerate(job.attachments_json or [])
    ]
    return render(request, "jobs/transmit_confirm.html", {
        "job": job, "attachments": attachments,
        "sheet_verdict": already_sent_in_sheet(job), "section": "jobs",
    })


def serve_doc(request, pk, kind):
    """Servește DOAR PDF-urile jobului (căi stocate, nu căi libere)."""
    job = get_object_or_404(Job, pk=pk)
    path = {"anschreiben": job.anschreiben_pdf_path,
            "bewerbung": job.bewerbung_pdf_path}.get(kind, "")
    if not path or not Path(path).exists():
        raise Http404
    return FileResponse(open(path, "rb"), content_type="application/pdf")


def serve_attachment(request, pk, idx):
    job = get_object_or_404(Job, pk=pk)
    paths = job.attachments_json or []
    if not (0 <= idx < len(paths)) or not Path(paths[idx]).exists():
        raise Http404
    return FileResponse(open(paths[idx], "rb"))


def sent_list(request):
    """Secțiunea „Trimise" din meniul lateral."""
    return render(request, "jobs/sent.html", {
        "jobs": Job.objects.filter(send_state="sent"), "section": "sent",
    })
```
(`from pathlib import Path` sus în views; `index` primește și el `"section": "jobs"` în context.) `urls.py` — șterge `send/reset/refresh`, adaugă:
```python
    path("job/<int:pk>/execute", views.execute_job, name="execute_job"),
    path("job/<int:pk>/preview", views.preview_job, name="preview_job"),
    path("job/<int:pk>/transmit", views.transmit_job, name="transmit_job"),
    path("job/<int:pk>/doc/<str:kind>", views.serve_doc, name="serve_doc"),
    path("job/<int:pk>/att/<int:idx>", views.serve_attachment, name="serve_attachment"),
    path("trimise/", views.sent_list, name="sent_list"),
```

- [ ] **Step 3: Verde parțial + commit** — testele view trec în afara celor care randează template-urile noi (`preview.html`, `transmit_confirm.html`, `sent.html` — Task 5). Rulează `python dashboard/manage.py test jobs.tests.PreviewSendViewTests`; POST-urile (302) trebuie să treacă acum.
```bash
git add dashboard/jobs/views.py dashboard/jobs/urls.py dashboard/jobs/tests.py
git commit -m "feat(dashboard): views execute/preview/transmit/serve/sent; dispare SENDEN (JOB-45)"
```

---

### Task 5: Template-uri — sidebar + index + preview + transmit + sent

**Files:**
- Modify: `dashboard/jobs/templates/jobs/base.html`, `index.html`, `detail.html`
- Create: `preview.html`, `transmit_confirm.html`, `sent.html`
- Delete: `send_confirm.html`

**Interfaces:**
- Consumes: context `section` (`jobs`/`sent`), rutele din Task 4.

- [ ] **Step 1:** Invocă skill-ul `ui-ux-pro-max` înainte de HTML/CSS (instrucțiune globală); păstrează design system-ul OLED existent.

- [ ] **Step 2: `base.html`** — layout sidebar: înlocuiește `<body><div class="wrap">` cu:
```html
<body>
  <div class="layout">
    <nav class="side" aria-label="Meniu principal">
      <div class="brand">Job Dashboard</div>
      <a class="nav-link {% if section == 'jobs' %}active{% endif %}" href="{% url 'index' %}">Joburi</a>
      <a class="nav-link {% if section == 'sent' %}active{% endif %}" href="{% url 'sent_list' %}">Trimise</a>
    </nav>
    <main class="wrap">
      {% if messages %}...neschimbat...{% endif %}
      {% block content %}{% endblock %}
    </main>
  </div>
</body>
```
CSS nou (înainte de media queries): `.layout{display:flex;min-height:100dvh}`, `.side{width:200px;flex-shrink:0;background:var(--surface);border-right:1px solid var(--border);padding:24px 14px}`, `.brand{font-weight:700;margin-bottom:20px}`, `.nav-link{display:block;padding:10px 12px;border-radius:8px;color:var(--muted);margin-bottom:4px}`, `.nav-link.active{background:var(--surface-2);color:var(--fg)}`, pastile noi `.pill.generated{albastru}` `.pill.sent{verde}` (error există), iar în `@media (max-width:720px)` sidebar-ul devine bară orizontală sus (`.layout{flex-direction:column}.side{width:100%;display:flex;gap:8px;border-right:0;border-bottom:1px solid var(--border)}`). Șterge stilurile moarte (`.banner.dry` rămâne pt. transmit).

- [ ] **Step 3: `index.html`** — coloana „Trimitere" arată `send_state` nou; coloana de acțiuni per stare:
```html
{% if job.send_state == "draft" or job.send_state == "error" %}
  <form method="post" action="{% url 'execute_job' job.pk %}">{% csrf_token %}
    <button class="btn-secondary" type="submit"
            onclick="this.disabled=true;this.textContent='Generez…';this.form.submit()">Execută</button></form>
{% elif job.send_state == "generated" %}
  <a href="{% url 'preview_job' job.pk %}">Preview</a> ·
  <a href="{% url 'transmit_job' job.pk %}">Transmite</a>
{% elif job.send_state == "sent" %}
  <a href="{% url 'preview_job' job.pk %}">Vezi</a>
{% endif %}
```
Legenda veche (pending) se înlocuiește cu: `draft → Execută generează pachetul · generated → Preview/Transmite · sent → trimis (GESENDET în Sheet) · error → vezi mesajul și re-Execută`. `send_error` afișat ca title pe pastila error.

- [ ] **Step 4: `preview.html`** — formular editabil: destinatar (`job.email_hr`, read-only, cu link spre detail dacă e gol), `email_subject` input, `email_body` textarea mare, `anschreiben_text` textarea mare, lista atașamentelor (link `serve_attachment`/`serve_doc`, marcaj roșu dacă `exists=False`), butoane: „Salvează editările" (POST), „Re-execută (regenerare AI)" (form spre `execute_job`), „Transmite…" (link spre `transmit_job`), „← Înapoi".

- [ ] **Step 5: `transmit_confirm.html`** — rezumat (Către/Subiect/atașamente cu link-uri), verdictul Sheet (`sheet_verdict`: `da` → banner roșu „DEJA trimis — LIVE va fi blocat"; `necunoscut...` → banner galben; `nu` → nimic), comutator:
```html
<form method="post" action="{% url 'transmit_job' job.pk %}">
  {% csrf_token %}
  <label class="live-toggle"><input type="checkbox" name="live"> Trimite REAL (LIVE) — fără bifă rulează dry-run</label>
  <button type="submit" class="btn-danger">Transmite</button>
</form>
```

- [ ] **Step 6: `sent.html`** — tabel simplu: Titlu (link preview), Companie, Către, Trimis la (`sent_at`), atașamente count. `detail.html` — actualizează butoanele: scoate SENDEN/reset, adaugă `Execută`/`Preview` după stare.

- [ ] **Step 7: Șterge `send_confirm.html`**, rulează TOT:
```bash
python dashboard/manage.py test jobs        # tot verde
python -m pytest -q                          # pipeline neatins
```

- [ ] **Step 8: Commit**
```bash
git add -A dashboard/jobs/templates/
git commit -m "feat(dashboard): UI sidebar Joburi/Trimise + preview + transmit (JOB-45)"
```

---

## Verificare finală

- [ ] Ambele suite verzi; `git status` curat.
- [ ] Manual: `set -a; source .env; set +a && python dashboard/manage.py migrate && runserver` → Execută pe un job real → Preview arată email+Anschreiben+PDF-uri → Transmite dry-run OK → (opțional) LIVE.
