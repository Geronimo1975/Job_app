# Dashboard → Sheet → SENDEN Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Dashboard-ul completează câmpurile FIELD_MAP, le upsertează în Sheet prin `N8nClient.ingest()` și declanșează trimiterea de email scriind `SENDEN` în col P — poll-ul existent face restul.

**Architecture:** O punte nouă `jobs/sheet_bridge.py` (funcții pure + wrapper subțire peste `N8nClient` din `sender/`) izolează tot ce ține de pipeline. Views-urile apelează doar puntea. Modelul primește câmpurile lipsă din FIELD_MAP + starea locală de trimitere (`send_state`). Rădăcina repo-ului intră în `sys.path` din `settings.py`.

**Tech Stack:** Django 6, `sender/n8n_client.py` + `sender/n8n_mapping.py` (refolosite read-only), PyYAML, mock-uri în teste.

## Global Constraints

- Comentarii și mesaje în română; commit-uri cu tag `JOB-41`.
- `sender/` NU se modifică. NU se importă din `agents/`. Col M „Status" nu se emite niciodată. `description`/`raw_json` nu se împing în Sheet.
- Toate testele cu `N8nClient` mock-uit (`@patch("jobs.sheet_bridge.N8nClient")`) — niciun test nu lovește rețeaua.
- Suita existentă (18 teste dashboard + pipeline) rămâne verde: `python dashboard/manage.py test jobs` și `python -m pytest -q`.
- Chei interne exacte (din FIELD_MAP): `company, title, location, url, source, email_hr, contact_person, language, gefunden_am, notizen` + `match_score` + `sende_befehl`.

---

### Task 1: Extinderea modelului `Job` + sys.path

**Files:**
- Modify: `dashboard/dashboard/settings.py` (sys.path spre rădăcina repo)
- Modify: `dashboard/jobs/models.py`
- Modify: `dashboard/jobs/tests.py` (extinde `JobModelTests`)
- Create (generat): `dashboard/jobs/migrations/0002_*.py`

**Interfaces:**
- Produces: câmpurile `contact_person`, `notizen`, `match_score`, `send_state` (default `"draft"`), `sheet_pushed_at`; importabilitate `from sender.n8n_mapping import ...` din codul dashboard-ului.

- [ ] **Step 1: Scrie testul care eșuează**

În `dashboard/jobs/tests.py`, adaugă la finalul clasei `JobModelTests`:
```python
    def test_new_sheet_fields_defaults(self):
        job = Job.objects.create(url="https://a.co/9", title="X")
        self.assertEqual(job.contact_person, "")
        self.assertEqual(job.notizen, "")
        self.assertIsNone(job.match_score)
        self.assertEqual(job.send_state, "draft")
        self.assertIsNone(job.sheet_pushed_at)

    def test_sender_is_importable_from_dashboard(self):
        # sys.path-ul din settings.py trebuie să facă sender/ importabil
        from sender.n8n_mapping import FIELD_MAP
        self.assertEqual(FIELD_MAP["url"], "Apply URL")
```

- [ ] **Step 2: Rulează — trebuie să eșueze**

Run: `python dashboard/manage.py test jobs.tests.JobModelTests -v 1`
Expected: FAIL (`contact_person` inexistent; posibil și ImportError pe `sender`).

- [ ] **Step 3: Implementează**

În `dashboard/dashboard/settings.py`, imediat după definiția `BASE_DIR`:
```python
import sys

# Puntea spre pipeline: face importabile sender/n8n_client.py + n8n_mapping.py
# (refolosire read-only — vezi spec-ul 2026-07-23-dashboard-sheet-send).
REPO_ROOT = BASE_DIR.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
```

În `dashboard/jobs/models.py`, în clasa `Job`, după `date_posted`:
```python
    # Câmpuri FIELD_MAP completabile manual (Sheet: Kontakt Person / Notizen / Match Score)
    contact_person = models.CharField(max_length=200, blank=True)
    notizen = models.TextField(blank=True)
    match_score = models.IntegerField(null=True, blank=True)
```
și după `raw_json`:
```python
    # Starea locală a fluxului de trimitere prin Sheet:
    # draft → senden_pushed (SENDEN împins) → processed (dedus din pending).
    send_state = models.CharField(max_length=20, default="draft")
    sheet_pushed_at = models.DateTimeField(null=True, blank=True)
```

- [ ] **Step 4: Migrația + testele trec**

```bash
python dashboard/manage.py makemigrations jobs
python dashboard/manage.py test jobs -v 1
```
Expected: toate testele PASS (inclusiv cele 18 existente).

- [ ] **Step 5: Commit**

```bash
git add dashboard/dashboard/settings.py dashboard/jobs/models.py dashboard/jobs/tests.py dashboard/jobs/migrations/
git commit -m "feat(dashboard): câmpuri FIELD_MAP + stare trimitere în model (JOB-41)"
```

---

### Task 2: Puntea `jobs/sheet_bridge.py`

**Files:**
- Create: `dashboard/jobs/sheet_bridge.py`
- Modify: `dashboard/jobs/tests.py` (adaugă `SheetBridgeTests`)

**Interfaces:**
- Consumes: `sender.n8n_client.N8nClient`, `sender.n8n_mapping.job_to_sheet_row`, modelul `Job`.
- Produces (pentru Task 3):
  - `job_to_internal(job, sende_befehl=None) -> dict`
  - `preview_sheet_row(job) -> dict` (chei germane, pentru pagina de confirmare)
  - `push_to_sheet(job, senden: bool) -> dict` — `{ok: bool, error?: str}`
  - `poll_mode() -> str` — `"LIVE"` / `"dry-run"` / `"necunoscut (dry-run implicit)"`
  - `refresh_states(jobs_qs) -> int` — ridică `RuntimeError` la n8n indisponibil/dezactivat

- [ ] **Step 1: Scrie testele care eșuează**

Adaugă la finalul `dashboard/jobs/tests.py`:
```python
from unittest.mock import MagicMock


class SheetBridgeTests(TestCase):
    def _job(self, **kw):
        base = dict(url="https://acme.de/jobs/1", title="Python Dev", company="ACME",
                    location="Berlin", email_hr="jobs@acme.de", language="en",
                    contact_person="Anna Schmidt", notizen="de sunat", match_score=85)
        base.update(kw)
        return Job.objects.create(**base)

    def test_job_to_internal_maps_and_omits_blanks(self):
        from jobs import sheet_bridge
        job = self._job(location="", match_score=None)
        internal = sheet_bridge.job_to_internal(job)
        self.assertEqual(internal["company"], "ACME")
        self.assertEqual(internal["contact_person"], "Anna Schmidt")
        self.assertEqual(internal["gefunden_am"], job.created_at.strftime("%Y-%m-%d"))
        self.assertNotIn("location", internal)      # gol → omis
        self.assertNotIn("match_score", internal)   # None → omis
        self.assertNotIn("status", internal)        # col M nu se emite NICIODATĂ
        self.assertNotIn("description", internal)
        self.assertNotIn("sende_befehl", internal)  # doar la cerere

    def test_job_to_internal_with_senden(self):
        from jobs import sheet_bridge
        internal = sheet_bridge.job_to_internal(self._job(), sende_befehl="SENDEN")
        self.assertEqual(internal["sende_befehl"], "SENDEN")

    def test_preview_sheet_row_has_german_headers(self):
        from jobs import sheet_bridge
        row = sheet_bridge.preview_sheet_row(self._job())
        self.assertEqual(row["Firma"], "ACME")
        self.assertEqual(row["Sende Befehl"], "SENDEN")
        self.assertEqual(row["Match Score"], 85)
        self.assertNotIn("Status", row)

    @patch.dict(os.environ, {}, clear=True)
    @patch("jobs.sheet_bridge.load_pipeline_config",
           return_value={"n8n": {"enabled": True, "secret_env": "N8N_WEBHOOK_SECRET"}})
    def test_push_without_secret_fails_without_http(self, _cfg):
        from jobs import sheet_bridge
        res = sheet_bridge.push_to_sheet(self._job(), senden=True)
        self.assertFalse(res["ok"])
        self.assertIn("N8N_WEBHOOK_SECRET", res["error"])

    @patch("jobs.sheet_bridge.load_pipeline_config", return_value={"n8n": {"enabled": False}})
    def test_push_with_n8n_disabled_fails(self, _cfg):
        from jobs import sheet_bridge
        res = sheet_bridge.push_to_sheet(self._job(), senden=False)
        self.assertFalse(res["ok"])
        self.assertIn("dezactivat", res["error"])

    @patch.dict(os.environ, {"N8N_WEBHOOK_SECRET": "s3cret"})
    @patch("jobs.sheet_bridge.N8nClient")
    @patch("jobs.sheet_bridge.load_pipeline_config",
           return_value={"n8n": {"enabled": True, "secret_env": "N8N_WEBHOOK_SECRET"}})
    def test_push_ok_calls_ingest_with_senden(self, _cfg, mock_client_cls):
        from jobs import sheet_bridge
        mock_client_cls.return_value.ingest.return_value = {"ok": True}
        res = sheet_bridge.push_to_sheet(self._job(), senden=True)
        self.assertTrue(res["ok"])
        sent_jobs = mock_client_cls.return_value.ingest.call_args[0][0]
        self.assertEqual(sent_jobs[0]["sende_befehl"], "SENDEN")

    @patch.dict(os.environ, {"N8N_WEBHOOK_SECRET": "s3cret"})
    @patch("jobs.sheet_bridge.N8nClient")
    @patch("jobs.sheet_bridge.load_pipeline_config",
           return_value={"n8n": {"enabled": True, "secret_env": "N8N_WEBHOOK_SECRET"}})
    def test_push_http_error_returns_error(self, _cfg, mock_client_cls):
        from jobs import sheet_bridge
        mock_client_cls.return_value.ingest.side_effect = RuntimeError("n8n 5xx")
        res = sheet_bridge.push_to_sheet(self._job(), senden=False)
        self.assertFalse(res["ok"])
        self.assertIn("n8n 5xx", res["error"])

    @patch.dict(os.environ, {"N8N_WEBHOOK_SECRET": "s3cret"})
    @patch("jobs.sheet_bridge.N8nClient")
    @patch("jobs.sheet_bridge.load_pipeline_config",
           return_value={"n8n": {"enabled": True, "secret_env": "N8N_WEBHOOK_SECRET"}})
    def test_refresh_marks_processed_only_when_absent(self, _cfg, mock_client_cls):
        from jobs import sheet_bridge
        still = self._job(url="https://a.co/still", send_state="senden_pushed")
        done = self._job(url="https://a.co/done", send_state="senden_pushed")
        draft = self._job(url="https://a.co/draft")  # send_state=draft
        mock_client_cls.return_value.enabled = True
        mock_client_cls.return_value.get_pending.return_value = [
            {"Apply URL": "https://a.co/still"}
        ]
        changed = sheet_bridge.refresh_states(Job.objects.all())
        self.assertEqual(changed, 1)
        still.refresh_from_db(); done.refresh_from_db(); draft.refresh_from_db()
        self.assertEqual(still.send_state, "senden_pushed")
        self.assertEqual(done.send_state, "processed")
        self.assertEqual(draft.send_state, "draft")

    @patch("jobs.sheet_bridge.load_pipeline_config", return_value={"n8n": {"enabled": False}})
    def test_refresh_with_n8n_disabled_raises(self, _cfg):
        # Dezactivat → pending gol ar minți „processed"; trebuie eroare explicită
        from jobs import sheet_bridge
        self._job(send_state="senden_pushed")
        with self.assertRaises(RuntimeError):
            sheet_bridge.refresh_states(Job.objects.all())

    def test_poll_mode_reads_plist(self):
        from jobs import sheet_bridge
        with patch("jobs.sheet_bridge.Path.read_text", return_value="<string>--live</string>"):
            self.assertEqual(sheet_bridge.poll_mode(), "LIVE")
        with patch("jobs.sheet_bridge.Path.read_text", return_value="<string>poll</string>"):
            self.assertEqual(sheet_bridge.poll_mode(), "dry-run")
        with patch("jobs.sheet_bridge.Path.read_text", side_effect=OSError("absent")):
            self.assertIn("necunoscut", sheet_bridge.poll_mode())
```

- [ ] **Step 2: Rulează — trebuie să eșueze**

Run: `python dashboard/manage.py test jobs.tests.SheetBridgeTests -v 1`
Expected: FAIL (`No module named 'jobs.sheet_bridge'`).

- [ ] **Step 3: Implementează puntea**

Creează `dashboard/jobs/sheet_bridge.py`:
```python
"""Puntea dashboard → pipeline: upsert în Sheet + comanda SENDEN prin n8n.

Singurul modul al dashboard-ului care importă din sender/ (read-only).
Nu emite NICIODATĂ col M „Status" — aparține mașinii de stări Apps Script.
"""
import os
from pathlib import Path

import yaml

from sender.n8n_client import N8nClient
from sender.n8n_mapping import job_to_sheet_row

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SETTINGS = _REPO_ROOT / "config" / "settings.yaml"
_POLL_PLIST = _REPO_ROOT / "scripts" / "com.jobapp.poll.plist"


def load_pipeline_config() -> dict:
    """Config-ul pipeline-ului (bloc n8n etc.), fără import din agents/."""
    with open(_SETTINGS, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def job_to_internal(job, sende_befehl: str | None = None) -> dict:
    """Modelul Django → dict intern (chei engleze din FIELD_MAP).

    Emite doar câmpurile cu valoare (upsert-ul n8n golește celula la "").
    NU emite: status (col M), description/raw_json (fără coloană mapată).
    """
    data = {
        "company": job.company,
        "title": job.title,
        "location": job.location,
        "url": job.url,
        "source": job.source,
        "email_hr": job.email_hr,
        "contact_person": job.contact_person,
        "language": job.language,
        "gefunden_am": job.created_at.strftime("%Y-%m-%d") if job.created_at else "",
        "notizen": job.notizen,
        "match_score": job.match_score,
    }
    internal = {k: v for k, v in data.items() if v is not None and v != ""}
    if sende_befehl:
        internal["sende_befehl"] = sende_befehl
    return internal


def preview_sheet_row(job) -> dict:
    """Rândul EXACT (chei germane) care ar pleca la „Trimite" — pt. confirmare."""
    return job_to_sheet_row(job_to_internal(job, sende_befehl="SENDEN"))


def _client_or_error(cfg: dict) -> tuple[N8nClient | None, str]:
    n8n_cfg = cfg.get("n8n", {}) or {}
    if not n8n_cfg.get("enabled", False):
        return None, "n8n e dezactivat în config/settings.yaml (n8n.enabled: false)."
    secret_env = n8n_cfg.get("secret_env", "N8N_WEBHOOK_SECRET")
    if not os.environ.get(secret_env):
        return None, f"{secret_env} lipsește din mediu — rulează: set -a; source .env; set +a"
    return N8nClient(cfg), ""


def push_to_sheet(job, senden: bool) -> dict:
    """Upsert în Sheet; cu senden=True scrie și col P „Sende Befehl"=SENDEN."""
    try:
        cfg = load_pipeline_config()
    except Exception as e:
        return {"ok": False, "error": f"config necititbil: {e}"}
    client, err = _client_or_error(cfg)
    if client is None:
        return {"ok": False, "error": err}
    internal = job_to_internal(job, sende_befehl="SENDEN" if senden else None)
    try:
        client.ingest([internal])
    except Exception as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True}


def poll_mode() -> str:
    """Modul poll-ului launchd: LIVE dacă plist-ul conține --live."""
    try:
        content = _POLL_PLIST.read_text(encoding="utf-8")
    except OSError:
        return "necunoscut (dry-run implicit)"
    return "LIVE" if "--live" in content else "dry-run"


def refresh_states(jobs_qs) -> int:
    """Deducție din pending: rândurile senden_pushed absente din pending → processed.

    Ridică RuntimeError dacă n8n e indisponibil/dezactivat — un pending gol
    fals ar marca totul „processed".
    """
    cfg = load_pipeline_config()
    client, err = _client_or_error(cfg)
    if client is None:
        raise RuntimeError(err)
    pending_urls = {
        str(c.get("Apply URL", "")).strip() for c in client.get_pending()
    }
    changed = 0
    for job in jobs_qs.filter(send_state="senden_pushed"):
        if job.url.strip() not in pending_urls:
            job.send_state = "processed"
            job.save(update_fields=["send_state"])
            changed += 1
    return changed
```

- [ ] **Step 4: Rulează — trebuie să treacă**

Run: `python dashboard/manage.py test jobs.tests.SheetBridgeTests -v 1`
Expected: 10 teste PASS.

- [ ] **Step 5: Commit**

```bash
git add dashboard/jobs/sheet_bridge.py dashboard/jobs/tests.py
git commit -m "feat(dashboard): punte spre Sheet prin N8nClient — upsert + SENDEN + refresh (JOB-41)"
```

---

### Task 3: Formular de editare + views push/send/refresh + rute

**Files:**
- Create: `dashboard/jobs/forms.py`
- Modify: `dashboard/jobs/views.py`
- Modify: `dashboard/jobs/urls.py`
- Modify: `dashboard/jobs/tests.py` (adaugă `SheetViewTests`, extinde `ViewTests`)

**Interfaces:**
- Consumes: `sheet_bridge.push_to_sheet`, `preview_sheet_row`, `poll_mode`, `refresh_states`; `jobs.forms.JobForm`.
- Produces: rute `push_job`, `send_job`, `refresh_states` (nume exacte); `job_detail` acceptă POST (salvare formular).

- [ ] **Step 1: Scrie testele care eșuează**

Adaugă la finalul `dashboard/jobs/tests.py`:
```python
class SheetViewTests(TestCase):
    def _job(self, **kw):
        base = dict(url="https://acme.de/jobs/1", title="Python Dev", company="ACME")
        base.update(kw)
        return Job.objects.create(**base)

    def test_detail_form_saves_new_fields(self):
        job = self._job()
        resp = self.client.post(reverse("job_detail", args=[job.pk]), {
            "title": "Python Dev", "company": "ACME", "location": "Berlin",
            "email_hr": "hr@acme.de", "contact_person": "Anna Schmidt",
            "language": "de", "match_score": "90", "notizen": "urgent",
        })
        self.assertEqual(resp.status_code, 302)
        job.refresh_from_db()
        self.assertEqual(job.contact_person, "Anna Schmidt")
        self.assertEqual(job.match_score, 90)
        self.assertEqual(job.notizen, "urgent")

    @patch("jobs.views.push_to_sheet", return_value={"ok": True})
    def test_push_job_does_not_change_send_state(self, mock_push):
        job = self._job()
        resp = self.client.post(reverse("push_job", args=[job.pk]))
        self.assertEqual(resp.status_code, 302)
        mock_push.assert_called_once()
        self.assertFalse(mock_push.call_args.kwargs.get("senden",
                         mock_push.call_args.args[1] if len(mock_push.call_args.args) > 1 else False))
        job.refresh_from_db()
        self.assertEqual(job.send_state, "draft")

    @patch("jobs.views.poll_mode", return_value="dry-run")
    def test_send_job_get_shows_confirmation(self, _mode):
        job = self._job()
        resp = self.client.get(reverse("send_job", args=[job.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Firma")       # rândul cu chei germane
        self.assertContains(resp, "SENDEN")
        self.assertContains(resp, "dry-run")     # modul poll vizibil

    @patch("jobs.views.push_to_sheet", return_value={"ok": True})
    def test_send_job_post_pushes_and_marks_state(self, mock_push):
        job = self._job()
        resp = self.client.post(reverse("send_job", args=[job.pk]))
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(mock_push.call_args.kwargs.get("senden") or
                        (len(mock_push.call_args.args) > 1 and mock_push.call_args.args[1]))
        job.refresh_from_db()
        self.assertEqual(job.send_state, "senden_pushed")
        self.assertIsNotNone(job.sheet_pushed_at)

    @patch("jobs.views.push_to_sheet", return_value={"ok": False, "error": "n8n 5xx"})
    def test_send_job_post_error_keeps_state(self, _push):
        job = self._job()
        self.client.post(reverse("send_job", args=[job.pk]))
        job.refresh_from_db()
        self.assertEqual(job.send_state, "draft")
        self.assertIsNone(job.sheet_pushed_at)

    @patch("jobs.views.refresh_states", return_value=2)
    def test_refresh_view_reports_count(self, mock_refresh):
        resp = self.client.post(reverse("refresh_states"), follow=True)
        self.assertContains(resp, "2")

    @patch("jobs.views.refresh_states", side_effect=RuntimeError("n8n dezactivat"))
    def test_refresh_view_surfaces_error(self, _refresh):
        resp = self.client.post(reverse("refresh_states"), follow=True)
        self.assertContains(resp, "n8n dezactivat")
```

- [ ] **Step 2: Rulează — trebuie să eșueze**

Run: `python dashboard/manage.py test jobs.tests.SheetViewTests -v 1`
Expected: FAIL (`NoReverseMatch: push_job`).

- [ ] **Step 3: Formularul**

Creează `dashboard/jobs/forms.py`:
```python
"""Formularul de completare a câmpurilor FIELD_MAP (editare în pagina de detaliu)."""
from django import forms

from .models import Job


class JobForm(forms.ModelForm):
    class Meta:
        model = Job
        fields = [
            "title", "company", "location", "email_hr",
            "contact_person", "language", "match_score", "notizen",
        ]
```

- [ ] **Step 4: Views + rute**

În `dashboard/jobs/views.py`, înlocuiește importurile și `job_detail`, apoi adaugă views-urile noi:
```python
"""Views dashboard: index, add (PRG), detail+edit, push/send în Sheet, refresh."""
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.validators import URLValidator
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from .extractor import extract_job
from .forms import JobForm
from .models import Job
from .sheet_bridge import poll_mode, preview_sheet_row, push_to_sheet, refresh_states
```

`job_detail` devine:
```python
def job_detail(request, pk):
    job = get_object_or_404(Job, pk=pk)
    if request.method == "POST":
        form = JobForm(request.POST, instance=job)
        if form.is_valid():
            form.save()
            messages.success(request, "Câmpuri salvate local.")
            return redirect("job_detail", pk=pk)
    else:
        form = JobForm(instance=job)
    return render(request, "jobs/detail.html", {"job": job, "form": form})
```

Adaugă la finalul fișierului:
```python
def push_job(request, pk):
    """„Salvează în Sheet" — upsert-ul câmpurilor, FĂRĂ comandă de trimitere."""
    if request.method != "POST":
        return redirect("job_detail", pk=pk)
    job = get_object_or_404(Job, pk=pk)
    res = push_to_sheet(job, senden=False)
    if res["ok"]:
        messages.success(request, "Rând salvat în Sheet (upsert pe Apply URL).")
    else:
        messages.error(request, f"Salvare în Sheet eșuată: {res['error']}")
    return redirect("job_detail", pk=pk)


def send_job(request, pk):
    """„Trimite (SENDEN)" — GET: confirmare cu rândul exact; POST: push cu col P=SENDEN."""
    job = get_object_or_404(Job, pk=pk)
    if request.method == "POST":
        res = push_to_sheet(job, senden=True)
        if res["ok"]:
            job.send_state = "senden_pushed"
            job.sheet_pushed_at = timezone.now()
            job.save(update_fields=["send_state", "sheet_pushed_at"])
            messages.success(
                request,
                "SENDEN scris în Sheet — poll-ul trimite emailul la următorul tick.",
            )
            return redirect("index")
        messages.error(request, f"Trimitere eșuată: {res['error']}")
        return redirect("job_detail", pk=pk)
    return render(request, "jobs/send_confirm.html", {
        "job": job,
        "row": preview_sheet_row(job),
        "mode": poll_mode(),
    })


def refresh_states_view(request):
    """Actualizează stările din pending (deducție: absent = procesat de poll)."""
    if request.method == "POST":
        try:
            changed = refresh_states(Job.objects.all())
            messages.info(request, f"Stări actualizate: {changed} rând(uri) marcate procesate.")
        except RuntimeError as e:
            messages.error(request, f"Refresh eșuat: {e}")
    return redirect("index")
```

În `dashboard/jobs/urls.py`, adaugă în `urlpatterns`:
```python
    path("job/<int:pk>/push", views.push_job, name="push_job"),
    path("job/<int:pk>/send", views.send_job, name="send_job"),
    path("refresh", views.refresh_states_view, name="refresh_states"),
]
```

- [ ] **Step 5: Rulează — GET-ul de confirmare va cere template-ul**

Run: `python dashboard/manage.py test jobs.tests.SheetViewTests -v 1`
Expected: majoritatea PASS; `test_send_job_get_shows_confirmation` FAIL cu `TemplateDoesNotExist: jobs/send_confirm.html` (se rezolvă în Task 4). Dacă și `test_detail_form_saves_new_fields` pică pe template, e tot Task 4 (detail.html încă nu are formular — dar POST-ul face redirect fără randare, deci trebuie să treacă).

- [ ] **Step 6: Commit**

```bash
git add dashboard/jobs/forms.py dashboard/jobs/views.py dashboard/jobs/urls.py dashboard/jobs/tests.py
git commit -m "feat(dashboard): editare câmpuri + push/SENDEN/refresh views (JOB-41)"
```

---

### Task 4: Template-uri (detail cu formular, confirmare, index cu stare)

**Files:**
- Modify: `dashboard/jobs/templates/jobs/detail.html`
- Create: `dashboard/jobs/templates/jobs/send_confirm.html`
- Modify: `dashboard/jobs/templates/jobs/index.html`
- Modify: `dashboard/jobs/templates/jobs/base.html` (stiluri formular + badge)

**Interfaces:**
- Consumes: context `{"job", "form"}` (detail), `{"job", "row", "mode"}` (send_confirm); rutele `push_job`, `send_job`, `refresh_states`.

- [ ] **Step 1: Stiluri noi în `base.html`**

În `<style>`, înainte de blocul `@media (max-width:520px)`:
```css
    /* Formular de editare (detail) */
    .form-grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(240px,1fr));
                 gap:14px; margin:0 0 16px; }
    .form-grid .full { grid-column:1 / -1; }
    .field label { display:block; font-size:12px; color:var(--muted); margin-bottom:5px;
                   text-transform:uppercase; letter-spacing:.04em; }
    .field input, .field textarea, .field select {
      width:100%; padding:10px 12px; background:var(--surface); color:var(--fg);
      border:1px solid var(--border-strong); border-radius:var(--radius); font:inherit;
      transition:border-color var(--dur) ease, box-shadow var(--dur) ease;
    }
    .field input:focus, .field textarea:focus {
      outline:none; border-color:var(--accent); box-shadow:0 0 0 3px rgba(34,197,94,.25);
    }
    .actions { display:flex; flex-wrap:wrap; gap:10px; margin:18px 0 8px; }
    .btn-secondary { padding:11px 16px; background:var(--surface-2); color:var(--fg);
                     border:1px solid var(--border-strong); font-weight:600; }
    .btn-secondary:hover { border-color:var(--accent); }
    .btn-danger { padding:11px 16px; background:var(--err); color:#fff; font-weight:600; }
    /* Badge stare trimitere */
    .pill.draft { background:rgba(148,163,184,.15); color:var(--muted); }
    .pill.senden_pushed { background:rgba(245,158,11,.15); color:var(--warn); }
    .pill.processed { background:rgba(34,197,94,.15); color:var(--ok); }
    /* Banner mod poll */
    .banner { padding:12px 16px; border-radius:var(--radius); margin:0 0 18px; font-size:14px;
              border:1px solid; }
    .banner.live { background:rgba(239,68,68,.1); color:var(--err); border-color:rgba(239,68,68,.35); }
    .banner.dry { background:rgba(245,158,11,.1); color:var(--warn); border-color:rgba(245,158,11,.35); }
```

- [ ] **Step 2: `detail.html` — formular + acțiuni Sheet**

Înlocuiește conținutul cu:
```html
{% extends "jobs/base.html" %}
{% block title %}{{ job.title|default:"Job" }}{% endblock %}
{% block content %}
  <p><a href="{% url 'index' %}">← Înapoi la tabel</a></p>
  <h1>{{ job.title|default:"(fără titlu)" }}</h1>
  <p class="sub">
    <span class="pill {{ job.extraction_status }}">{{ job.extraction_status }}</span>
    <span class="pill {{ job.send_state }}">{{ job.send_state }}</span>
    {% if job.sheet_pushed_at %} · SENDEN împins: {{ job.sheet_pushed_at|date:"d.m.Y H:i" }}{% endif %}
  </p>
  <div class="meta">
    <span><strong>URL:</strong> <a href="{{ job.url }}" target="_blank" rel="noopener" class="mono">{{ job.url }}</a></span>
  </div>

  <form method="post" action="{% url 'job_detail' job.pk %}">
    {% csrf_token %}
    <div class="form-grid">
      <div class="field"><label for="id_title">Position</label>{{ form.title }}</div>
      <div class="field"><label for="id_company">Firma</label>{{ form.company }}</div>
      <div class="field"><label for="id_location">Standort</label>{{ form.location }}</div>
      <div class="field"><label for="id_email_hr">Kontakt Email</label>{{ form.email_hr }}</div>
      <div class="field"><label for="id_contact_person">Kontakt Person</label>{{ form.contact_person }}</div>
      <div class="field"><label for="id_language">Sprache</label>{{ form.language }}</div>
      <div class="field"><label for="id_match_score">Match Score</label>{{ form.match_score }}</div>
      <div class="field full"><label for="id_notizen">Notizen</label>{{ form.notizen }}</div>
    </div>
    <div class="actions">
      <button type="submit" class="btn-secondary">Salvează local</button>
    </div>
  </form>

  <div class="actions">
    <form method="post" action="{% url 'push_job' job.pk %}">
      {% csrf_token %}<button type="submit" class="btn-secondary">Salvează în Sheet</button>
    </form>
    <a href="{% url 'send_job' job.pk %}"><button type="button" class="btn-primary">Trimite (SENDEN)…</button></a>
  </div>

  <h3>Descriere (doar local)</h3>
  <div class="desc">{{ job.description|default:"(goală)" }}</div>
  <h3>JSON brut</h3>
  <pre class="raw mono">{{ job.raw_json }}</pre>
{% endblock %}
```

- [ ] **Step 3: `send_confirm.html`**

Creează:
```html
{% extends "jobs/base.html" %}
{% block title %}Confirmă SENDEN — {{ job.title|default:"Job" }}{% endblock %}
{% block content %}
  <p><a href="{% url 'job_detail' job.pk %}">← Înapoi la job</a></p>
  <h1>Confirmă trimiterea (SENDEN)</h1>
  <p class="sub">Rândul de mai jos se upsertează în Sheet (match pe Apply URL), iar
     col P „Sende Befehl" devine SENDEN. Poll-ul preia comanda la următorul tick.</p>

  {% if mode == "LIVE" %}
    <div class="banner live"><strong>Mod poll: LIVE</strong> — emailul va fi trimis REAL către firmă.</div>
  {% else %}
    <div class="banner dry"><strong>Mod poll: {{ mode }}</strong> — emailul NU pleacă real (dry-run) până nu rulezi poll-ul cu --live.</div>
  {% endif %}

  <div class="table-scroll">
    <table>
      <thead><tr><th>Coloană Sheet</th><th>Valoare</th></tr></thead>
      <tbody>
        {% for header, value in row.items %}
        <tr><td class="dim">{{ header }}</td><td>{{ value }}</td></tr>
        {% endfor %}
      </tbody>
    </table>
  </div>

  <div class="actions">
    <form method="post" action="{% url 'send_job' job.pk %}">
      {% csrf_token %}<button type="submit" class="btn-danger">Confirmă trimiterea</button>
    </form>
    <a href="{% url 'job_detail' job.pk %}"><button type="button" class="btn-secondary">Înapoi</button></a>
  </div>
{% endblock %}
```

- [ ] **Step 4: `index.html` — coloana „Trimitere" + butonul de refresh**

În `<thead>`, după `<th>Status</th>` adaugă `<th>Trimitere</th>`. În `<tbody>`, după celula cu pastila `extraction_status` adaugă:
```html
        <td><span class="pill {{ job.send_state }}">{{ job.send_state }}</span></td>
```
Sub formularul de adăugare (înainte de `{% if jobs %}`), adaugă:
```html
  <form method="post" action="{% url 'refresh_states' %}" style="margin:0 0 16px;">
    {% csrf_token %}
    <button type="submit" class="btn-secondary">Actualizează starea trimiterilor</button>
  </form>
```

- [ ] **Step 5: Toată suita trece**

Run: `python dashboard/manage.py test jobs -v 1`
Expected: TOATE testele PASS (18 vechi + modelul + bridge + views).
Run: `python -m pytest -q` → suita pipeline neatinsă, verde.

- [ ] **Step 6: Verificare manuală**

```bash
set -a; source .env; set +a
python dashboard/manage.py migrate && python dashboard/manage.py runserver
# detaliu → completează câmpuri → Salvează în Sheet → verifică rândul în Sheet
# Trimite (SENDEN)… → confirmare → verifică col P în Sheet → după poll: refresh stare
```

- [ ] **Step 7: Commit**

```bash
git add dashboard/jobs/templates/
git commit -m "feat(dashboard): UI editare + confirmare SENDEN + stare trimitere (JOB-41)"
```

---

## Verificare finală

- [ ] `python dashboard/manage.py test jobs` — tot verde
- [ ] `python -m pytest -q` — pipeline neatins
- [ ] Manual: un rând de test ajunge în Sheet cu upsert corect (fără coloane fantomă, col M neatinsă)
