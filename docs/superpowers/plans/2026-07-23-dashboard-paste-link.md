# Dashboard „Paste link → structură" Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Un dashboard Django local unde lipești URL-ul unui job, Claude extrage câmpurile canonice, iar rezultatul se salvează și se afișează într-un tabel persistent.

**Architecture:** Proiect Django de sine stătător în `dashboard/`, izolat complet de pipeline (propriul SQLite, propriul `runserver`, fără import din `agents/`/`sender/`). O funcție pură `extract_job(url)` face fetch (httpx) → text (BeautifulSoup) → extragere JSON (Claude) → dict canonic. Views cu pattern Post/Redirect/Get salvează prin `update_or_create` (deduplicare pe URL).

**Tech Stack:** Django 5.x, SQLite, httpx, beautifulsoup4, anthropic, PyYAML (toate deja în proiect în afară de Django).

## Global Constraints

- Python 3.12+; comentarii și mesaje în limba română.
- Izolare strictă: NU se modifică `agents/`, `sender/`, `main.py`, `scrapers/`, workflow-urile n8n. Dashboard-ul nu importă din aceste pachete.
- Model AI din `config/settings.yaml` (`ai.model`, fallback `claude-sonnet-4-6`); cheia din `ANTHROPIC_API_KEY` (env).
- Câmpuri canonice identice cu `scrapers/base.py::make_job()`: `title, company, location, description, email_hr, salary_min, salary_max, language, date_posted`. Descriere trunchiată la 5000 caractere.
- Testele NU lovesc rețeaua: `httpx` și `anthropic` mock-uite. Rulare: `python dashboard/manage.py test jobs`.
- Commit-uri în română cu tag `JOB-39`.

---

### Task 1: Scaffold proiect Django + configurare + gardă de boot

**Files:**
- Create: `dashboard/manage.py`, `dashboard/dashboard/{__init__,settings,urls,asgi,wsgi}.py`, `dashboard/jobs/{__init__,admin,apps,models,views,tests}.py`, `dashboard/jobs/migrations/__init__.py` (generate de `django-admin`)
- Modify: `requirements.txt` (adaugă `Django>=5.0`), `.gitignore` (adaugă `dashboard/db.sqlite3`)
- Modify: `dashboard/dashboard/settings.py` (adaugă app `jobs`, ALLOWED_HOSTS)

**Interfaces:**
- Produces: proiect Django care pornește; app `jobs` înregistrat; `python dashboard/manage.py check` curat.

- [ ] **Step 1: Adaugă Django în requirements și instalează**

În `requirements.txt`, sub secțiunea Core, adaugă:
```
# Dashboard web (paste link → structură)
Django>=5.0
```
Apoi:
```bash
cd /Users/Geronimo/Projects/Job_app
source .venv/bin/activate
pip install "Django>=5.0"
```

- [ ] **Step 2: Scaffold proiectul și app-ul**

```bash
cd /Users/Geronimo/Projects/Job_app
mkdir -p dashboard
cd dashboard
django-admin startproject dashboard .
python manage.py startapp jobs
```
Structura rezultată: `dashboard/manage.py`, `dashboard/dashboard/settings.py`, `dashboard/jobs/`.

- [ ] **Step 3: Înregistrează app-ul și ajustează settings**

În `dashboard/dashboard/settings.py`:
- În `INSTALLED_APPS`, adaugă `"jobs",` la finalul listei.
- Setează `ALLOWED_HOSTS = ["127.0.0.1", "localhost"]`.
- Sub `SECRET_KEY = ...`, înlocuiește cu citire din env cu fallback dev:
```python
import os
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "dev-insecure-local-only-change-me")
```
(DEBUG rămâne `True` — unealtă locală.)

- [ ] **Step 4: Ignoră DB-ul local**

În `.gitignore` (root proiect), adaugă:
```
# Dashboard local DB
dashboard/db.sqlite3
```

- [ ] **Step 5: Verifică boot-ul**

Run: `cd /Users/Geronimo/Projects/Job_app && python dashboard/manage.py check`
Expected: `System check identified no issues (0 silenced).`

- [ ] **Step 6: Commit**

```bash
cd /Users/Geronimo/Projects/Job_app
git add requirements.txt .gitignore dashboard/
git commit -m "feat(dashboard): scaffold proiect Django + app jobs (JOB-39)"
```

---

### Task 2: Modelul `Job`

**Files:**
- Modify: `dashboard/jobs/models.py`
- Modify: `dashboard/jobs/tests.py`
- Create (generat): `dashboard/jobs/migrations/0001_initial.py`

**Interfaces:**
- Produces: `jobs.models.Job` cu câmpurile canonice + `extraction_status`, `raw_json`, `created_at`; `Meta.ordering = ["-created_at"]`.

- [ ] **Step 1: Scrie testul care eșuează**

Înlocuiește conținutul `dashboard/jobs/tests.py` cu:
```python
"""Teste dashboard jobs. Rulare: python dashboard/manage.py test jobs"""
from django.test import TestCase
from jobs.models import Job


class JobModelTests(TestCase):
    def test_create_job_with_canonical_fields(self):
        job = Job.objects.create(
            url="https://acme.de/jobs/1",
            title="Python Developer",
            company="ACME",
            location="Berlin",
            description="Build things",
            email_hr="jobs@acme.de",
            salary_min=60000,
            salary_max=80000,
            language="en",
            date_posted="2026-07-01",
        )
        self.assertEqual(job.source, "dashboard")
        self.assertEqual(job.extraction_status, "ok")
        self.assertEqual(job.raw_json, {})
        self.assertIsNotNone(job.created_at)

    def test_ordering_newest_first(self):
        Job.objects.create(url="https://a.co/1", title="First")
        Job.objects.create(url="https://a.co/2", title="Second")
        titles = list(Job.objects.values_list("title", flat=True))
        self.assertEqual(titles, ["Second", "First"])

    def test_url_is_unique(self):
        Job.objects.create(url="https://a.co/1", title="First")
        from django.db import IntegrityError
        with self.assertRaises(IntegrityError):
            Job.objects.create(url="https://a.co/1", title="Dup")
```

- [ ] **Step 2: Rulează testul — trebuie să eșueze**

Run: `python dashboard/manage.py test jobs -v 2`
Expected: FAIL (câmpurile `source`/`extraction_status`/`raw_json` nu există încă).

- [ ] **Step 3: Implementează modelul**

Înlocuiește `dashboard/jobs/models.py` cu:
```python
"""Modelul Job — oglindește schema canonică din scrapers/base.py::make_job()."""
from django.db import models


class Job(models.Model):
    url = models.URLField(max_length=500, unique=True)
    title = models.CharField(max_length=300, blank=True)
    company = models.CharField(max_length=200, blank=True)
    location = models.CharField(max_length=200, blank=True)
    description = models.TextField(blank=True)
    # CharField, nu EmailField: nu vrem validare pe output-ul AI (poate fi gol/imperfect).
    email_hr = models.CharField(max_length=254, blank=True)
    salary_min = models.IntegerField(null=True, blank=True)
    salary_max = models.IntegerField(null=True, blank=True)
    language = models.CharField(max_length=5, blank=True, default="")
    date_posted = models.CharField(max_length=40, blank=True)
    source = models.CharField(max_length=40, default="dashboard")
    # Rezultatul extragerii: "ok" / "partial" / "error".
    extraction_status = models.CharField(max_length=10, default="ok")
    # Extragerea AI brută + eventuale mesaje de eroare, pentru debug.
    raw_json = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.title or '(fără titlu)'} @ {self.company or '?'}"
```

- [ ] **Step 4: Generează și rulează migrația**

```bash
python dashboard/manage.py makemigrations jobs
python dashboard/manage.py test jobs -v 2
```
Expected: 3 teste PASS.

- [ ] **Step 5: Commit**

```bash
git add dashboard/jobs/models.py dashboard/jobs/tests.py dashboard/jobs/migrations/0001_initial.py
git commit -m "feat(dashboard): model Job cu schema canonică (JOB-39)"
```

---

### Task 3: Extractorul `extract_job(url)`

**Files:**
- Create: `dashboard/jobs/extractor.py`
- Modify: `dashboard/jobs/tests.py` (adaugă `ExtractorTests`)

**Interfaces:**
- Produces: `jobs.extractor.extract_job(url: str) -> dict`. Întoarce mereu un dict cu cheile: `url, title, company, location, description, email_hr, salary_min, salary_max, language, date_posted, source, extraction_status, raw_json`. `extraction_status ∈ {"ok","partial","error"}`. Nu ridică excepții.

- [ ] **Step 1: Scrie testele care eșuează**

Adaugă la începutul `dashboard/jobs/tests.py` (după docstring) importurile și la final clasa:
```python
import os
from unittest.mock import Mock, patch
from django.test import SimpleTestCase
from jobs import extractor


def _mock_http(text):
    resp = Mock()
    resp.text = text
    resp.raise_for_status = Mock()
    return resp


def _mock_anthropic(json_text):
    client = Mock()
    msg = Mock()
    msg.content = [Mock(text=json_text)]
    client.messages.create.return_value = msg
    return client


class ExtractorTests(SimpleTestCase):
    @patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"})
    @patch("jobs.extractor.anthropic.Anthropic")
    @patch("jobs.extractor.httpx.get")
    def test_maps_ai_json_to_canonical_fields(self, mock_get, mock_anthropic):
        mock_get.return_value = _mock_http("<html><body>Python Dev at ACME</body></html>")
        mock_anthropic.return_value = _mock_anthropic(
            '{"title": "Python Developer", "company": "ACME", "location": "Berlin", '
            '"description": "Build things", "email_hr": "jobs@acme.de", '
            '"salary_min": 60000, "salary_max": 80000, "language": "en", '
            '"date_posted": "2026-07-01"}'
        )
        result = extractor.extract_job("https://acme.de/jobs/1")
        self.assertEqual(result["extraction_status"], "ok")
        self.assertEqual(result["title"], "Python Developer")
        self.assertEqual(result["company"], "ACME")
        self.assertEqual(result["salary_min"], 60000)
        self.assertEqual(result["email_hr"], "jobs@acme.de")
        self.assertEqual(result["url"], "https://acme.de/jobs/1")
        self.assertEqual(result["source"], "dashboard")

    @patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"})
    @patch("jobs.extractor.anthropic.Anthropic")
    @patch("jobs.extractor.httpx.get")
    def test_salary_as_string_is_coerced(self, mock_get, mock_anthropic):
        mock_get.return_value = _mock_http("<html>x</html>")
        mock_anthropic.return_value = _mock_anthropic(
            '{"title": "X", "salary_min": "60.000 €", "salary_max": null}'
        )
        result = extractor.extract_job("https://acme.de/jobs/9")
        self.assertEqual(result["extraction_status"], "ok")
        self.assertEqual(result["salary_min"], 60000)
        self.assertIsNone(result["salary_max"])

    @patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"})
    @patch("jobs.extractor.anthropic.Anthropic")
    @patch("jobs.extractor.httpx.get")
    def test_non_json_ai_response_is_partial(self, mock_get, mock_anthropic):
        mock_get.return_value = _mock_http("<html>x</html>")
        mock_anthropic.return_value = _mock_anthropic("Îmi pare rău, nu pot extrage.")
        result = extractor.extract_job("https://acme.de/jobs/2")
        self.assertEqual(result["extraction_status"], "partial")
        self.assertEqual(result["title"], "")
        self.assertIn("raw", result["raw_json"])

    @patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"})
    @patch("jobs.extractor.httpx.get", side_effect=Exception("timeout"))
    def test_fetch_failure_is_error(self, mock_get):
        result = extractor.extract_job("https://broken.example/x")
        self.assertEqual(result["extraction_status"], "error")
        self.assertEqual(result["title"], "")
        self.assertIn("error", result["raw_json"])

    @patch.dict(os.environ, {}, clear=True)
    def test_missing_api_key_is_error(self):
        result = extractor.extract_job("https://acme.de/jobs/3")
        self.assertEqual(result["extraction_status"], "error")
        self.assertIn("ANTHROPIC_API_KEY", result["raw_json"]["error"])
```

- [ ] **Step 2: Rulează — trebuie să eșueze**

Run: `python dashboard/manage.py test jobs.tests.ExtractorTests -v 2`
Expected: FAIL cu `ModuleNotFoundError: No module named 'jobs.extractor'`.

- [ ] **Step 3: Implementează extractorul**

Creează `dashboard/jobs/extractor.py`:
```python
"""Extragere câmpuri canonice dintr-un URL de job, cu Claude.

Funcție pură (fără Django), ușor de testat. Întoarce mereu un dict;
codează eșecul în `extraction_status`, nu ridică excepții spre view.
"""
import json
import os
import re
from pathlib import Path

import anthropic
import httpx
import yaml
from bs4 import BeautifulSoup

# dashboard/jobs/extractor.py → parents[2] = rădăcina repo-ului
_REPO_ROOT = Path(__file__).resolve().parents[2]
_SETTINGS = _REPO_ROOT / "config" / "settings.yaml"
_DEFAULT_MODEL = "claude-sonnet-4-6"

_MAX_DESCRIPTION_CHARS = 5000
_MAX_PAGE_TEXT_CHARS = 15000

_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

_STRING_FIELDS = ("title", "company", "location", "email_hr", "language", "date_posted")

_PROMPT = """Ești un extractor de date pentru anunțuri de job. Mai jos e textul unei pagini de job.
Extrage câmpurile și răspunde DOAR cu un obiect JSON valid (fără markdown, fără text în plus):

- title: titlul postului (string)
- company: numele companiei (string)
- location: localitate/regiune (string)
- description: descrierea postului, rezumată la esențial (string, max ~2000 caractere)
- email_hr: email de contact/aplicare dacă apare, altfel null
- salary_min: salariul minim ca număr întreg (fără simboluri), altfel null
- salary_max: salariul maxim ca număr întreg, altfel null
- language: limba anunțului ca cod ISO ("de", "en", "ro"), altfel null
- date_posted: data publicării dacă apare (string), altfel null

Folosește null unde informația lipsește. Nu inventa date.

TEXT PAGINĂ:
{page}
"""


def _ai_model() -> str:
    """Modelul AI din config/settings.yaml, cu fallback."""
    try:
        with open(_SETTINGS, encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        return (cfg.get("ai") or {}).get("model") or _DEFAULT_MODEL
    except Exception:
        return _DEFAULT_MODEL


def _blank_result(url: str, status: str, raw) -> dict:
    """Dict canonic gol, cu statusul dat."""
    return {
        "url": url,
        "title": "", "company": "", "location": "", "description": "",
        "email_hr": "", "salary_min": None, "salary_max": None,
        "language": "", "date_posted": "",
        "source": "dashboard",
        "extraction_status": status,
        "raw_json": raw if isinstance(raw, dict) else {"raw": raw},
    }


def _fetch_text(url: str) -> str:
    """Descarcă pagina și o reduce la text curat, trunchiat."""
    headers = {"User-Agent": _USER_AGENT, "Accept-Language": "en-US,en;q=0.9,de;q=0.8"}
    resp = httpx.get(url, headers=headers, timeout=15, follow_redirects=True)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    return soup.get_text(" ", strip=True)[:_MAX_PAGE_TEXT_CHARS]


def _call_ai(page_text: str) -> str:
    """Întoarce textul brut al răspunsului AI (posibil non-JSON)."""
    client = anthropic.Anthropic()
    response = client.messages.create(
        model=_ai_model(),
        max_tokens=1000,
        temperature=0,
        messages=[{"role": "user", "content": _PROMPT.format(page=page_text)}],
    )
    return response.content[0].text.strip()


def _coerce_int(value):
    """Convertește '60.000 €' / '60000' / 60000 → int; altfel None."""
    if value is None:
        return None
    if isinstance(value, int):
        return value
    digits = re.sub(r"[^\d]", "", str(value))
    return int(digits) if digits else None


def extract_job(url: str) -> dict:
    """Fetch → text → Claude → dict canonic. Nu ridică excepții."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return _blank_result(url, "error", {"error": "ANTHROPIC_API_KEY lipsă din mediu"})

    try:
        page_text = _fetch_text(url)
    except Exception as e:
        return _blank_result(url, "error", {"error": f"fetch eșuat: {e}"})

    try:
        raw_text = _call_ai(page_text)
    except Exception as e:
        return _blank_result(url, "error", {"error": f"AI eșuat: {e}"})

    cleaned = re.sub(r"^```json\s*", "", raw_text)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        return _blank_result(url, "partial", {"raw": raw_text})

    result = _blank_result(url, "ok", data if isinstance(data, dict) else {"raw": data})
    if isinstance(data, dict):
        for field in _STRING_FIELDS:
            if data.get(field) is not None:
                result[field] = str(data[field]).strip()
        if data.get("description") is not None:
            result["description"] = str(data["description"])[:_MAX_DESCRIPTION_CHARS]
        result["salary_min"] = _coerce_int(data.get("salary_min"))
        result["salary_max"] = _coerce_int(data.get("salary_max"))
    return result
```

- [ ] **Step 4: Rulează — trebuie să treacă**

Run: `python dashboard/manage.py test jobs.tests.ExtractorTests -v 2`
Expected: 5 teste PASS.

- [ ] **Step 5: Commit**

```bash
git add dashboard/jobs/extractor.py dashboard/jobs/tests.py
git commit -m "feat(dashboard): extractor URL job → câmpuri canonice via Claude (JOB-39)"
```

---

### Task 4: Views + URLs + admin

**Files:**
- Modify: `dashboard/jobs/views.py`
- Create: `dashboard/jobs/urls.py`
- Modify: `dashboard/dashboard/urls.py`
- Modify: `dashboard/jobs/admin.py`
- Modify: `dashboard/jobs/tests.py` (adaugă `ViewTests`)

**Interfaces:**
- Consumes: `jobs.extractor.extract_job`, `jobs.models.Job`.
- Produces: rute cu numele `index`, `add_job`, `job_detail`, `delete_job`.

- [ ] **Step 1: Scrie testele care eșuează**

Adaugă la finalul `dashboard/jobs/tests.py`:
```python
from django.urls import reverse
from jobs.models import Job


def _ok_extraction(url):
    return {
        "url": url, "title": "Python Dev", "company": "ACME", "location": "Berlin",
        "description": "desc", "email_hr": "jobs@acme.de", "salary_min": 60000,
        "salary_max": 80000, "language": "en", "date_posted": "2026-07-01",
        "source": "dashboard", "extraction_status": "ok", "raw_json": {},
    }


class ViewTests(TestCase):
    @patch("jobs.views.extract_job")
    def test_add_job_creates_row_and_redirects(self, mock_extract):
        url = "https://acme.de/jobs/1"
        mock_extract.return_value = _ok_extraction(url)
        resp = self.client.post(reverse("add_job"), {"url": url})
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(Job.objects.count(), 1)
        self.assertEqual(Job.objects.get().title, "Python Dev")

    @patch("jobs.views.extract_job")
    def test_duplicate_url_updates_not_duplicates(self, mock_extract):
        url = "https://acme.de/jobs/1"
        mock_extract.return_value = _ok_extraction(url)
        self.client.post(reverse("add_job"), {"url": url})
        updated = _ok_extraction(url)
        updated["title"] = "Senior Python Dev"
        mock_extract.return_value = updated
        self.client.post(reverse("add_job"), {"url": url})
        self.assertEqual(Job.objects.count(), 1)
        self.assertEqual(Job.objects.get().title, "Senior Python Dev")

    def test_invalid_url_rejected(self):
        # fără follow: nu randăm index.html (nu există până la Task 5)
        resp = self.client.post(reverse("add_job"), {"url": "not-a-url"})
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(Job.objects.count(), 0)

    def test_delete_job(self):
        job = Job.objects.create(url="https://a.co/1", title="Alpha")
        resp = self.client.post(reverse("delete_job", args=[job.pk]))
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(Job.objects.count(), 0)
```

- [ ] **Step 2: Rulează — trebuie să eșueze**

Run: `python dashboard/manage.py test jobs.tests.ViewTests -v 2`
Expected: FAIL (`NoReverseMatch` — rutele nu există).

- [ ] **Step 3: Implementează views**

Înlocuiește `dashboard/jobs/views.py` cu:
```python
"""Views dashboard: index (formular + tabel), add (PRG), detail, delete."""
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.validators import URLValidator
from django.shortcuts import get_object_or_404, redirect, render

from .extractor import extract_job
from .models import Job


def index(request):
    return render(request, "jobs/index.html", {"jobs": Job.objects.all()})


def add_job(request):
    if request.method != "POST":
        return redirect("index")

    url = (request.POST.get("url") or "").strip()
    try:
        URLValidator()(url)
    except ValidationError:
        messages.error(request, "URL invalid.")
        return redirect("index")

    data = extract_job(url)
    defaults = {k: v for k, v in data.items() if k != "url"}
    job, created = Job.objects.update_or_create(url=url, defaults=defaults)

    status = data["extraction_status"]
    if status == "ok":
        verb = "Adăugat" if created else "Actualizat"
        messages.success(request, f"{verb}: {job.title or url}")
    elif status == "partial":
        messages.warning(request, "Extragere parțială — AI n-a întors JSON valid. Verifică manual.")
    else:
        err = (data.get("raw_json") or {}).get("error", "eroare necunoscută")
        messages.error(request, f"Extragere eșuată: {err}")
    return redirect("index")


def job_detail(request, pk):
    return render(request, "jobs/detail.html", {"job": get_object_or_404(Job, pk=pk)})


def delete_job(request, pk):
    if request.method == "POST":
        Job.objects.filter(pk=pk).delete()
        messages.info(request, "Rând șters.")
    return redirect("index")
```

- [ ] **Step 4: Rutele app-ului**

Creează `dashboard/jobs/urls.py`:
```python
from django.urls import path

from . import views

urlpatterns = [
    path("", views.index, name="index"),
    path("add", views.add_job, name="add_job"),
    path("job/<int:pk>/", views.job_detail, name="job_detail"),
    path("job/<int:pk>/delete", views.delete_job, name="delete_job"),
]
```

- [ ] **Step 5: Include rutele în proiect**

Înlocuiește `dashboard/dashboard/urls.py` cu:
```python
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", include("jobs.urls")),
]
```

- [ ] **Step 6: Înregistrează modelul în admin**

Înlocuiește `dashboard/jobs/admin.py` cu:
```python
from django.contrib import admin

from .models import Job


@admin.register(Job)
class JobAdmin(admin.ModelAdmin):
    list_display = ("title", "company", "location", "extraction_status", "created_at")
    list_filter = ("extraction_status", "source")
    search_fields = ("title", "company", "url")
```

- [ ] **Step 7: Rulează — trebuie să treacă**

Run: `python dashboard/manage.py test jobs.tests.ViewTests -v 2`
Expected: 4 teste PASS. (Toate folosesc redirect 302, fără randare de template — deci nu depind de Task 5. Randarea `index`/`detail` e testată în Task 5.)

- [ ] **Step 8: Commit**

```bash
git add dashboard/jobs/views.py dashboard/jobs/urls.py dashboard/dashboard/urls.py dashboard/jobs/admin.py dashboard/jobs/tests.py
git commit -m "feat(dashboard): views + rute add/detail/delete cu PRG și dedupe (JOB-39)"
```

---

### Task 5: Template-uri + UI

**Files:**
- Create: `dashboard/jobs/templates/jobs/base.html`, `index.html`, `detail.html`

**Interfaces:**
- Consumes: context `{"jobs": QuerySet[Job]}` (index), `{"job": Job}` (detail); rutele `index`, `add_job`, `job_detail`, `delete_job`; `django.contrib.messages`.

- [ ] **Step 1: Scrie testele de randare care eșuează**

Adaugă la finalul `dashboard/jobs/tests.py`:
```python
class RenderTests(TestCase):
    def test_index_lists_jobs(self):
        Job.objects.create(url="https://a.co/1", title="Alpha", company="A")
        resp = self.client.get(reverse("index"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Alpha")

    def test_empty_index_shows_hint(self):
        resp = self.client.get(reverse("index"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Niciun job încă")

    def test_detail_renders(self):
        job = Job.objects.create(url="https://a.co/1", title="Alpha", description="Detalii aici")
        resp = self.client.get(reverse("job_detail", args=[job.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Detalii aici")
```

Run: `python dashboard/manage.py test jobs.tests.RenderTests -v 2`
Expected: FAIL cu `TemplateDoesNotExist: jobs/index.html`.

- [ ] **Step 2: Invocă skill-ul de design UI**

Înainte de a scrie HTML/CSS, invocă skill-ul `ui-ux-pro-max` (instrucțiune globală). Codul de mai jos e un schelet funcțional, curat și responsiv; rafinează vizualul (spacing, tipografie, culori, stări) conform skill-ului, păstrând structura și numele rutelor/variabilelor.

- [ ] **Step 3: Base template**

Creează `dashboard/jobs/templates/jobs/base.html`:
```html
<!doctype html>
<html lang="ro">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{% block title %}Job Dashboard{% endblock %}</title>
  <style>
    :root { --bg:#0f1115; --card:#1a1d24; --line:#2a2f3a; --fg:#e6e8ec; --muted:#9aa3b2;
            --ok:#2f9e44; --warn:#e8a13a; --err:#e5484d; --accent:#4c8bf5; }
    * { box-sizing: border-box; }
    body { margin:0; background:var(--bg); color:var(--fg);
           font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif; }
    .wrap { max-width:1100px; margin:0 auto; padding:32px 20px; }
    h1 { font-size:22px; margin:0 0 4px; }
    .sub { color:var(--muted); margin:0 0 24px; }
    form.add { display:flex; gap:10px; margin-bottom:24px; }
    input[type=url] { flex:1; padding:11px 14px; background:var(--card); border:1px solid var(--line);
                      border-radius:9px; color:var(--fg); font-size:15px; }
    button { padding:11px 18px; border:0; border-radius:9px; background:var(--accent); color:#fff;
             font-weight:600; cursor:pointer; }
    button.ghost { background:transparent; border:1px solid var(--line); color:var(--muted); padding:6px 12px; }
    table { width:100%; border-collapse:collapse; background:var(--card);
            border:1px solid var(--line); border-radius:12px; overflow:hidden; }
    th, td { text-align:left; padding:11px 14px; border-bottom:1px solid var(--line); font-size:14px; }
    th { color:var(--muted); font-weight:600; font-size:12px; text-transform:uppercase; letter-spacing:.04em; }
    tr:last-child td { border-bottom:0; }
    a { color:var(--accent); text-decoration:none; }
    .pill { font-size:11px; padding:2px 8px; border-radius:20px; font-weight:600; }
    .pill.ok{background:rgba(47,158,68,.15);color:var(--ok);}
    .pill.partial{background:rgba(232,161,58,.15);color:var(--warn);}
    .pill.error{background:rgba(229,72,77,.15);color:var(--err);}
    .msgs { list-style:none; padding:0; margin:0 0 18px; }
    .msgs li { padding:10px 14px; border-radius:9px; margin-bottom:8px; font-size:14px; }
    .msgs .success{background:rgba(47,158,68,.12);color:var(--ok);}
    .msgs .warning{background:rgba(232,161,58,.12);color:var(--warn);}
    .msgs .error{background:rgba(229,72,77,.12);color:var(--err);}
    .msgs .info{background:rgba(76,139,245,.12);color:var(--accent);}
    .empty { color:var(--muted); text-align:center; padding:40px; }
  </style>
</head>
<body>
  <div class="wrap">
    {% if messages %}
      <ul class="msgs">
        {% for m in messages %}<li class="{{ m.tags }}">{{ m }}</li>{% endfor %}
      </ul>
    {% endif %}
    {% block content %}{% endblock %}
  </div>
</body>
</html>
```

- [ ] **Step 4: Index template (formular + tabel)**

Creează `dashboard/jobs/templates/jobs/index.html`:
```html
{% extends "jobs/base.html" %}
{% block content %}
  <h1>Job Dashboard</h1>
  <p class="sub">Lipește link-ul unui job — Claude extrage structura în tabel.</p>

  <form class="add" method="post" action="{% url 'add_job' %}">
    {% csrf_token %}
    <input type="url" name="url" placeholder="https://…/job/…" required autofocus>
    <button type="submit">Adaugă</button>
  </form>

  {% if jobs %}
  <table>
    <thead>
      <tr><th>Titlu</th><th>Companie</th><th>Locație</th><th>Email HR</th>
          <th>Salariu</th><th>Status</th><th>Găsit</th><th></th></tr>
    </thead>
    <tbody>
      {% for job in jobs %}
      <tr>
        <td><a href="{% url 'job_detail' job.pk %}">{{ job.title|default:"(fără titlu)" }}</a></td>
        <td>{{ job.company|default:"—" }}</td>
        <td>{{ job.location|default:"—" }}</td>
        <td>{{ job.email_hr|default:"—" }}</td>
        <td>{% if job.salary_min or job.salary_max %}{{ job.salary_min|default:"?" }}–{{ job.salary_max|default:"?" }}{% else %}—{% endif %}</td>
        <td><span class="pill {{ job.extraction_status }}">{{ job.extraction_status }}</span></td>
        <td>{{ job.created_at|date:"d.m H:i" }}</td>
        <td>
          <form method="post" action="{% url 'delete_job' job.pk %}" onsubmit="return confirm('Ștergi rândul?');">
            {% csrf_token %}<button class="ghost" type="submit">Șterge</button>
          </form>
        </td>
      </tr>
      {% endfor %}
    </tbody>
  </table>
  {% else %}
  <div class="empty">Niciun job încă. Lipește primul link mai sus.</div>
  {% endif %}
{% endblock %}
```

- [ ] **Step 5: Detail template**

Creează `dashboard/jobs/templates/jobs/detail.html`:
```html
{% extends "jobs/base.html" %}
{% block title %}{{ job.title|default:"Job" }}{% endblock %}
{% block content %}
  <p><a href="{% url 'index' %}">← Înapoi</a></p>
  <h1>{{ job.title|default:"(fără titlu)" }}</h1>
  <p class="sub">{{ job.company|default:"—" }} · {{ job.location|default:"—" }} ·
     <span class="pill {{ job.extraction_status }}">{{ job.extraction_status }}</span></p>
  <p><a href="{{ job.url }}" target="_blank" rel="noopener">{{ job.url }}</a></p>
  <p><strong>Email HR:</strong> {{ job.email_hr|default:"—" }} &nbsp;
     <strong>Salariu:</strong> {{ job.salary_min|default:"?" }}–{{ job.salary_max|default:"?" }} &nbsp;
     <strong>Limbă:</strong> {{ job.language|default:"—" }}</p>
  <h3>Descriere</h3>
  <p style="white-space:pre-wrap;">{{ job.description|default:"(goală)" }}</p>
  <h3>JSON brut</h3>
  <pre style="background:#1a1d24;padding:14px;border-radius:9px;overflow:auto;">{{ job.raw_json }}</pre>
{% endblock %}
```

- [ ] **Step 6: Rulează toată suita dashboard — trebuie să treacă**

Run: `python dashboard/manage.py test jobs -v 2`
Expected: TOATE testele PASS (model + extractor + views + render, inclusiv `index`/`detail` care randează acum).

- [ ] **Step 7: Verificare manuală (opțional, dar recomandat)**

```bash
export ANTHROPIC_API_KEY="$(grep -m1 ANTHROPIC_API_KEY .env | cut -d= -f2-)"
python dashboard/manage.py migrate
python dashboard/manage.py runserver
# deschide http://127.0.0.1:8000/ , lipește un URL real de job, verifică rândul
```

- [ ] **Step 8: Commit**

```bash
git add dashboard/jobs/templates/ dashboard/jobs/tests.py
git commit -m "feat(dashboard): template-uri index/detail + UI (JOB-39)"
```

---

## Verificare finală (după toate task-urile)

- [ ] Suita dashboard verde: `python dashboard/manage.py test jobs`
- [ ] Suita pipeline neatinsă: `python -m pytest -q` (183 teste PASS ca înainte)
- [ ] `git status` curat; `dashboard/db.sqlite3` NU e trackuit.
