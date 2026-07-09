# Spec: Audit & Optimizare Job_app — igienă repo, n8n, cod, CI

- **Data:** 2026-07-09
- **Status:** Approved design (pre-implementare)
- **Autor:** George + Claude

## 1. Context & motivație

Cerere: „Verifică întregul proiect și optimizează toate acțiunile și workflow-urile."
Un audit inițial (explorare read-only: git, structură repo, teste, config, instanța n8n
live via API public) a scos la iveală mai multe discrepanțe concrete între ce e documentat/
deploiat și ce e efectiv în git, plus o necunoscută operațională. Scopul acestei lucrări:
închide aceste discrepanțe fără să atingă nimic din afara proiectului Job_app (instanța n8n
e comună mai multor proiecte — Rotocon, Rankenstein, LinkedIn content, stripe-orders etc.).

## 2. Constatări (audit)

| # | Constatare | Categorie | Severitate |
|---|---|---|---|
| 1 | `docs/` (spec+plan din brainstorming-ul din 2026-06-22) e complet netrackuit, nu e gitignored — risc de pierdere | Igienă repo | Mediu |
| 2 | `scripts/jobapp_appscript.gs` (sursa Apps Script de producție) e netrackuit; CLAUDE.md spune eronat „NU e în repo" | Igienă repo / docs | Mediu |
| 3 | Diff necomis în `scrapers/career_page_scraper.py` (extragere email din Impressum) | Igienă repo | Mic |
| 4 | Instanța n8n live are 4 workflow-uri `JobApp*` active (Ingest, Pending, **Trigger**, **Upload**), dar `n8n_workflows/` are backup JSON doar pentru 2 (Ingest, Pending); `deploy_n8n_workflows.py` nu știe de celelalte 2 | n8n IaC drift | **Mare** |
| 5 | 2 workflow-uri n8n duplicate „Job Tracker → Google Sheets Sync" (SSH, retrase), ambele inactive | n8n curățenie | Mic |
| 6 | 2 workflow-uri n8n orfane: „Automate LinkedIn job search... Easy-Apply" (prototip separat, inactiv) și „Bewerbung Email Sender — Brevo" (manual-trigger, alternativa notată-dar-nefolosită din spec-ul inițial) | n8n curățenie | Mic |
| 7 | Cele 4 webhook-uri `JobApp*` au **zero execuții înregistrate vreodată** — plumbing-ul live (auth, rutare, scriere Sheet) nu a fost confirmat empiric | Verificare operațională | Mediu |
| 8 | Niciun CI — `.github/workflows` nu există; cele 97 de teste rulează doar manual | Optimizare | Mediu |
| 9 | `main.py`: flag `--use-ai` e no-op (default `True`, nimic nu-l poate seta `False` în afară de `--no-ai`) | Cod | Mic |
| 10 | *(FYI, în afara scope-ului)* workflow-urile „LinkedIn — ..." de pe aceeași instanță n8n eșuează la fiecare rulare programată — nu aparțin Job_app, nu se ating | — | — |

Baseline: **97/97 teste trec**, `app.n8n-studio.com` și `job.n8n-studio.com/health` răspund 200.

## 3. Decizii confirmate cu userul

1. **Scope:** toate cele 4 arii (igienă repo, n8n backup&curățenie, n8n verificare live, cod&CI).
2. **n8n curățenie:** șterge toate cele 4 workflow-uri confirmate moarte (#5, #6), DUPĂ ce
   Trigger+Upload sunt salvate ca backup (#4 rezolvat înainte de orice ștergere).
3. **Verificare live:** limitată strict la un smoke-test **read-only** pe `jobapp-pending`
   (auth + citire Sheet, fără scriere). NU se apelează live `jobapp-ingest` / `jobapp-upload` /
   `jobapp-trigger` — acestea scriu în Sheet-ul real, urcă fișiere în Drive, sau pot declanșa
   un send real; rămân apelate doar intenționat de user (`--step poll`, Apps Script).

## 4. Workstream 1 — Igienă repo & documentație

- Commit `docs/` (spec+plan existente din 2026-06-22 + noul spec de față).
- Commit `scripts/jobapp_appscript.gs`.
- Commit diff-ul din `scrapers/career_page_scraper.py`.
- Update `CLAUDE.md`:
  - elimină afirmația „Apps Script-ul... NU în repo"; menționează calea nouă
    `scripts/jobapp_appscript.gs`.
  - completează secțiunea „n8n — detalii critice" cu toate cele 4 workflow-uri
    (Ingest/Pending/Trigger/Upload), nu doar 3.
- Fiecare commit separat, tag `JOB-NN` secvențial (română, per convenție).

## 5. Workstream 2 — n8n: backup & curățenie

1. Export live pentru `JobApp Trigger` (`cINDCLxxAeFi2UrB`) și `JobApp Upload`
   (`WBiiK6V89Lc8KX6z`) via `GET /api/v1/workflows/{id}`, trimis la forma minimă folosită deja
   de `jobapp_ingest.json`/`jobapp_pending.json` (doar `name`, `settings`, `nodes`,
   `connections` — fără `id`/`versionId`/`createdAt`/`tags`/`pinData` la nivel de workflow).
   Salvate ca `n8n_workflows/jobapp_trigger.json` / `jobapp_upload.json`.
2. Update `scripts/deploy_n8n_workflows.py`: `WORKFLOWS` include toate cele 4 fișiere.
3. Update `n8n_workflows/README.md` cu toate cele 4.
4. **Abia după** ce (1)-(3) sunt commise: șterge via API cele 4 workflow-uri moarte —
   `naxm3lJSXipBoH5r`, `4jF1tciI4Oj4lIZp` (Sheets-Sync duplicate), `TUjxbhfZQwL1U3Mr`
   (LinkedIn Easy-Apply), `rw6lXL3JG85DIcmN` (Brevo manual sender).

## 6. Workstream 3 — n8n: verificare live

- Un singur apel: `POST /webhook/jobapp-pending` cu header `X-JobApp-Secret` real, verific
  status 200 și forma `{ok, commands: [...]}`.
- Succes = confirmă că auth + rutare + citire Sheet funcționează end-to-end pe instanța
  live. Nu se validează conținutul `commands` (poate fi listă goală — e ok).

## 7. Workstream 4 — Cod Python & CI

- Code review pe `agents/`, `generators/`, `sender/`, `scrapers/`, `tracker/`, `main.py`:
  bug-uri reale / probleme de securitate / dead code — nu nitpicks de stil. Fix-uri aplicate
  direct dacă sunt mici și clare; raportate pentru discuție dacă sunt ambigue.
- Elimină flag-ul no-op `--use-ai` din `main.py` (păstrează doar `--no-ai`).
- Adaugă `.github/workflows/tests.yml`: rulează `pytest -q` pe push/PR către `main`,
  Python 3.12, fără secrete (suita nu atinge rețeaua — confirmat din `tests/conftest.py`,
  fixtures mock).

## 8. Secvențiere

```
1. Igienă repo (commit-uri separate: docs/, appscript.gs, scraper diff, CLAUDE.md)
2. n8n: export Trigger+Upload → n8n_workflows/ + update deploy script + README (commit)
3. n8n: smoke-test read-only jobapp-pending (raportează rezultat, fără commit)
4. n8n: șterge cele 4 workflow-uri moarte (acțiune live, ireversibilă — confirmată deja)
5. Cod: code review + fix-uri + cleanup --use-ai (commit)
6. CI: adaugă .github/workflows/tests.yml (commit)
7. Rulare finală: pytest complet + git status curat
```

## 9. Non-goals

- Orice workflow n8n non-`JobApp*` de pe instanța comună (Rotocon, Rankenstein, LinkedIn
  content, stripe-orders, Keyword Research etc.) — neatinse, inclusiv cele care eșuează.
- Apeluri live către `jobapp-ingest` / `jobapp-upload` / `jobapp-trigger` — rămân netestate
  automat în această lucrare.
- Nicio modificare a datelor din Google Sheet-ul real sau din `data/Job_Application_Tracker.xlsx`.
- Fără refactor arhitectural al agenților (pattern-ul `BaseAgent`/`AgentResult` rămâne neatins) —
  doar fix-uri punctuale găsite la code review.
- Fără schimbarea providerului AI/model sau a config-ului de business (`settings.yaml` conținut).

## 10. Criterii de succes

1. `git status` curat pe branch-ul curent (fără fișiere netrackuite relevante rămase).
2. `n8n_workflows/` conține toate cele 4 definiții; `deploy_n8n_workflows.py` le poate
   redeploya pe toate (idempotent, ca azi).
3. Instanța n8n nu mai are cele 4 workflow-uri moarte; cele 4 `JobApp*` active rămân active.
4. Smoke-test `jobapp-pending` răspunde 200 cu forma așteptată.
5. `.github/workflows/tests.yml` există și (verificabil manual sau la primul push) rulează
   cei 97+ teste verzi.
6. Toate testele existente + eventualele fix-uri de cod trec local (`pytest -q`).
7. `CLAUDE.md` reflectă corect starea reală (Apps Script tracked, 4 workflow-uri n8n).
