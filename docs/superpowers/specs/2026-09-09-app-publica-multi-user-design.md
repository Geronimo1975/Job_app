# Spec: Job_app public, multi-utilizator — trigger-ul din Sheet mutat în Django + PostgreSQL (JOB-60)

## Context

Azi fluxul de producție al owner-ului trăiește în Google Sheet: lipești un link în col E „Typ" și Apps Script-ul cheamă serviciul FastAPI de pe VPS, care completează rândul; scrii `send` în col M „Status" și același serviciu generează Anschreiben-ul + emailul și trimite la „Kontakt Email". n8n nu e pe traseul trigger-ului, doar pe upsert/read.

Owner-ul vrea aplicația **publică**, folosită de mai multe persoane, cu exact aceleași două declanșatoare, dar fără Google Sheet per utilizator și fără Apps Script. Fundamentul e Django.

Dashboard-ul Django existent (`dashboard/`) are deja jumătate din mecanică (`add_job` + `extractor.py`, „Execută" = `generate_package`, „Transmite" = `send_package`, stări `draft→generated→sending→sent|error`), dar e strict personal: **zero autentificare**, un singur profil în `config/settings.yaml`, SQLite, rate limit într-un fișier global, toate operațiile lungi (AI 10-30 s, SMTP + `sleep(30)`) rulează blocant în request.

### Decizii luate cu owner-ul (fixe)

| Decizie | Alegere |
| --- | --- |
| Trigger 1 | link lipit → scraping automat în fundal, rândul se completează, stare vizibilă |
| Trigger 2 | „Trimite automat" pe rând → în fundal: Anschreiben AI + email personalizat + PDF-uri → trimitere la `email_hr`; stare `queued → running → sent/error` |
| Expeditor | contul SMTP **propriu al utilizatorului** (criptat în DB); fără expeditor comun |
| Cost AI | cheia platformei, cotă zilnică per utilizator |
| Google Sheet | **niciun rol** pentru utilizatorii noi; PostgreSQL = sursa de adevăr. CLI + n8n + Sheet rămân doar pentru owner (flag `sheet_sync`) |
| Mecanism trigger | coadă de task-uri **în PostgreSQL**, worker Django separat; fără Redis, fără webhook Supabase |
| Hosting | VPS-ul existent (Traefik deja acolo), docker-compose: web + 2 workeri + **PostgreSQL pe server**, volume persistente. Local = SQLite pentru dev/teste |

### Rezultat urmărit

Un utilizator străin își face cont, completează profilul (contact, skill-uri, CV DE/EN, certificate, SMTP propriu), lipește un link, vede rândul completându-se singur, apasă „Trimite automat" și emailul pleacă din contul lui. Owner-ul își păstrează contul cu datele importate din `settings.yaml` și sincronizarea cu Sheet-ul.

---

## Design

### 1. Coada: `django.tasks` (Django 6.1, nativ) + `django-tasks-db`

- Django 6.1 din `.venv` are `django.tasks` (`@task`, `enqueue`, `TaskResult`), dar doar backend-urile `dummy`/`immediate`. Backend-ul DB + `manage.py db_worker` vin din pachetul separat `django-tasks-db` (aceeași echipă ca framework-ul).
- **Semantică at-most-once**: worker-ul face claim cu `select_for_update`; un task nu rulează niciodată de două ori. Exact ce vrem pentru email. Peste asta, **claim atomic pe `Job`** în interiorul fiecărui task (`filter(pk, send_state="queued").update(send_state="running", busy_since=now) == 1`), deci un dublu POST e inofensiv.
- Fără retry automat: extragerea o reia userul („Re-extrage"), trimiterea **nu se repetă niciodată automat**.
- Două cozi, doi workeri: `scrape` și `apply` (o trimitere lentă nu blochează extragerile). Scalare: `--scale worker-apply=2`, claim-ul pe Job o face sigură.
- Teste: `ImmediateBackend` cu `ENQUEUE_ON_COMMIT=False` (în `TestCase` tranzacția nu se comite niciodată).
- **Plan B** (dacă `django-tasks-db` nu trece `manage.py check` pe 6.1, verificat în Faza 0): tabel propriu `QueuedTask` + comandă `worker` cu `select_for_update(skip_locked=True)`; interfața din `tasks.py` rămâne identică.

### 2. Mașina de stări pe `Job`

Câmpuri (migrație): `extraction_status` primește valorile `pending|scraping|ok|partial|error` (default `pending`; rândurile vechi rămân `ok`), `extraction_error`, `send_state` devine `draft|queued|running|generated|sent|error` (`sending` dispare; data migration `sending→error`), `apply_mode` (`auto`/`manual`), `busy_since`, `active_task_id`. `Job.is_busy` = `extraction_status in (pending, scraping) or send_state in (queued, running)`.

| Trigger | Tranziție | Cine |
| --- | --- | --- |
| 1 | `add_job`/`scrape_job` → `pending` + enqueue `extract_job_task` | user |
| 1 | claim `pending→scraping` → extragere → `ok/partial/error` | worker |
| 2 | `apply_job`: gărzi (extras, `email_hr` nevid, `profile.smtp_configured`, `sent_at is None`, cotă) → claim `draft/error/generated → queued` (`auto`) + enqueue `apply_job_task` | user |
| 2 | claim `queued→running` → `_generate` + `_send` în **același task** → `sent` / `error` | worker |
| manual | `execute_job` → `queued` (`manual`) + `generate_job_task` → `generated`; `transmit_job` → `queued` + `send_job_task` → `sent` | user/worker |
| sweeper | `scraping` > 5 min → `error` „întreruptă, Re-extrage"; `running` > 10 min → `error` „verifică Trimise în contul SMTP înainte de a reîncerca" (**niciodată re-enqueue**) | `expire_stale_jobs()` din `index`/`job_row`, ca `expire_stale_runs` |
| recover | `error` cu `generated_at` → `generated` (existent) | user |

Cazuri: `email_hr` gol → buton dezactivat + task-ul re-verifică; adresă `_RANK_AVOID` (`privacy@`, `noreply@`) după extragere → golită, notată în `raw_json["email_rejected"]`; fallback-ul silențios la șablon din `CoverLetterGenerator` → `generate(..., raise_on_ai_error=True)` din dashboard, deci eroare AI = rând `error`, nu scrisoare generică; cotă epuizată → `error` cu mesaj; SMTP → `_friendly_error()` mapează `SMTPAuthenticationError`/`SMTPConnectError`/`SMTPRecipientsRefused`. `sent_at` nenul blochează orice re-trimitere. `raw_json["log"]` = jurnal per rând (ultimele 20 evenimente).

**Dry-run** dispare ca checkbox per trimitere; devine setare de mediu `EMAIL_DRY_RUN` (implicit `1` local, `0` în producție).

### 3. Tenancy și modele noi (`dashboard/jobs/models.py`)

- `Profile` (OneToOne User): `full_name, phone, linkedin, github, location, languages_json, skills_json, preferred_language, subject_template`, SMTP (`smtp_host, smtp_port, smtp_login, smtp_password_enc, smtp_from_email, smtp_security ∈ starttls|ssl|none`), cote (`daily_email_quota=10, daily_ai_quota=40`, editabile doar din admin), `sheet_sync=False` (doar owner). Metode `set_/get_smtp_password()`, `smtp_configured`, `missing_for_send()`. `__str__` fără secrete.
- `UserDocument`: `owner`, `kind ∈ cv_de|cv_en|certificate`, `FileField(upload_to=users/<id>/cv/<kind>.pdf | certificates/<nume>)`, `UniqueConstraint(owner, kind)` condiționat pe CV.
- `DailyUsage(owner, date, emails_sent, ai_calls)` + `quota.try_consume(user, kind)` = `UPDATE … WHERE count < limit` cu `F()` (atomic, fără `select_for_update`), `release()`, `usage_today()`.
- `Job.owner` FK, `url` **nu mai e unique global** → `UniqueConstraint(owner, url)`; `SearchRun.owner`. Migrări în 4 pași: modele noi → owner nullable → data migration „system owner" → owner obligatoriu + constrângere.
- Criptare: `dashboard/jobs/crypto.py`, Fernet/`MultiFernet` cu `FIELD_ENCRYPTION_KEY` din env (rotație prin listă). Dependență nouă `cryptography`.

### 4. Refolosirea pipeline-ului fără fork

`dashboard/jobs/profile_config.py::config_for(user)` produce dict-ul în forma `settings.yaml` (`applicant`, `skills.primary`, `ai`, `email.providers.user{server,port,login,password,security}`) pe care `CoverLetterGenerator`, `EmailBodyGenerator` și `SmtpSender` îl consumă neschimbat. `documents_for(user, language)` → (CV în limba jobului cu fallback, certificate) din `UserDocument`. `subject_for()` înlocuiește subiectul hardcodat.

Schimbări **opt-in** (default = comportamentul CLI, `pytest -q` rămâne verde):
- `sender/smtp_sender.py`: `cfg["password"]` înaintea env; fără `getpass` când `stdin` nu e tty (ridică); `__init__(..., file_rate_limit=True)` → `False` sare `flock` + `email_rate_state.json`; `security` (`ssl` → `SMTP_SSL`). `delay_between_emails_sec: 0` din adaptor (ritmul e al worker-ului).
- `generators/cover_letter_gen.py`: `generate(..., raise_on_ai_error=False)`; default-ul hardcodat cu numele owner-ului → `""`.
- `dashboard/jobs/generation_bridge.py`: `load_pipeline_config`/`cv_path_for`/`_smtp_password_env_missing` (bug: citea `email.provider`, cheie inexistentă) dispar; `generate_package(job)`/`send_package(job, live)` lucrează pe `config_for(job.owner)` + `documents_for`, scriu în `MEDIA_ROOT/users/<id>/jobs/<pk>/`, căi stocate **relativ** la `MEDIA_ROOT` (`media.resolve()` acceptă și absolute vechi).
- `dashboard/jobs/extractor.py`: `_fetch_text` → `urlguard.fetch_public_text` (SSRF: doar http/https, refuză IP private/loopback/link-local, re-verifică fiecare redirect prin event hook httpx, max 2 MB, doar HTML); `anthropic.Anthropic(timeout=90, max_retries=1)`.
- Sheet (`sheet_bridge`): `push_job`, `already_sent_in_sheet`, `mark_sent_in_sheet` rulează **doar** când `profile.sheet_sync`.

### 5. Autentificare, fișiere, protecția datelor

- `LoginRequiredMiddleware` global (Django 5.1+), `@login_not_required` pe signup/login/reset/privacy/terms/healthz. `accounts/` din `django.contrib.auth.urls`, `accounts/signup/` (email unic, `SIGNUP_INVITE_CODE` opțional din env), `settings/` (profil + SMTP + `settings/smtp/test/` = login SMTP fără trimitere), `settings/documents/`, `account/delete/` (confirmare parolă, cascade + `media.remove_user_dir`), `privacy/`, `terms/`.
- Toate view-urile: `get_object_or_404(Job, pk=pk, owner=request.user)`; `search_bridge.start_search(params, owner)`, `import_results(ids, owner)`.
- Media: `MEDIA_ROOT/users/<id>/…`, fără `MEDIA_URL` public; `serve_doc`/`serve_attachment`/`serve_user_document` verifică owner **și** `media.is_owned(path, user_id)` (anti path-traversal), 404 nu 403. Upload: `.pdf`, magic `%PDF-`, max 5 MB, max 10 certificate.
- Secrete: `ProfileAdmin.exclude=("smtp_password_enc",)`, `PasswordInput(render_value=False)`, `LOGGING` cu filtru `RedactSecrets` (`sk-ant-…`, `password=…`), nu se loghează niciodată `cfg`/`profile`; `DEBUG=0` în prod.
- Reset parolă prin SMTP-ul platformei (`EMAIL_HOST*` din env; console backend în dev).

### 6. UI

- htmx vendorizat intră în `base.html`. `_job_row.html`: `<tr hx-get="job/<pk>/status" hx-trigger="every 3s" hx-swap="outerHTML">` **doar cât `is_busy`**; rândul întors fără trigger oprește polling-ul singur. Pastile noi `pending/scraping/queued/running`, `title` = motivul erorii.
- `index.html`: buton „Trimite automat" (confirm: „emailul pleacă AUTOMAT către {email}"), „Re-extrage", banner „worker-ul pare oprit" (task `READY` > 2 min). `transmit_confirm.html` fără checkbox LIVE. `detail.html` cu jurnal. Sidebar: user, „Setări", „Ieșire", „Azi: 3/10 emailuri · 12/40 AI".

### 7. Deploy

Fișiere noi: `Dockerfile` (python 3.12-slim, gunicorn, whitenoise, `collectstatic` la build), `docker/entrypoint.sh` (așteaptă DB, `migrate` doar pentru `web`), `docker-compose.yml` (`db` postgres:16 + volum `pgdata` + healthcheck; `web` pe `172.29.0.1:8010` ca serviciul FastAPI existent; `worker-scrape`, `worker-apply` = `db_worker --queue-name …`; volum `media`), `deploy/traefik/jobapp.yml` (după modelul `/opt/gethub/traefik/dynamic/jobs.yml`; `certResolver`/`entryPoints` se copiază de pe host, nu se presupun), `.env.prod.example`, `.dockerignore` (`.venv`, `documents/`, `output/`, `data/`).

`settings.py`: `IS_PROD` din `DJANGO_ENV`; `SECRET_KEY` obligatoriu în prod; `DATABASES` din `DATABASE_URL` (parser propriu, fără dj-database-url) cu fallback SQLite; `ALLOWED_HOSTS`/`CSRF_TRUSTED_ORIGINS` din env; `SECURE_PROXY_SSL_HEADER`, cookie-uri secure, HSTS, `SECURE_SSL_REDIRECT=False` (Traefik redirectează); `TASKS`; `MEDIA_ROOT`; `EMAIL_DRY_RUN`; `LOGGING`.

Env prod: `DJANGO_SECRET_KEY, DATABASE_URL, POSTGRES_*, ALLOWED_HOSTS, CSRF_TRUSTED_ORIGINS, MEDIA_ROOT, ANTHROPIC_API_KEY, FIELD_ENCRYPTION_KEY, EMAIL_HOST/PORT/USER/PASSWORD, DEFAULT_FROM_EMAIL, EMAIL_DRY_RUN=0, SIGNUP_INVITE_CODE, N8N_WEBHOOK_SECRET` (doar owner).

Backup: cron pe host `docker compose exec db pg_dump … | gzip > /opt/jobapp/backups/$(date).sql.gz` zilnic + rotație 14 zile; `prune_db_task_results --min-age-days=14`.

Datele owner-ului: `dumpdata jobs auth` local → `loaddata` pe VPS, apoi `import_owner_profile` (comandă nouă: citește `settings.yaml` + env, criptează parola, copiază CV-urile/certificatele ca `UserDocument`, `--migrate-media` mută PDF-urile generate și rescrie căile, setează `sheet_sync=True`; idempotentă).

Domeniul rămâne de ales la deploy (`app.findajob.pro` din spec-ul mobil sau `jobapp.n8n-studio.com`); ruta Traefik acceptă ambele.

---

## Faze de implementare (fiecare fază = plan detaliat separat în `docs/superpowers/plans/`, commit-uri cu tag JOB-60…)

**Faza 0 — Fundație (coadă + settings + Postgres local)**
1. `requirements.txt`: `django-tasks-db`, `psycopg[binary]`, `gunicorn`, `whitenoise`, `cryptography`. Verifică `manage.py check`/`migrate` pe 6.1; altfel Plan B.
2. `settings.py`: `TASKS` (+ ramura de test), `DATABASES` din env, `IS_PROD`, `SECURE_*`, whitenoise, `MEDIA_ROOT`, `EMAIL_DRY_RUN`, `FIELD_ENCRYPTION_KEYS`, `LOGGING` + `jobs/logging_filters.py`.
3. Fum: task `ping` + `db_worker --max-tasks 1` pe Postgres în Docker local.

**Faza 1 — Tenancy (modele, criptare, auth, media)**
4. `crypto.py`, `Profile`, `UserDocument`, `DailyUsage`, `Job.owner`/`SearchRun.owner`, stările noi pe `Job`; migrările 0008–0011; `admin.py`.
5. `media.py` (dirs, `resolve/relativize/is_owned`, validatoare), `quota.py`, `profile_config.py`.
6. Auth: middleware global, `accounts_forms.py`/`accounts_views.py` (signup, settings, documents, smtp test, delete, legal), template-uri `registration/*`, `jobs/settings.html`, legale; `base.html` cu user/cote/logout.
7. Scoparea pe owner a view-urilor existente și a `search_bridge`; `serve_*` cu `is_owned`.
8. `OwnedTestCase` (user + profil + `force_login`, `override_settings` pe cheie și `MEDIA_ROOT` temporar); refactorul fixture-urilor; teste noi (crypto, cote, upload, cross-owner 404, signup, ștergere cont).
9. `import_owner_profile` + rulare locală pe datele owner-ului.

**Faza 2 — Puncte de atingere în pipeline (compatibile CLI)**
10. `smtp_sender.py` (`password`, no-tty, `file_rate_limit`, `security`) + teste pytest.
11. `cover_letter_gen.py` (`raise_on_ai_error`, default nume gol) + test.
12. `generation_bridge.py` rescris pe `config_for`/`documents_for`/`media`; `_smtp_password_env_missing` eliminat; teste adaptate.
13. `urlguard.py` + `extractor` (SSRF, timeout AI) + teste `SimpleTestCase` cu `getaddrinfo` mock-uit.

**Faza 3 — Task-urile și trigger-ele**
14. `tasks.py`: `_claim/_finish/_log/_friendly_error`, funcții pure `run_extract/run_apply/run_generate/run_send` + decoratoarele `@task(queue_name=…)`, `expire_stale_jobs()`, gating Sheet pe `sheet_sync`, filtrul `_RANK_AVOID` pe email.
15. `tests_tasks.py`: tabelul de cazuri (claim refuză, dublă rulare, send eșuat păstrează `generated_at`, `email_hr` gol, AI eșuat, cotă, SMTP auth, excepție neprevăzută, dry-run, sweeper, stale task id, extragere nu șterge date bune, `privacy@`).

**Faza 4 — Views + UI**
16. `urls.py`/`views.py`: `add_job`→enqueue, `scrape_job`, `apply_job`, `execute_job`→enqueue, `transmit_job` fără LIVE, `delete_job` gardă busy, `job_row`, `healthz`, `index` cu sweeper + `worker_stalled`.
17. Template-uri: `_job_row.html`, `index.html`, `base.html` (htmx, pastile), `transmit_confirm.html`, `detail.html`. Teste view cu `ImmediateBackend`.
18. `CLAUDE.md`: secțiune nouă „Dashboard public multi-user" (Profile = sursa, `settings.yaml` doar CLI, coadă/stări/sweeper, cheia Fernet, cote, `sheet_sync`).

**Faza 5 — Deploy + CI**
19. `Dockerfile`, `docker/entrypoint.sh`, `docker-compose.yml`, `.dockerignore`, `.env.prod.example`, `deploy/traefik/jobapp.yml`; `.gitignore` (`.env.prod`, `staticfiles/`, `media/`).
20. `.github/workflows/tests.yml`: + `python dashboard/manage.py test jobs --noinput`.
21. Suita rulată o dată **și pe Postgres** local (`DATABASE_URL` setat).

**Faza 6 — VPS**
22. DNS, `jobapp.yml` în `/opt/gethub/traefik/dynamic/`, `.env.prod`, `docker compose up -d --build`, `loaddata` + `import_owner_profile`, cron backup + prune, smoke test.

---

## Verificare

Local, la fiecare fază:
```bash
python -m pytest -q                                  # suita CLI, 100% verde (schimbările sunt opt-in)
python dashboard/manage.py makemigrations --check
python dashboard/manage.py test jobs                 # 146 existente + noile
DJANGO_ENV=production DJANGO_DEBUG=0 … python dashboard/manage.py check --deploy
docker compose up -d db && DATABASE_URL=… python dashboard/manage.py test jobs   # o dată pe Postgres
```

Manual local (`runserver` + `db_worker --queue-name scrape` + `--queue-name apply`, `EMAIL_DRY_RUN=1`): signup → settings → upload CV → test SMTP → lipește URL → rândul `pending→scraping→ok` fără refresh manual → „Trimite automat" → `queued→running→generated` (dry-run). Al doilea user nu vede joburile primului (`/job/<pk>/` → 404, `/doc/` → 404). Parola SMTP nu apare în admin/`dumpdata`/loguri.

Smoke pe VPS: `/healthz` 200; loguri workeri „Starting worker queue=…"; URL real → `ok` în 10-20 s; `http://169.254.169.254/` → „URL refuzat"; trimitere către o căsuță de test proprie → emailul are `From` = adresa userului, Anschreiben PDF + CV; retrimitere blocată (`sent_at`); `docker compose stop worker-apply` → banner după 2 min; `kill worker-scrape` în `scraping` → sweeper la 5 min; contul owner → Sheet primește GESENDET, alt cont → nicio linie n8n în log; `grep -i "sk-ant\|password"` pe loguri = zero.

## Riscuri

- `django-tasks-db` pe Django 6.1: verificat în Faza 0, Plan B gata.
- Un `db_worker` per coadă = un task o dată; se scalează cu `--scale`.
- DNS rebinding între validare și conectare (documentat, acceptat).
- Imaginea Docker mare (jobspy → pandas); `.dockerignore` obligatoriu.
- Fluxul CLI/n8n al owner-ului nu se atinge, dar `SmtpSender` capătă kwarg-uri noi: suita `tests/` e garda.
