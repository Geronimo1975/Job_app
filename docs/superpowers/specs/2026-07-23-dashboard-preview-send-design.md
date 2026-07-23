# Design: Dashboard Preview & Send + meniu lateral

**Data:** 2026-07-23
**Status:** aprobat (brainstorming)
**Autor:** Claude Code + Geronimo
**Înlocuiește:** fluxul SENDEN din `2026-07-23-dashboard-sheet-send-design.md` (JOB-41)

## Scop

Utilizatorul vede **exact** ce pleacă — emailul, Anschreiben-ul, atașamentele —
apoi transmite direct din dashboard. UI reorganizat: meniu lateral cu secțiunea
inițială „Joburi" (tabel cu acțiuni pe rând: Execută → Preview → Transmite +
status) și „Trimise" (istoric).

Motivul înlocuirii SENDEN: poll-ul regenerează Anschreiben-ul la trimitere
(temperature 0.7 → text diferit) — preview-ul ar fi minciună. Trimiterea
directă e singura cale WYSIWYG.

## Decizii (brainstorming)

| Decizie | Alegere |
|---|---|
| Transmitere | **Direct din dashboard** prin `SmtpSender` (rate-limit partajat pe disc), apoi upsert în Sheet `Email Status=GESENDET` + `Gesendet am` |
| Fluxul SENDEN | **Eliminat complet** (butoane, confirmare, reset, refresh pending, poll_mode, seen_in_pending). „Salvează în Sheet" rămâne |
| Editare preview | **Da** — body email și Anschreiben editabile; PDF-urile se regenerează din textul editat |
| Siguranță | **Dry-run implicit** + comutator LIVE explicit pe confirmare |

Coexistență cu celelalte sisteme: col P și col M nu se mai ating deloc.
`GESENDET` în col R e recunoscut de ambele filtre existente → fără dubluri.
Gardă suplimentară pre-LIVE: `N8nClient.get_known_urls()` (workflow-ul
`JobApp Urls`, read-only) — dacă URL-ul e deja `done/sent/GESENDET` în Sheet,
LIVE se blochează cu mesaj; dacă verificarea pică (n8n indisponibil), se
permite cu avertisment vizibil (fail-open advisory).

## Stări noi (`send_state`)

`draft → generated → sent`, plus `error` (generare sau SMTP eșuat; motivul în
`send_error`). Stările vechi (`senden_pushed`/`processed`/`unconfirmed`) se
resetează la `draft` prin migrație de date; câmpul `seen_in_pending` dispare.

## Modelul `Job` — câmpuri noi (migrație)

| Câmp | Tip | Rol |
|---|---|---|
| `email_subject` | CharField(300, blank) | subiect editabil |
| `email_body` | TextField(blank) | body editabil |
| `anschreiben_text` | TextField(blank) | scrisoarea editabilă |
| `anschreiben_pdf_path` | CharField(500, blank) | PDF Anschreiben generat |
| `bewerbung_pdf_path` | CharField(500, blank) | „Bewerbung Komplett" (Anschreiben+CV+certificate) |
| `attachments_json` | JSONField(list) | căile atașamentelor colectate |
| `generated_at` | DateTimeField(null) | când s-a generat pachetul |
| `sent_at` | DateTimeField(null) | când s-a trimis LIVE |
| `send_error` | TextField(blank) | motivul stării `error` |

## Componente

### 1. `jobs/generation_bridge.py` (punte nouă — importă din `generators/` + `sender/`, read-only)

- `generate_package(job) -> dict` — apelează:
  - `CoverLetterGenerator(config, root).generate(company=, position=, job_description=job.description, location=, language=, special_instructions=)` → text Anschreiben; `save_cover_letter(...)` → .txt; `text_to_pdf` → PDF; `assemble_bewerbung(text, cv, out, certificates=...)` → Komplett (căile CV/certificate din `config.documents`, ca în `EmailAgent._collect_attachments`).
  - `EmailBodyGenerator(config, root).generate(company=, position=, contact_person=, language=)` → body.
  - Subiect: `Bewerbung als {position} — {applicant.name}`.
  - Atașamente: aceeași prioritate ca `EmailAgent` (Komplett singur; altfel Anschreiben PDF + CV + certificate).
  - Întoarce `{ok, email_subject, email_body, anschreiben_text, anschreiben_pdf_path, bewerbung_pdf_path, attachments, error?}` — nu ridică excepții.
- `regenerate_pdfs(job) -> dict` — după editarea textului: re-rulează `text_to_pdf` + `assemble_bewerbung` din `job.anschreiben_text`, actualizează căile + atașamentele.
- `send_package(job, live: bool, provider=None) -> dict` — `SmtpSender.build_email(to_email=job.email_hr, subject, body, attachments)` + `.send(msg, provider=, dry_run=not live)`; întoarce `{ok, live, error?}`. Rate-limit-ul e în `send()` (starea pe disc partajată cu poll-ul).
- `already_sent_in_sheet(job) -> str` — `"da"` dacă URL-ul e în `get_known_urls()`, `"nu"` altfel, `"necunoscut: <motiv>"` la eroare (advisory).

### 2. `sheet_bridge.py` — curățare + completare

- Dispar: `poll_mode`, `refresh_states`, `preview_sheet_row`, parametrul `senden` (push devine doar upsert de date; `sende_befehl` nu se mai emite niciodată).
- Apare: `mark_sent_in_sheet(job) -> dict` — ingest cu `{url, email_status: "GESENDET", gesendet_am: "YYYY-MM-DD HH:MM" (Berlin)}` (FIELD_MAP le mapează pe col R / „Gesendet am"). Eșecul NU anulează trimiterea locală — mesaj de avertisment cu instrucțiune de completare manuală.

### 3. Views + rute

- Dispar: `send_job`, `reset_job`, `refresh_states_view` (+ rute + template `send_confirm.html`).
- `execute_job` (POST) — `generate_package` → salvează câmpurile, `send_state=generated`; eroare → `error` + `send_error`. Re-execuția e permisă din orice stare în afară de `sent` (regenerare completă).
- `preview_job` (GET/POST) — GET: pagina de preview (destinatar, subiect+body editabile, Anschreiben editabil, atașamente cu link-uri, avertisment dacă `email_hr` gol). POST: salvează subiect/body/anschreiben editate + `regenerate_pdfs`.
- `transmit_job` (GET/POST) — GET: confirmare (rezumat, atașamente, verdictul `already_sent_in_sheet`, rate-limit info, **comutator LIVE** — checkbox nebifat = dry-run). POST: blocat dacă `send_state != generated` sau `email_hr` gol sau (LIVE și deja trimis în Sheet); altfel `send_package`; LIVE reușit → `sent` + `sent_at` + `mark_sent_in_sheet`; dry-run reușit → mesaj, rămâne `generated`; eșec → `error` + `send_error`.
- `serve_doc` (GET) — `FileResponse` **doar** pentru căile stocate pe job (`anschreiben_pdf_path`, `bewerbung_pdf_path`, intrări din `attachments_json`), identificate prin index, nu prin cale liberă → zero path traversal.
- `sent_list` (GET) — secțiunea „Trimise": joburile `sent`, cu `sent_at`.
- `delete_job` — gardă nouă: nu pe `senden_pushed` (dispare), ci avertisment simplu la `sent` (istoricul se pierde local; Sheet-ul rămâne).

### 4. UI (meniu lateral)

- `base.html`: layout cu sidebar stânga — „Joburi" (activ pe index/preview/transmit) și „Trimise"; pe mobil devine bară orizontală sus.
- Index: coloana „Trimitere" cu pastile `draft/generated/sent/error` + acțiuni pe rând după stare: `Execută` (draft/error), `Preview`+`Transmite`+`Re-execută` (generated), `Vezi` (sent).
- `preview.html`, `transmit_confirm.html` noi.
- Generarea durează ~10–30s (AI sincron): butonul „Execută" arată stare de loading (disabled + text), acceptabil pentru unealtă locală.

## Erori (nimic silențios)

| Situație | Comportament |
|---|---|
| `ANTHROPIC_API_KEY` lipsă la Execută | generatorul cade pe template (convenția `cover_letter_gen`) — pagina marchează vizibil „fallback template" dacă textul nu vine de la AI |
| SMTP creds lipsă la Transmite | mesaj clar pe confirmare, buton LIVE blocat |
| Rate limit atins | `send()` refuză → `error` cu motiv vizibil |
| `mark_sent_in_sheet` eșuat după LIVE | starea locală rămâne `sent`; avertisment: completează manual col R în Sheet |
| deja trimis în Sheet (get_known_urls) | LIVE blocat; dry-run permis |
| verificarea Sheet indisponibilă | LIVE permis cu avertisment explicit |

## Testare

Mock-uri pe `CoverLetterGenerator`, `EmailBodyGenerator`, `text_to_pdf`,
`assemble_bewerbung`, `SmtpSender`, `N8nClient` — nimic nu lovește rețeaua/AI:

- `generate_package` populează toate câmpurile; eroare AI → fallback marcat.
- `send_package` dry-run nu apelează send cu dry_run=False; LIVE da.
- `transmit_job`: dry-run păstrează `generated`; LIVE → `sent` + `mark_sent_in_sheet` apelat; blocat pe deja-trimis/fără email/stare greșită.
- `preview_job` POST salvează editările + regenerează PDF.
- `serve_doc` refuză orice cale care nu e a jobului.
- `sent_list` filtrează corect.
- Suita pipeline rămâne neatinsă.

## Configurare

Rulare cu `.env` încărcat (`ANTHROPIC_API_KEY`, `GMX_PASSWORD`/`BREVO_SMTP_KEY`,
`N8N_WEBHOOK_SECRET`): `set -a; source .env; set +a` înainte de `runserver`.
