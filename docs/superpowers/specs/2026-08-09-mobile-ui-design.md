# Mobile UI (`/m/`) — design

Data: 2026-08-09

## Context

`mobile_app/Mobile app design project.zip` conține un handoff de design (`JobApp Mobile.dc.html` + `README.md`): prototip HTML clickabil, high-fidelity, pentru un client mobil al pipeline-ului Job_app. Nu e cod de producție — trebuie recreat ca template-uri Django, conectate la view-urile reale din `dashboard/jobs/`.

Deployment live (domeniu `app.findajob.pro`, VPS, DNS, Caddy, autentificare de producție) e **scop separat**, tratat într-o rundă de brainstorming ulterioară, după ce UI-ul mobil e complet și testabil local.

## Scop acestei faze

Construim UI-ul mobil complet (9 ecrane din README: Inbox, Detaliu job, Preview, Transmit, Success, Search, Pipeline, Sent, Settings), rulabil local prin `python dashboard/manage.py runserver`, la ruta `/m/`.

## Arhitectură

- Rută nouă `/m/` în `dashboard/jobs/urls.py`, cu view-uri Django proprii (funcții separate de cele desktop, dar reutilizând logica din `generation_bridge`, `search_bridge`, `sheet_bridge`, `extractor` — nimic din pipeline-ul CLI nu se duplică).
- Template-uri noi în `dashboard/jobs/templates/jobs/mobile/`, cu `mobile/base.html` propriu (viewport mobil, tab bar fix, design tokens ca variabile CSS după paleta din README §Design Tokens). Complet separat de `jobs/base.html` (desktop) — desktop-ul rămâne neschimbat.
- HTMX (adăugat ca dependență nouă — nu există încă în proiect) pentru:
  - Polling stare `generating` (execute_job) / `sending` (transmit_job).
  - Tab-uri Preview (Email / Anschreiben / Files) fără reload.
  - Toggle-uri Settings (piețe, provider) — optimistic update + POST.
  - Refresh listă rezultate în timpul unei căutări Search active.
- CSS scris de mână, fără framework nou, urmând tokens/spacing/radius din README.

## Date & model

- Fără câmp nou `route` pe `Job` — se derivă în view/template: `email_hr` prezent → `"email"`, altfel → `"portal"`.
- `Job.send_state` capătă o valoare nouă posibilă: `"applied"` (doar pentru rută portal). Setată de un view nou `mark_applied(request, pk)` (POST simplu, fără efect pe Sheet, fără apel n8n).
- Migrare Django: `send_state` rămâne `CharField` — nicio schimbare de schemă necesară, doar convenție nouă de valoare.

## Ecrane

### Inbox, Detaliu, Preview, Transmit, Success
Recreează markup/stiluri din README §Screens 1-5 pixel-perfect (culori, spacing, tipografie, stări pastile, inel scor SVG cu overlay HTML). Reutilizează view-urile desktop existente (`job_detail`, `preview_job`, `transmit_job`, `execute_job`, `recover_job`) — doar `template_name` diferă (`mobile/...`) și redirect-urile țintesc rutele `/m/...`.

Card job (Inbox): inel scor 44×44 (SVG cerc, arc `stroke-dasharray` proporțional cu scorul, overlay HTML absolut centrat cu numărul — nu `<text>` SVG). Culoare scor: ≥75 verde, ≥55 amber, altfel roșu.

Bara de acțiuni din Detaliu variază după `send_state` + rută (draft+email / draft+portal / generating / generated / sent / error / applied) — vezi tabelul din README §2.

### Search
Reutilizează `search_bridge` (thread de fundal existent, `SearchRun`/`SearchResult`). UI: chips multi-select keyword groups + markets, log monospace care crește în timpul rulării (poll HTMX), listă rezultate cu badge prescore/rejected/skipped/imported — logică identică desktop-ului (`search_bridge._holds_package`), doar markup diferit.

### Sent
`sent_list` + badge nou `APPLIED` (sky) alături de `GESENDET` (verde) pentru `send_state="applied"`.

### Pipeline (nou, read-only)
View nou care citește:
- Ultimul rezultat de rulare / funnel (scraped/prefilter/analyzed/generated/sent) — din log-urile agenților în `output/logs/` dacă există un sumar structurat; altfel afișează „N/A” pe rândurile fără sursă (nu se inventează date).
- Rate limit curent din `output/logs/email_rate_state.json` (aceeași sursă folosită de `sender/smtp_sender.py`).
- Status agenți (SUCCESS/PARTIAL/FAILED) — din ultimele log-uri de rulare, dacă disponibile.

**Fără trigger de pornire a pipeline-ului din UI** în această fază — butonul „Run full pipeline” din prototip NU se implementează. Triggerul rămâne exclusiv CLI/launchd, ca acum.

### Settings (nou, citire + scriere)
- Citește `config/settings.yaml`: piețe active (`scraping.markets.*`), provider (`email.provider` sau echivalent), max emails/day (`email.max_emails_per_day`), min match score.
- Scrie modificări (toggle piață, schimbare provider, stepper max emails/day) direct în `config/settings.yaml`.
- Înainte de fiecare salvare: backup `settings.yaml.bak` (overwrite, nu versionat — doar plasă de siguranță pentru ultima scriere bună).
- Min match score rămâne read-only (ca în prototip — „read-only 50”), nu se editează din UI.

## Autentificare

`LoginRequiredMiddleware` (Django 6) aplicat **doar pe ruta `/m/`** (prin `path()` cu un middleware/decorator scopat, nu global) — desktop-ul (`/`) rămâne neschimbat, fără login, ca acum. Motiv: `/m/` va fi expus public pe domeniu în faza de deployment; desktop-ul rămâne uz local.

## Testare

- Teste Django (pytest, `dashboard/jobs/tests`) pentru:
  - `mark_applied`: schimbă `send_state` la `"applied"` doar pentru joburi rută portal; nu afectează Sheet.
  - Settings view: citire corectă din YAML mock; scriere + verificare backup `.bak` creat înainte de suprascriere.
  - Pipeline view: randare corectă cu fișiere log/rate-limit lipsă (afișează „N/A”, nu crapă) și cu fișiere prezente (mock).
- Fără E2E automat (nu există infra Playwright în proiect) — verificare manuală prin browser după implementare (skill `run`).

## Ce NU e în scop (faza asta)

- Deployment live, DNS, Caddy, HTTPS, PWA manifest — rundă separată de brainstorming.
- Trigger de pornire pipeline din UI.
- Autentificare pe desktop (`/`).
- Modificarea `min_prescore`/prag de scor din Settings.
