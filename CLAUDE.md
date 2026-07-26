# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

# Job_app — Multi-Agent Bewerbung Pipeline

Pipeline automatizat pentru aplicarea la joburi în industria gaming/tech din Germania.

## Rulare

```bash
source .venv/bin/activate

python3 main.py --step full            # Pipeline complet (dry-run implicit)
python3 main.py --step scrape          # Scraping (JobSpy: Indeed/Glassdoor/Google + career pages)
python3 main.py --step analyze         # Analiză + Match Score (AI)
python3 main.py --step generate        # Generare Anschreiben (AI) + PDF
python3 main.py --step organize        # Organizare documente (DocumentAgent)
python3 main.py --step verify          # Verificare documente
python3 main.py --step send            # Trimitere email (dry-run)
python3 main.py --step send --live     # Trimitere email LIVE (cere confirmare interactivă)
python3 main.py --step poll            # Trigger Sheet→trimite (vezi „Două sisteme de trigger")
python3 main.py --step report

# Opțiuni: --min-score 50 | --provider {gmx,brevo} | --no-ai
```

## Teste

```bash
python -m pytest -q                              # Toată suita (tests/, configurat în pytest.ini)
python -m pytest tests/test_n8n_client.py -q     # Un fișier
python -m pytest tests/test_analyzer_agent.py::TestAnalyzerAgent::test_detect_language_german
```

Testele folosesc config-ul real (`load_config`) + mock-uri pentru I/O extern (httpx, SMTP). Nu lovesc rețeaua.

CI: `.github/workflows/tests.yml` rulează `pytest -q` pe Python 3.12 la push pe `main` și pe orice PR. Marker-e definite în `pytest.ini`: `integration`, `slow`.

## Arhitectură (big picture)

**Pattern multi-agent.** `main.py` are două căi de execuție separate:
- `run_pipeline()` — pașii scrape→analyze→generate→organize→verify→send, înlănțuiți prin `pipeline_jobs`.
- `run_poll()` — trigger din Sheet (NU înlănțuie pașii pipeline).

Fiecare agent moștenește `agents/base_agent.py`, implementează `run()` și întoarce un `AgentResult` (`AgentStatus`: SUCCESS/PARTIAL/FAILED/RUNNING/IDLE). Datele curg între agenți prin `result.data`.

**Gardă de mediu.** `validate_environment()` blochează `run_pipeline()` **doar** pentru variabile de mediu lipsă (`ANTHROPIC_API_KEY`, parola SMTP a providerului activ). Directoarele `data/`/`output/` și fișierul CV sunt doar **raportate** cu ❌, nu opresc rularea (`has_critical_failure` se setează numai la env vars). `run_poll()` NU trece deloc prin această gardă. Pentru a testa scraping-ul izolat fără secrete, instanțiază `ScraperAgent().run(...)` direct.

**Nimic nu încarcă `.env` automat** — nu există `load_dotenv` nicăieri și `python-dotenv` nu e în `requirements.txt`. Variabilele trebuie exportate în shell înainte (`set -a; source .env; set +a`), altfel `--step full` se oprește la garda de mediu.

**Sheet = sursă de adevăr.** Python nu atinge Google direct — vorbește cu n8n prin webhook. `sender/n8n_mapping.py` (`FIELD_MAP`) mapează cheile interne engleze ↔ headerele germane EXACTE ale Sheet-ului. Headerele reale contează: `url→"Apply URL"`, `cover_letter→"Anschreiben PDF"`, `gefunden_am→"Datum gefunden"`. Coloane inexistente în Sheet NU trebuie scrise (creează coloane fantomă la upsert). Coloana „Keywords Match" NU există în Sheet (verificat live 2026-07-23) — motivele prefiltrului (`job["keywords_match"]`) rămân câmp intern; dacă se adaugă manual coloana, maparea e pregătită în planul JOB-44 (Step 9).

**Piețe, nu liste plate.** `scraping.markets` din `settings.yaml` definește câte o piață (`de`, `ro_remote`) cu țară, limbă, `keyword_groups` și surse proprii. Codul primește un `scrapers.base.Market` și nu știe nimic despre țări — o piață nouă se adaugă în YAML, fără cod nou. `market.language` ajunge în col „Sprache" și comandă limba scrisorii; de asta un job din RO nu primește Anschreiben german. Bucla veche `keywords × locations` (49 de apeluri, majoritatea pe rate-limit) a fost înlocuită cu `keyword_groups × site-uri` (~10 apeluri jobspy, interogări booleene, paralel, max 4 workeri). `CareerPageScraper` e pre-piețe și rămâne cu semnătura veche (joburile lui n-au câmpul `market` — primesc reuniunea keyword-urilor la scoring).

**Poarta dintre scrape și analyze.** `main.apply_prefilter_gate()` rulează `store/seen.py` (index de dedupe cu memorie între rulări, `data/seen_jobs.json`, seed din Sheet prin workflow-ul read-only `JobApp Urls`) și `filters/relevance.py` (scor determinist 0-100 din `scraping.filters` + **bonus pentru keyword-urile de căutare ale pieței** găsite în titlu). Doar joburile peste `min_prescore` ajung la AI — pe date reale poarta respinge local ~88%. Lotul reținut se persistă în `data/jobs_filtered.json`, pe care `--step analyze` standalone îl preferă fișierului brut (altfel ar ocoli poarta). Ștergerea intrărilor `respins_prefiltru` din `seen_jobs.json` forțează re-evaluarea după o schimbare de scoring; intrările `aplicat` nu se șterg niciodată.

**Căutare din dashboard.** `SearchQuery` (`scrapers/base.py`) e unitatea de lucru a scraperului; `Market.to_query(scrape_config)` o produce din YAML, pentru calea CLI. `JobSpyScraper.scrape_query(q)` e SINGURUL loc care apelează `scrape_jobs()` — `scrape_market(market)` e doar un wrapper peste el (`scrape_query(market.to_query(...))`). Dashboard-ul (`dashboard/jobs/search_bridge.py`) rulează căutări ad-hoc într-un thread `daemon`, cu staging în `SearchRun`/`SearchResult` și import selectiv în `Job` (fără apel AI — JobSpy livrează deja structura). Indexul de dedupe e citit read-only din dashboard: `data/seen_jobs.json` aparține pipeline-ului CLI.

**Filtre incompatibile per site.** Upstream: pe Indeed poți folosi DOAR unul din `hours_old` / `(job_type & is_remote)` / `easy_apply`. `_sanitize()` renunță la `hours_old` când căutarea e remote (sau are `job_type`) și raportează asta cu prefix `[avertisment]`. `ScraperAgent` exclude avertismentele din `blocking_errors`, deci o degradare nu coboară statusul la PARTIAL.

**Surse best-effort.** `market.best_effort_sources` listează surse SAU sub-site-uri al căror eșec NU coboară statusul (dar se raportează în `errors`): `linkedin` (site jobspy, rate-limit agresiv — potrivit pe eticheta `[piață/site]` din mesajul de eroare, nu pe numele sursei) și `ro_boards` (eJobs, scraping HTML fără API). `ScraperAgent` dă `PARTIAL` doar pe erori blocante și `FAILED` doar la zero joburi + erori blocante. Notă eJobs: doar ~3 carduri per căutare sunt randate server-side (restul sunt placeholdere client-side fără link); datele complete există în `window.__NUXT__` — parsarea acelui JSON ar da ~19× randament (neimplementat).

**Upsert-ul golește ce nu trimiți.** Nodul Google Sheets din `JobApp Ingest` rulează `appendOrUpdate` + `autoMapInputData`, cu `Apply URL` drept `matchingColumns`. O cheie **absentă** din payload e ignorată, dar o cheie cu `""` **șterge** celula din rândul găsit. De aceea `job_to_sheet_row()` emite doar câmpurile cu valoare, iar `N8nClient.ingest()` aruncă rândurile fără `Apply URL` (fără cheie de match, n8n cade pe append și adaugă rânduri goale).

**Col M vs col P — cine scrie ce.** Col M „Status" aparține mașinii de stări a Apps Script (`plan→send→running→done/error`); `--step poll` NU o atinge, iar valorile străine (ex. `new` de la scrapere) sunt filtrate în `n8n_mapping`. Col P „Sende Befehl" e trigger-ul scris de om — poll-ul o golește **doar** după un email chiar trimis, altfel același rând s-ar reprocesa la fiecare tick. Col R „Email Status" are două forme de „trimis", pentru că o scriu două sisteme: `sent` (Apps Script→FastAPI) și `GESENDET` (`--step poll`); ambele filtre le recunosc pe amândouă.

**Generare text.** `cover_letter_gen.py` (Anschreiben AI, system prompt DE/EN, injectează `matching_skills`+cerințe via `special_instructions`) și `email_body_gen.py` (body din `config/email_templates/`, personalizat din `analysis`, salut german pe gen). Ambele cad pe template/fallback la eroare AI — `cover_letter_gen` avertizează pe stderr ca să nu fie silențios.

**PDF.** `generators/pdf_gen.py` (`text_to_pdf` → `merge_pdfs` → `assemble_bewerbung`) e apelat din `CoverLetterAgent`, nu din `main.py`: pasul `generate` produce și Anschreiben PDF + „Bewerbung Komplett" (Anschreiben + CV + certificate). `_latin1_safe()` transliterează textul — FPDF pe font core nu suportă UTF-8.

**Rate limit email persistat pe disc.** `sender/smtp_sender.py` ține contorul zilnic în `output/logs/email_rate_state.json`, protejat de `email_rate_state.lock` (`flock`), tocmai pentru că `--step poll` rulează ca proces separat la fiecare tick. Limita vine din `email.max_emails_per_day` (implicit 10) + `delay_between_emails_sec`. Ștergerea fișierului de stare resetează contorul.

### Două sisteme de trigger pe ACELAȘI Sheet (atenție — se pot bate)

| | Sistem A (Apps Script, producție) | Sistem B (acest repo) |
|---|---|---|
| Trigger | scrii `send` în **coloana M „Status"** | `--step poll`: `SENDEN` în **coloana P „Sende Befehl"** |
| Mecanism | Apps Script `onEdit` → POST webhook | Python interoghează n8n (poll) |
| Backend | **`job.n8n-studio.com/trigger`** — serviciul FastAPI de pe VPS, NU n8n (vechiul `jobs.get-hub.io` e **mort**) | n8n `JobApp Pending` |
| Stare | mașină de stări col M: `plan→send→running→done/error`; erori logate în col W „Notizen" | `Email Status=GESENDET` |

Apps Script-ul (bound la Sheet, sursă trackuită la `scripts/jobapp_appscript.gs`) e front-end-ul producției: declanșează și pe URL paste în col E (scrape) și pe col AB (Danke). La eșec scrie `error` în M + motivul în col W.

### n8n — detalii critice

- **Instanța proiectului = `app.n8n-studio.com`** (config `n8n.base_url`). Acces programatic prin **API public cu `N8N_API_KEY`** din `.env` (vezi `scripts/deploy_n8n_workflows.py`).
- **Tool-urile MCP n8n (`mcp__n8n-mcp__*`, `mcp__claude_ai_n8n__*`) sunt conectate la ALTĂ instanță** (`n8n.rotocon.world`) — NU folosi MCP pentru workflow-urile acestui proiect; folosește API-ul public.
- **`$env` e blocat în nodurile Code** pe această instanță → pentru secrete pe webhook folosește credential nativ **Header Auth** (`X-JobApp-Secret`), nu `$env`.
- Workflow-uri active: `JobApp Ingest`/`Pending` (upsert/read Sheet), `JobApp Upload` (PDF → Google Drive, scrie link-ul în col O „Anschreiben PDF" — vezi `sender/n8n_client.py::upload_doc`, deocamdată neapelat din pipeline), `JobApp Urls` (read-only: întoarce doar `Apply URL`/`Status`/`Email Status` pentru tot tab-ul — alimentează indexul de dedupe la `--step scrape`; proiecția pe 3 coloane + range `A1:AB` sunt intenționate, tab-ul are ~1140 coloane fantomă `col_29…col_1167` pe care o citire completă le-ar transfera pe toate). `JobApp Trigger` e un stub ping/pong **neconectat** — NU e endpoint-ul Apps Script (acela e `job.n8n-studio.com/trigger`, alt serviciu, vezi mai jos); păstrat activ + în git (`n8n_workflows/jobapp_trigger.json`) pentru istoric/eventuală reutilizare. Sheet real: doc `1fTjvs6...`, tab `Bewerbungen` (28 coloane), Google Sheets OAuth credential în n8n.
- **Autentificarea Google în n8n = service account, nu OAuth.** Credentialul `googleApi` „JobApp Service Account (get-hub)" (`D1jIVLCWwpVV1Y3U`) folosește `job-tracker-bot@get-hub-489416.iam.gserviceaccount.com`, același service account ca serviciul de pe VPS. Nodurile Google Sheets rulează cu `authentication: "serviceAccount"`, iar nodurile HTTP Request cu `nodeCredentialType: "googleApi"`. Motivul: **toate** credentialele `googleSheetsOAuth2Api` din instanță expiraseră („needs to be reconnected"), ceea ce dădea HTTP 500 pe `jobapp-ingest`/`pending`/`upload`. Un service account nu expiră.
- Pentru apeluri Google Sheets API arbitrare (citire/ștergere/validări) când nu există un workflow: creează un workflow temporar Webhook→HTTP Request (cu credential `googleApi`)→Respond, execută, apoi șterge-l.
- **`*.get-hub.io` NU mai e valabil** (instanța veche pe `2.57.91.92`, jos). Nu mai folosi `jobs.get-hub.io` / `n8n.get-hub.io` ca backend. Job Tracker Webhook (uvicorn :8765 pe VPS `85.215.69.83`) e acum expus pe **`https://job.n8n-studio.com`** — ruta Traefik `Host(\`job.n8n-studio.com\`)` în `/opt/gethub/traefik/dynamic/jobs.yml` (TLS Let's Encrypt OK; `/health` și `/docs` → 200). Toate domeniile active ale proiectului sunt pe `*.n8n-studio.com` (VPS `85.215.69.83`).

### Serviciul de producție de pe VPS (NU e în git)

Calea care chiar trimite aplicații (Sistem A) rulează pe VPS `85.215.69.83`, în afara acestui repo:

| cale | rol |
|---|---|
| `/opt/job-webhook/webhook/server.py` | FastAPI: `/trigger`, `/scrape`, `/auto-fix`, `/digest`, `/health` |
| `/opt/job-webhook/tracker/sheets_tracker.py` | acces Sheet prin **service account** `config/get-hub-489416-*.json` (legat pe numele tabului) |
| `/opt/job-webhook/sender/send_from_tracker.py` | trimitere SMTP |
| `/etc/systemd/system/job-webhook.service` | uvicorn pe `172.29.0.1:8765`, `EnvironmentFile=/opt/job-webhook/.env` |
| `/etc/systemd/system/job-scrape.timer` | scrape la 07:00 și 18:00 Europe/Berlin |
| `crontab -l` → `*/15 * * * * … /auto-fix` | reîncearcă rândurile `error` |

Serviciul are **propriul `.env`** (`WEBHOOK_SECRET`, `GMX_PASSWORD`, `ANTHROPIC_API_KEY`) și **propriul `config/settings.yaml`** — schimbarea modelului AI sau a cheii în repo NU se propagă acolo. `server.py` nu apelează `load_dotenv`: variabilele intră doar prin `EnvironmentFile`, deci după orice editare a `.env` e nevoie de `systemctl restart job-webhook`.

⚠️ Cele două sisteme de trigger scriu în același Sheet cu chei diferite. Vezi „Col M vs col P" mai sus înainte de a atinge orice logică de status.

### Scheduling & scripturi

- `scripts/com.jobapp.poll.plist` — job launchd (macOS) care rulează periodic `--step poll`.
- `scripts/deploy_n8n_workflows.py` — push/pull workflow-uri din `n8n_workflows/*.json` prin API-ul public n8n (`N8N_API_KEY`).
- `scripts/test_e2e_manual.py` — E2E manual (lovește servicii reale, NU face parte din suita pytest).
- `scripts/jobapp_appscript.gs` — copie trackuită a Apps Script-ului bound la Sheet; editarea aici nu îl deployează, trebuie copiat manual în editorul Apps Script.

### Documente

`documents/` în repo e **doar schelet** (`.gitkeep` + `Signature.png`) — CV-ul, Anschreiben-urile și certificatele NU sunt aici (sunt pe hostul de producție / iCloud). n8n cloud nu poate atașa fișiere locale; pentru attachment-uri trebuie linkuri Drive în Sheet.

## Configurare & secrete

- `config/settings.yaml` — profil, skills, scraping, AI, email, bloc `n8n:`. **Modelul AI trebuie să fie un ID valid curent** (ex. `claude-sonnet-4-6`); un model dispărut dă 404 → fallback silențios la template.
- `.env` (gitignored) — `ANTHROPIC_API_KEY` (verifică să nu fie duplicat/placeholder), `GMX_PASSWORD`/`BREVO_SMTP_KEY`, `N8N_WEBHOOK_SECRET` (= valoarea credentialului Header Auth din n8n), `N8N_API_KEY` (doar deploy).
- Dezactivare n8n: `n8n.enabled: false` → pipeline doar cu Excel local (`tracker/excel_tracker.py`).

## Convenții

- Python 3.12+; comentarii și mesaje în limba română.
- Commit-uri în română cu tag `JOB-NN` (ex. `fix(n8n): ... (JOB-20)`) — numerele sunt secvențiale, ia următorul din `git log`.
- Specificațiile și planurile de lucru stau în `docs/superpowers/{specs,plans}/YYYY-MM-DD-<subiect>.md`.
- `archive/` conține scripturi pre-refactor (v1, monolitice) — referință istorică, nu se importă din pipeline.
- Dry-run implicit pentru email (`--live` pentru producție).
- Config centralizat în `settings.yaml`, secrete în `.env`.
