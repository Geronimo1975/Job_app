# Design: Dashboard „Paste link → vezi structura"

**Data:** 2026-07-23
**Status:** aprobat (brainstorming)
**Autor:** Claude Code + Geronimo

## Scop

Un dashboard web local în care lipești link-ul unui job și, în câteva secunde,
vezi câmpurile structurate ale jobului (title, company, location, description,
email HR, salariu) într-un tabel persistent. Fiecare job introdus rămâne salvat
între sesiuni.

Nu este un scraper de listing — este extragere pe **un URL individual** de job,
ceva ce pipeline-ul actual NU face (`CareerPageScraper` găsește doar link-uri pe
o pagină de listing și lasă `description` gol).

## Decizii luate (brainstorming)

| Decizie | Alegere | Motiv |
|---|---|---|
| Cum se completează câmpurile | **AI extrage din pagină** (Claude) | Merge pe orice layout de site; cost mic per job |
| Persistență | **SQLite local** (Django ORM) | Istoric între sesiuni |
| Integrare cu pipeline | **Separat, doar vizualizare** | Zero risc pentru Sheet/n8n de producție |
| Stack | **Django** | Cerut explicit de utilizator |

## Non-scop (YAGNI)

- Fără autentificare (rulează local, un singur utilizator).
- Fără push în Google Sheet / n8n. Dashboard-ul NU importă din `sender/` și NU
  atinge producția.
- Fără editare inline a rândurilor (doar vizualizare + ștergere).
- Fără paginare la prima iterație (se adaugă când lista crește).
- Fără deploy / server extern — rulează pe `runserver` local.

## Arhitectură

Proiect Django de sine stătător în `dashboard/`, izolat de pipeline:

```
dashboard/
├── manage.py
├── db.sqlite3            # gitignored
├── dashboard/            # config proiect
│   ├── __init__.py
│   ├── settings.py
│   ├── urls.py
│   └── wsgi.py
└── jobs/                 # aplicația
    ├── __init__.py
    ├── models.py         # modelul Job
    ├── extractor.py      # extract_job(url) -> dict
    ├── views.py          # index, add_job, job_detail, delete_job
    ├── urls.py
    ├── admin.py
    ├── migrations/
    ├── templates/jobs/
    │   ├── index.html    # formular + tabel
    │   └── detail.html   # descriere completă + JSON brut
    └── tests.py
```

**Singura punte cu proiectul existent:** `extractor.py` citește
`config/settings.yaml` (via un helper mic, NU importă `agents/`) ca să
folosească același model AI (`ai.model`, ex. `claude-sonnet-4-6`) și aceleași
nume de câmpuri canonice ca `scrapers/base.py::make_job()`. Cheia AI vine din
`ANTHROPIC_API_KEY` (env), la fel ca în restul proiectului — `anthropic.Anthropic()`
o citește implicit.

## Componente

### 1. `jobs/models.py` — modelul `Job`

Câmpuri care oglindesc schema canonică din `make_job()`:

| Câmp | Tip Django | Note |
|---|---|---|
| `url` | `URLField(unique=True)` | cheia de deduplicare |
| `title` | `CharField(max_length=300, blank=True)` | |
| `company` | `CharField(max_length=200, blank=True)` | |
| `location` | `CharField(max_length=200, blank=True)` | |
| `description` | `TextField(blank=True)` | trunchiat la 5000 char, ca în `make_job` |
| `email_hr` | `EmailField(blank=True)` | |
| `salary_min` | `IntegerField(null=True, blank=True)` | |
| `salary_max` | `IntegerField(null=True, blank=True)` | |
| `language` | `CharField(max_length=5, default="")` | |
| `date_posted` | `CharField(max_length=40, blank=True)` | text liber, ca în canonical |
| `source` | `CharField(max_length=40, default="dashboard")` | |
| `extraction_status` | `CharField(max_length=10)` | `ok` / `partial` / `error` |
| `raw_json` | `JSONField(default=dict)` | extragerea AI brută, pentru debug / câmpuri extra |
| `created_at` | `DateTimeField(auto_now_add=True)` | înlocuiește `scraped_at` |

`class Meta: ordering = ["-created_at"]` — cele mai noi primele.

### 2. `jobs/extractor.py` — `extract_job(url) -> dict`

Funcție pură (fără Django), ușor de testat:

1. **Fetch** — `httpx.get(url, headers={User-Agent realist}, timeout=15, follow_redirects=True)`.
2. **Text** — `BeautifulSoup(html).get_text(" ")`, curățat de script/style, trunchiat
   (ex. ~15000 char) ca să limiteze tokenurile trimise la AI.
3. **AI** — `anthropic.Anthropic().messages.create(model=<din settings.yaml>, temperature=0, max_tokens=1000, ...)`
   cu prompt strict: „Extrage următoarele câmpuri ca JSON valid: title, company,
   location, description, email_hr, salary_min, salary_max, language,
   date_posted. Folosește null unde lipsește. Răspunde DOAR cu JSON."
4. **Parse** — curăță eventuale ```json fences (ca în `analyzer_agent`), `json.loads`,
   mapează la câmpurile canonice, `extraction_status="ok"`.
5. **Return** — dict cu câmpurile canonice + `url` + `extraction_status` + `raw_json`.

Întoarce mereu un dict; nu ridică excepții spre view — codează eșecul în
`extraction_status` (vezi „Tratarea erorilor").

### 3. `jobs/views.py`

- `index` (GET) — randează `index.html`: formular URL sus + tabel cu toate `Job`
  (ordonate `-created_at`).
- `add_job` (POST) — validează URL-ul → `extract_job(url)` → `Job.objects.update_or_create(url=..., defaults=...)`
  → redirect la `index` (pattern Post/Redirect/Get, evită re-submit). Mesaje prin
  `django.contrib.messages`.
- `job_detail` (GET) — descrierea completă + `raw_json` formatat.
- `delete_job` (POST) — șterge un rând, redirect la index.

### 4. `jobs/templates/` — UI

O pagină principală: câmp URL + buton „Adaugă" sus, tabel dedesubt cu coloanele
cheie (title, company, location, email, salariu, status, data) + link spre
detaliu + buton ștergere. Rând colorat subtil după `extraction_status`
(`error` = roșu discret, `partial` = galben). Design curat, responsiv —
**la implementare se invocă skill-ul `ui-ux-pro-max`** înainte de a scrie HTML/CSS
(instrucțiune globală).

## Flux de date

```
lipești URL
  → POST /add
    → extract_job(url)
        → httpx GET  (HTML)
        → BeautifulSoup  (text)
        → Claude messages.create  (JSON)
        → parse + map
    → Job.objects.update_or_create(url=...)
  → redirect GET /
    → tabelul afișează rândul nou
```

## Tratarea erorilor (nimic silențios)

| Situație | Comportament |
|---|---|
| URL invalid (validator Django) | eroare în formular, nu se creează rând |
| Fetch eșuat (timeout / 4xx / 5xx) | rând salvat cu `extraction_status="error"`, doar `url`; mesaj vizibil |
| AI întoarce non-JSON | `extraction_status="partial"`, păstrează ce s-a putut, `raw_json` = textul brut, loghează pe stderr |
| URL duplicat | `update_or_create` actualizează rândul existent (nu dublură); mesaj „actualizat" |
| `ANTHROPIC_API_KEY` lipsă | extractorul detectează și întoarce `error` cu mesaj clar; pagina îl afișează, nu crash |

## Testare

Teste Django (`jobs/tests.py`), rulate cu `python dashboard/manage.py test jobs`.
NU lovesc rețeaua — httpx și anthropic mock-uite:

- `extract_job` cu httpx + anthropic mock-uite → verifică maparea corectă a
  câmpurilor din JSON-ul AI la dict-ul canonical.
- `extract_job` când AI întoarce non-JSON → `extraction_status="partial"`.
- `extract_job` când httpx ridică excepție → `extraction_status="error"`, doar url.
- View `add_job` cu `extract_job` mock-uit → `Job` salvat + redirect (302).
- View `add_job` cu URL duplicat → un singur `Job` în DB (update, nu insert).

Suita pytest existentă rămâne neatinsă (dashboard-ul e izolat).

## Dependențe & rulare

- Adaugă `Django>=5.0` în `requirements.txt`.
- `.gitignore`: `dashboard/db.sqlite3`.
- Setup:
  ```bash
  source .venv/bin/activate
  pip install -r requirements.txt
  python dashboard/manage.py migrate
  python dashboard/manage.py runserver
  # → http://127.0.0.1:8000/
  ```

## Riscuri & atenționări

- **Calitatea extragerii depinde de pagină.** Site-uri cu conținut randat JS
  (SPA) pot întoarce HTML gol → AI nu are ce extrage → `partial`. Acceptabil la
  prima iterație; dacă devine frecvent, o iterație viitoare poate adăuga
  Playwright (deja opțional în requirements).
- **Cost AI** — un apel Claude per job introdus. Neglijabil la volum manual.
- Izolarea față de pipeline e strictă: nicio modificare în `agents/`, `sender/`,
  `main.py` sau workflow-urile n8n.
