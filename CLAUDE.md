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
python3 main.py --step generate        # Generare Anschreiben (AI)
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

## Arhitectură (big picture)

**Pattern multi-agent.** `main.py` are două căi de execuție separate:
- `run_pipeline()` — pașii scrape→analyze→generate→organize→verify→send, înlănțuiți prin `pipeline_jobs`.
- `run_poll()` — trigger din Sheet (NU înlănțuie pașii pipeline).

Fiecare agent moștenește `agents/base_agent.py`, implementează `run()` și întoarce un `AgentResult` (`AgentStatus`: SUCCESS/PARTIAL/FAILED/RUNNING/IDLE). Datele curg între agenți prin `result.data`.

**Gardă de mediu.** `validate_environment()` blochează `run_pipeline()` dacă lipsesc `ANTHROPIC_API_KEY`, parola SMTP, directoarele `data/`/`output/`, sau fișierul CV. `run_poll()` NU trece prin această gardă. Pentru a testa scraping-ul izolat fără secrete, instanțiază `ScraperAgent().run(...)` direct.

**Sheet = sursă de adevăr.** Python nu atinge Google direct — vorbește cu n8n prin webhook. `sender/n8n_mapping.py` (`FIELD_MAP`) mapează cheile interne engleze ↔ headerele germane EXACTE ale Sheet-ului. Headerele reale contează: `url→"Apply URL"`, `cover_letter→"Anschreiben PDF"`, `gefunden_am→"Datum gefunden"`. Coloane inexistente în Sheet NU trebuie scrise (creează coloane fantomă la upsert).

**Generare text.** `cover_letter_gen.py` (Anschreiben AI, system prompt DE/EN, injectează `matching_skills`+cerințe via `special_instructions`) și `email_body_gen.py` (body din `config/email_templates/`, personalizat din `analysis`, salut german pe gen). Ambele cad pe template/fallback la eroare AI — `cover_letter_gen` avertizează pe stderr ca să nu fie silențios.

### Două sisteme de trigger pe ACELAȘI Sheet (atenție — se pot bate)

| | Sistem A (Apps Script, producție) | Sistem B (acest repo) |
|---|---|---|
| Trigger | scrii `send` în **coloana M „Status"** | `--step poll`: `SENDEN` în **coloana P „Sende Befehl"** |
| Mecanism | Apps Script `onEdit` → POST webhook | Python interoghează n8n (poll) |
| Backend | `app.n8n-studio.com/webhook/jobapp-trigger` (vechiul `jobs.get-hub.io` e **mort** — vezi mai jos) | n8n `JobApp Pending` |
| Stare | mașină de stări col M: `plan→send→running→done/error`; erori logate în col W „Notizen" | `Email Status=GESENDET` |

Apps Script-ul (bound la Sheet, NU în repo) e front-end-ul producției: declanșează și pe URL paste în col E (scrape) și pe col AB (Danke). La eșec scrie `error` în M + motivul în col W.

### n8n — detalii critice

- **Instanța proiectului = `app.n8n-studio.com`** (config `n8n.base_url`). Acces programatic prin **API public cu `N8N_API_KEY`** din `.env` (vezi `scripts/deploy_n8n_workflows.py`).
- **Tool-urile MCP n8n (`mcp__n8n-mcp__*`, `mcp__claude_ai_n8n__*`) sunt conectate la ALTĂ instanță** (`n8n.rotocon.world`) — NU folosi MCP pentru workflow-urile acestui proiect; folosește API-ul public.
- **`$env` e blocat în nodurile Code** pe această instanță → pentru secrete pe webhook folosește credential nativ **Header Auth** (`X-JobApp-Secret`), nu `$env`.
- Workflow-uri: `JobApp Ingest`/`Pending` (upsert/read Sheet), `JobApp Trigger` (endpoint Apps Script). Sheet real: doc `1fTjvs6...`, tab `Bewerbungen` (28 coloane), Google Sheets OAuth credential în n8n.
- Pentru apeluri Google Sheets API arbitrare (citire/ștergere/validări) când nu există un workflow: creează un workflow temporar Webhook→HTTP Request (cu credential `googleSheetsOAuth2Api`)→Respond, execută, apoi șterge-l.
- **`*.get-hub.io` NU mai e valabil** (instanța veche pe `2.57.91.92`, jos). Nu mai folosi `jobs.get-hub.io` / `n8n.get-hub.io` ca backend. Job Tracker Webhook (uvicorn :8765 pe VPS `85.215.69.83`) e acum expus pe **`https://job.n8n-studio.com`** — ruta Traefik `Host(\`job.n8n-studio.com\`)` în `/opt/gethub/traefik/dynamic/jobs.yml` (TLS Let's Encrypt OK; `/health` și `/docs` → 200). Toate domeniile active ale proiectului sunt pe `*.n8n-studio.com` (VPS `85.215.69.83`).

### Documente

`documents/` în repo e **doar schelet** (`.gitkeep` + `Signature.png`) — CV-ul, Anschreiben-urile și certificatele NU sunt aici (sunt pe hostul de producție / iCloud). n8n cloud nu poate atașa fișiere locale; pentru attachment-uri trebuie linkuri Drive în Sheet.

## Configurare & secrete

- `config/settings.yaml` — profil, skills, scraping, AI, email, bloc `n8n:`. **Modelul AI trebuie să fie un ID valid curent** (ex. `claude-sonnet-4-6`); un model dispărut dă 404 → fallback silențios la template.
- `.env` (gitignored) — `ANTHROPIC_API_KEY` (verifică să nu fie duplicat/placeholder), `GMX_PASSWORD`/`BREVO_SMTP_KEY`, `N8N_WEBHOOK_SECRET` (= valoarea credentialului Header Auth din n8n), `N8N_API_KEY` (doar deploy).
- Dezactivare n8n: `n8n.enabled: false` → pipeline doar cu Excel local (`tracker/excel_tracker.py`).

## Convenții

- Python 3.12+; comentarii și mesaje în limba română.
- Commit-uri în română cu tag `JOB-NN` (ex. `fix(n8n): ... (JOB-20)`).
- Dry-run implicit pentru email (`--live` pentru producție).
- Config centralizat în `settings.yaml`, secrete în `.env`.
