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
