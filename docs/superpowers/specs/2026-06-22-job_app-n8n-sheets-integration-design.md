# Spec: Job_app ↔ Google Sheets ↔ n8n Integration

- **Data:** 2026-06-22
- **Status:** Approved design (pre-implementation)
- **Autor:** George + Claude

## 1. Context & motivație

`Job_app` este o pipeline multi-agent (scrape → analyze → generate → send → track) care
ține evidența aplicațiilor într-un fișier Excel local (`data/Job_Application_Tracker.xlsx`,
via `openpyxl`). Scopul acestei lucrări: să facem trackerul vizibil/colaborativ într-un
**Google Sheet** și să folosim **n8n** ca strat de orchestrare/declanșare, fără ca pipeline-ul
local să depindă de conexiuni inbound (NAT/firewall).

### Realitatea descoperită la audit (importantă)

- Instanța n8n a proiectului este **`https://app.n8n-studio.com`** (HTTPS/443) — NU
  `http://app.n8n-studio.com:5678` cum spune `.env` (host/port greșit), și NU
  `n8n.rotocon.world` (conectorul MCP, instanță diferită).
- Cheia API n8n din `.env` (linia 18) este **validă**.
- Există deja un workflow **„Job Tracker → Google Sheets Sync"**, **activ**, care la fiecare
  6h citea Excel-ul **prin SSH** și-l scria în Google Sheet — dar **eșuează de fiecare dată**
  cu `All configured authentication methods failed` (SSH auth respins). Deci „pornit", dar
  nefuncțional.
- Modelul vechi (SSH-pull, Excel=sursă) este **invers** față de ce vrem și pică exact pe
  direcția inbound-spre-mașină. Îl **pensionăm** (dezactivăm, nu ștergem).
- Google Sheet-ul real conectat: `1p0u4XcMxOWex2-OTfIxnvpun3NVFkrqh3DvLWhRBkO0`, tab
  **„Job Applications"** — NU link-ul `1fTjvs6...` dat inițial (acela e inaccesibil / alt cont).

### Resurse n8n existente de refolosit

| Resursă | ID | Note |
|---|---|---|
| Google Sheets OAuth2 cred | `lwLv81Aaab8h0Zm3` | „Google Sheets account" |
| Brevo SMTP cred | `aK6Bete8OTRMNACY` | from `george.cucuiet@gmx.de` |
| Sheet documentId | `1p0u4XcMxOWex2-OTfIxnvpun3NVFkrqh3DvLWhRBkO0` | tab „Job Applications", `appendOrUpdate`, autoMap |

## 2. Decizii de arhitectură (confirmate)

1. **Google Sheet = sursa de adevăr**; **n8n = orchestrator/declanșator**.
2. **Python NU atinge Google direct** — comunică doar cu n8n via webhook HTTPS.
3. **n8n → Job_app: prin polling** — Job_app (cron/launchd) interoghează n8n; nicio
   conexiune inbound spre Mac.
4. **Excel local = păstrat ca backup/cache** (dual-write). Codul Excel existent rămâne neatins.
5. Trimiterea email rămâne **în Python** (email_agent existent). Alternativa „trimite prin
   Brevo n8n" e notată dar nu e default.
6. `n8n.enabled=false` ⟹ pipeline-ul rulează **exact ca azi** (zero regresie).

## 3. Arhitectură & flux de date

```
┌─────────────────────────── Mac local ───────────────────────────┐
│  Job_app (Python)                                                │
│   scrape→analyze→generate→send  (agenți existenți, neatinși)     │
│   ExcelTracker  ──►  data/...xlsx        (BACKUP local, rămâne)  │
│   NOU: sender/n8n_client.py                                      │
│   NOU: agents/n8n_sync_agent.py                                  │
│   NOU: main.py --step poll  +  cron/launchd la fiecare N minute  │
└──────────┬───────────────────────────────────▲──────────────────┘
           │ POST jobapp-ingest (push)          │ POST jobapp-pending (poll)
           ▼ HTTPS + X-JobApp-Secret            │
┌─────────────────── https://app.n8n-studio.com ──────────────────┐
│  WF-A „JobApp Ingest"  : webhook → Sheets appendOrUpdate (URL)   │
│  WF-B „JobApp Pending" : webhook → Sheets read → SENDEN rows     │
│  (pensionat) „Job Tracker → Sheets Sync" SSH  = active:false     │
└──────────┬───────────────────────────────────▲──────────────────┘
           ▼ Google Sheets cred lwLv81...       │
┌──────── Google Sheet 1p0u4... / tab „Job Applications" ─────────┐
│  1 rând/job, cheie upsert = URL.  Tu marchezi „SENDEN" manual.   │
└──────────────────────────────────────────────────────────────────┘
```

**Două fluxuri:**
- **PUSH (outbound):** după scrape/analyze/generate/send, Job_app trimite rezultatele la
  `jobapp-ingest`; n8n face upsert în Sheet (dual-write cu Excel local).
- **PULL (trigger):** `--step poll` cere `jobapp-pending`; n8n întoarce rândurile marcate
  `SENDEN`; Job_app generează (dacă lipsește) + trimite + push status înapoi.

## 4. Contract HTTP

Toate POST, HTTPS. Header `X-JobApp-Secret: <N8N_WEBHOOK_SECRET>`. Răspuns JSON.
La secret invalid → n8n răspunde `401 {ok:false, error:"unauthorized"}`.

### 4.1 `POST /webhook/jobapp-ingest`  (Job_app → n8n)

Request:
```json
{ "event": "jobs_upsert",
  "jobs": [
    { "URL": "https://...", "Firma": "Studio X", "Position": "Python Dev",
      "Standort": "Berlin", "Quelle": "jobspy", "Kontakt Email": "hr@...",
      "Kontakt Person": "...", "Sprache": "de", "Gefunden am": "2026-06-22",
      "Status": "Bereit", "Match Score": 72, "Keywords Match": "Python, Django",
      "Anschreiben": "Sehr geehrte...", "Email Inhalt": "...",
      "Gesendet am": "", "Email Status": "" } ] }
```
n8n: pentru fiecare job → `appendOrUpdate` în tab „Job Applications", **columnToMatchOn = `URL`**
(coloana cheie). Cheile payload-ului trebuie să corespundă **exact** headerelor Sheet-ului
(germane, case-sensitive — ex. `URL`, `Firma`, `Email Status`).
Răspuns: `{ "ok": true, "upserted": N }`.

### 4.2 `POST /webhook/jobapp-pending`  (Job_app poll → n8n)

Request: `{ "action": "get_pending" }`

n8n: citește tab-ul, filtrează rândurile cu `Sende Befehl == "SENDEN"` și
`Email Status != "GESENDET"`. Răspuns:
```json
{ "ok": true,
  "commands": [
    { "action": "send", "URL": "https://...", "Firma": "Studio X",
      "Position": "Python Dev", "Kontakt Email": "hr@...", "Sprache": "de",
      "Anschreiben": "..." } ] }
```

## 5. Schema Google Sheet (tab „Job Applications")

Refolosim headerele existente (identice cu `tracker/excel_tracker.py::COLUMNS`):

`Nr.`, `Firma`, `Position`, `Standort`, `URL`, `Quelle`, `Kontakt Email`,
`Kontakt Person`, `Sprache`, `Gefunden am`, `Status`, `Anschreiben`, `Match Score`,
`Keywords Match`, `Email Inhalt`, `Gesendet am`, `Email Status`, `Anschreiben PDF`,
`CV PDF`, `Zeugnisse`, `Zertifikate`, `Komplett PDF`, **`Sende Befehl`**, `Notizen`.

- Cheia de upsert: **`URL`**.
- Coloana de control manual: **`Sende Befehl`** (gol / `SENDEN`).
- Payload-ul de la Python mapează un subset; restul rămân gestionate manual sau goale.

## 6. Componente Python (noi)

### 6.1 `sender/n8n_client.py`
- Clasa `N8nClient(base_url, secret, paths, timeout, enabled)`.
- Metode:
  - `ingest(jobs: list[dict]) -> dict` — POST `jobapp-ingest`, întoarce `{ok, upserted}`.
  - `get_pending() -> list[dict]` — POST `jobapp-pending`, întoarce `commands`.
- HTTPS, header secret, timeout (default 20s), 1 retry pe eroare de rețea/5xx.
- Dacă `enabled=False` → no-op care întoarce rezultat „skipped" (nu aruncă).
- Folosește `httpx` (deja în `requirements.txt`).

### 6.2 `agents/n8n_sync_agent.py`
- `N8nSyncAgent(BaseAgent)`, `run(action, jobs=None)` cu acțiuni:
  - `push` → `client.ingest(jobs)` → `AgentResult` (SUCCESS/PARTIAL/FAILED).
  - `pending` → `client.get_pending()` → `AgentResult` cu `data={"commands": [...]}`.
- Citește configul `n8n:` prin `load_config` (pattern existent). Erorile n8n NU pică
  pipeline-ul (PARTIAL + log), pentru că Excel rămâne sursa de backup.

### 6.3 `main.py`
- După fiecare pas care modifică starea (scrape/analyze/generate/send), dacă `n8n.enabled`:
  apel `N8nSyncAgent.run("push", jobs=pipeline_jobs)` (dual-write, non-blocant).
- **Nou `--step poll`:**
  1. `N8nSyncAgent.run("pending")` → comenzi.
  2. Pentru fiecare comandă `send`: dacă lipsește Anschreiben → `CoverLetterAgent`;
     apoi `EmailAgent` (dry-run/live după `--live`).
  3. Scrie în Excel local (backup) + `push` status `GESENDET` / `FEHLER` înapoi la n8n.
  4. Idempotent: dacă `Email Status == GESENDET` → skip.

### 6.4 Config & secrete
- `config/settings.yaml` — bloc nou:
  ```yaml
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
- `.env` — adaug `N8N_WEBHOOK_SECRET=...`; corectez comentariul de host (https/443).
  `N8N_API_KEY` existent rămâne (folosit doar pentru deploy, nu la runtime).

### 6.5 Scheduling
- `scripts/com.jobapp.poll.plist` (launchd) SAU snippet cron pentru
  `python3 main.py --step poll` la `poll_interval_min`. Documentat în README/CLAUDE.md.

## 7. Componente n8n (deploiate live via API)

Deoarece cheia API funcționează, workflow-urile se creează prin
`POST /api/v1/workflows` și se activează. Refolosesc credențialele existente.

- **WF-A „JobApp Ingest"**: Webhook (POST, path `jobapp-ingest`) → (IF secret) → Google
  Sheets (`appendOrUpdate`, doc `1p0u4...`, tab „Job Applications", matchOn `url`, cred
  `lwLv81...`) → Respond to Webhook `{ok, upserted}`.
- **WF-B „JobApp Pending"**: Webhook (POST, path `jobapp-pending`) → (IF secret) → Google
  Sheets (read, doc/tab idem) → Filter `Sende Befehl=SENDEN & Email Status≠GESENDET` →
  Respond to Webhook `{ok, commands}`.
- **Cleanup**: „Job Tracker → Sheets Sync" (`naxm3lJSXipBoH5r`) → `active:false` via
  `POST /api/v1/workflows/{id}/deactivate`.

> Notă deploy: dacă activarea webhook-urilor prin API public eșuează pe versiunea n8n,
> fallback = livrăm workflow-urile ca JSON importabil + activare manuală din UI. De
> confirmat în prima fază de implementare.

## 8. Securitate
- Secret partajat `X-JobApp-Secret` pe ambele webhook-uri (env, nu hardcodat).
- HTTPS obligatoriu (instanța răspunde pe 443).
- Cheia API n8n rămâne doar în `.env` (gitignored); nu apare în cod sau loguri.

## 9. Testare
- `tests/test_n8n_client.py` — mock httpx (fără rețea reală): ingest happy-path, pending
  parsing, secret/header trimis, `enabled=False` no-op, retry pe 5xx, timeout.
- `tests/test_n8n_sync_agent.py` — AgentResult corect pe success/partial/failed; n8n down
  ⟹ PARTIAL, nu FAILED.
- `tests/test_poll_step.py` — logica poll: skip pe `GESENDET`, generează când lipsește
  Anschreiben, push status înapoi. Agenți email/cover-letter mock-uiți.
- Smoke test manual (post-deploy): `curl` real către cele 2 webhook-uri cu secret.

## 10. Non-goals (YAGNI)
- Fără mutarea trimiterii email în n8n (rămâne Python).
- Fără WF3 „scrape trigger" pe cron n8n (eventual fază 2).
- Fără ștergerea codului Excel (rămâne backup).
- Fără gestionarea link-ului `1fTjvs6...` (inaccesibil; folosim `1p0u4...`).

## 11. Criterii de succes
1. `--step poll` pe gol rulează fără eroare și raportează 0 comenzi.
2. Un job marcat `SENDEN` în Sheet e returnat de `jobapp-pending` și procesat (dry-run).
3. Un job procesat apare în Sheet cu `Status/Email Status` actualizat via `jobapp-ingest`,
   ȘI în Excel local.
4. `n8n.enabled=false` → comportament identic cu cel de azi (teste existente verzi).
5. Workflow-ul SSH stricat nu mai produce erori la fiecare 6h (dezactivat).
6. Toate testele noi + cele existente trec.
