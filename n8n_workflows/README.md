# n8n Workflows — JobApp

Două workflow-uri pentru integrarea Job_app ↔ Google Sheets:

- `jobapp_ingest.json` — webhook `POST /webhook/jobapp-ingest` → upsert în Sheet (match pe `URL`).
- `jobapp_pending.json` — webhook `POST /webhook/jobapp-pending` → rândurile `SENDEN`.

## Deploy
```bash
python3 scripts/deploy_n8n_workflows.py
```

## Pre-condiții în n8n
- Variabila de mediu `JOBAPP_SECRET` setată pe instanța n8n = valoarea din `.env::N8N_WEBHOOK_SECRET`.
- Credențialul Google Sheets `lwLv81Aaab8h0Zm3` valid; Sheet-ul `1p0u4...` are tab-ul `Job Applications` cu headerele germane pe rândul 1.

## Fallback (dacă activarea prin API eșuează)
Importă manual cele 2 fișiere JSON din UI (Import from File) și activează-le.
