# Handoff: JobApp Mobile — client mobil pentru pipeline-ul Job_app

## Overview
Interfață mobilă (390×844) pentru pipeline-ul de aplicare automată la joburi (Job_app — Django 6 + agenți Python). Acoperă tot fluxul: inbox de joburi cu scor de potrivire → detaliu → generare pachet (email + Anschreiben + PDF-uri) → preview/editare → transmitere pe email SAU aplicare prin portal → istoric trimise → căutare ad-hoc (JobSpy) cu import → status pipeline → setări (piețe, provider, rate limit).

Țintă de deployment: servită din Django-ul existent (`dashboard/`), publicată pe VPS la `mobile.findajob.pro` (Caddy + Gunicorn, HTTPS, autentificare obligatorie).

## About the Design Files
`JobApp Mobile.dc.html` este o **referință de design în HTML** — un prototip clickabil cu date demo, nu cod de producție. Sarcina: **recrearea acestui design în codebase-ul Django existent** (template Django + HTMX sau JS vanilla, conform pattern-urilor din `dashboard/jobs/templates/`), conectat la view-urile reale. Nu copia fișierul direct; folosește-l ca specificație vizuală și comportamentală. Prototipul folosește un runtime propriu (`support.js`, sintaxă `sc-if`/`sc-for`) — ignoră mecanica lui; relevant e markup-ul, stilurile inline și logica din clasa `Component`.

## Fidelity
**High-fidelity.** Culori, spațieri, tipografie și stări sunt finale — recreează pixel-perfect.

## Integrare cu backend-ul existent

View-urile Django existente acoperă deja toate acțiunile (în `dashboard/jobs/`):

| Ecran mobil | Endpoint existent | Observații |
|---|---|---|
| Inbox | `index` (`/`) | Necesită variantă JSON sau template mobil |
| Detaliu job | `job_detail` (`/job/<pk>/`) | |
| Generate package | `execute_job` (`/job/<pk>/execute`) | Stare `generating` → poll sau HTMX |
| Preview / editare | `preview_job` (`/job/<pk>/preview`) + `job_detail` POST | Subject/body/letter editabile |
| Transmit | `transmit_job` (`/job/<pk>/transmit`) | Verifică `already_sent_in_sheet` + rate limit |
| Recover (după eroare) | `recover_job` (`/job/<pk>/recover`) | |
| Search | `search_index` (`/cauta/`), `search_run` (`/cauta/<pk>/`) | |
| Import rezultate | `import_search_results` (`/cauta/<pk>/import`) | |
| Sent | `sent_list` (`/trimise/`) | |
| Fișiere PDF | `serve_doc`, `serve_attachment` | Pentru tab-ul Files |

De adăugat:
- **Rută mobilă** `/m/` care servește UI-ul mobil (template separat sau răspunsuri JSON + client).
- **Aplicare prin portal**: joburile cu `route=portal` (fără email de aplicare) au butoanele „Apply via portal" (deschide `url` în tab nou) și „Mark as applied" → stare nouă `applied` pe model (sau valoare nouă în câmpul de status existent), afișată în Sent cu badge APPLIED.
- **Ecran Pipeline**: agregare status agenți + funnel (scraped/prefilter/analyzed/generated/sent) + rate limit curent — datele există în pipeline (rate limit persistat pe disc, log-uri agenți).
- **Setări**: toggle piețe (config piețe existent), provider GMX/Brevo, max emails/day, min match score.

### Deployment (VPS + domeniu)
1. DNS: A record `mobile.findajob.pro → IP VPS`.
2. Gunicorn + Caddy (auto-HTTPS). Config Caddy minim:
   ```
   mobile.findajob.pro {
       reverse_proxy 127.0.0.1:8000
       basic_auth { george <hash bcrypt> }
   }
   ```
3. `settings.py`: `ALLOWED_HOSTS=["mobile.findajob.pro"]`, `CSRF_TRUSTED_ORIGINS=["https://mobile.findajob.pro"]`, `DEBUG=0`, `DJANGO_SECRET_KEY` fix din env (acum e efemer — ar invalida sesiunile la restart).
4. **Autentificare obligatorie** — aplicația trimite emailuri reale și are chei API. Basic Auth din Caddy (minim) sau `LoginRequiredMiddleware` Django 6 (corect).
5. iPhone: Safari → `https://mobile.findajob.pro` → Share → Add to Home Screen. Adaugă `<meta name="apple-mobile-web-app-capable" content="yes">` + `theme-color` + manifest PWA minimal pentru aspect de app nativă.

## Screens / Views

Toate ecranele trăiesc într-un container 390px lățime, `height: 100dvh` pe mobil real (prototipul folosește 844px fix cu colțuri rotunjite 28px — doar pentru prezentare, nu recrea rama). Tab bar fix jos pe cele 5 ecrane rădăcină; ecranele Detail/Preview/Transmit/Success sunt push-uri full-screen fără tab bar.

### 1. Inbox (feed)
- **Header**: titlu „Inbox" 24px/700, letter-spacing -0.02em; dreapta: contor „N of M jobs" 13px muted. Padding 22px 20px 12px.
- **Filtre**: rând orizontal scrollabil de chips-uri (All / Draft N / Ready N / Sent N / Error N). Chip activ: fundal accent, text accent-fg, 700; inactiv: fundal surface, border 1px, text muted, 600. Padding 7px 14px, radius 20px, 12.5px.
- **Card job** (gap 10px, padding listă 2px 16px 16px): fundal surface, border 1px border, radius 14px, padding 14px, `active:scale(.985)`.
  - Stânga: inel scor 44×44 — SVG cerc r=18 stroke-width=4, track culoare border, arc colorat după scor (`stroke-dasharray: score/100*113 113`, rotit -90°); numărul scorului e un **overlay HTML absolut centrat** (12px/700), NU `<text>` SVG.
  - Culoare scor: ≥75 verde #22c55e, ≥55 amber #f59e0b, altfel roșu #ef4444.
  - Mijloc: titlu 14.5px/600 ellipsis; „Company · Location" 12.5px muted; rând pastile (gap 6px): piață (DE/SE/NL/RO/REMOTE — fundal surface2, border), rută EMAIL (verde 15% alpha) sau PORTAL (sky #38bdf8 15% alpha), stare (DRAFT gri / GENERATING amber / READY sky / SENT verde / ERROR roșu / APPLIED sky). Pastile: 10px/700, letter-spacing .04em, padding 2-3px 7-8px, radius 20px.
  - Dreapta: timestamp 11px muted.

### 2. Detaliu job (push full-screen)
- Buton back „‹ Inbox" culoare accent, 15px, sus-stânga.
- Titlu 19px/700 lh 1.25; „Company · Location" 14px muted; rând pastile: piață, stare, salariu (dacă există).
- Dreapta sus: inel scor 64×64 (r=27, stroke 5, dasharray score/100*170 170) cu overlay „87 / MATCH" (17px/700 + 7.5px muted).
- **Card rută**: pastilă EMAIL/PORTAL + valoarea (email de aplicare sau URL) în monospace 13px muted, ellipsis.
- **Why this match**: heading uppercase 11px/600 muted; listă motive cu „+" accent 700 (13px). Motivele vin din analiza AI (matching score breakdown).
- **Description**: 13.5px muted, lh 1.6, `white-space: pre-wrap`.
- **Banner eroare** (stare error): fundal roșu 10% alpha, border roșu 30%, radius 12px, text 13px #f87171 — mesajul SMTP.
- **Bară acțiuni** (fixă jos, border-top, fundal surface, padding 14px 20px 22px, gap 10px), în funcție de stare:
  - draft + email: „Generate package" (accent, full)
  - draft + portal: „Apply via portal" (accent — `window.open(url)`) + „Mark as applied" (secundar)
  - generating: buton dezactivat cu spinner „Generating email + Anschreiben…"
  - generated: „Preview" (secundar) + „Transmit" (accent)
  - sent: „View sent package" (secundar)
  - error: „Recover package" (secundar) + „Re-generate" (accent)
  - applied: badge static „Applied via portal ✓"
- Butoane: padding 14px, radius 12px, 700. Primar: fundal accent, text accent-fg. Secundar: fundal surface2, border 1px borderS.

### 3. Preview package (push)
- Header: back / „Preview package" 15px/700 centrat.
- **Tab-uri segmentate**: Email | Anschreiben | Files — container surface2 radius 10px padding 3px; segment activ fundal accent radius 7px 12px/700.
- **Email**: To (read-only, monospace, surface), Subject (input editabil), Body (textarea 12 rânduri). Inputuri: padding 11-12px 13px, border borderS, radius 10px, fundal surface.
- **Anschreiben**: label cu limba (DE/EN), textarea 20 rânduri, notă „Edits are kept — re-generating overwrites them." 12px muted.
- **Files**: 4 carduri atașamente cu icon PDF roșu: Anschreiben.pdf (Generated · 1 page), Lebenslauf_….pdf (CV · 2 pages), Zeugnisse.pdf (4 pages), Bewerbung_Komplett.pdf (Merged · 7 pages). În producție: link-uri la `serve_doc`/`serve_attachment`.
- Jos: „Save edits" (secundar, devine „Saved ✓") + „Transmit" (accent, doar dacă starea e generated).

### 4. Transmit (push)
- Card sumar: To / Subject / Attachments („4 PDFs · …") / Provider („GMX SMTP (smtp.gmx.net)" sau „Brevo transactional API"). Label-uri 11px uppercase muted.
- Card rate limit: „Rate limit today" + „3 / 10", bară progres 8px (accent; amber ≥70%; roșu la 100%).
- Banner avertisment amber: „Live send — the email goes out immediately and GESENDET is written to the Sheet."
- „Confirm & send" full-width accent 15px; la trimitere → spinner „Sending via SMTP…" (~1.7s în demo).
- În producție: dezactivează Confirm când rate limit e atins sau `already_sent_in_sheet` e true.

### 5. Success (push)
- Centrat: icon check în cerc 72px stroke accent 1.6, „Application sent" 21px/700, subtitlu „{title} at {company}" 14px muted, „GESENDET written to Sheet · 4 / 10 used" 12.5px, buton „Back to inbox" secundar.

### 6. Search
- Header „Search" + subtitlu „Ad-hoc JobSpy run · results staged before import".
- **Keyword groups** (chips multi-select): AI & Agents, Python & Backend, Automation / n8n, Game Dev.
- **Markets** (chips multi-select): cele 10 piețe.
- Notă context (card surface 12px): „Boolean queries · N keyword groups × M markets · Indeed + LinkedIn (best-effort) · dedupe against seen_jobs".
- „Run search" accent full → **stare running**: card cu spinner „Scraping…" + log monospace 12px care crește (ex: „[de/indeed] 214 rows · sanitized: is_remote → local filter", „[warning] linkedin rate-limited (best-effort)").
- **Rezultate**: header „Results" + „N staged · M below prescore 30". Card rezultat: badge prescore (pătrat radius 8px, colorat după scor; roșu pentru rejected), titlu 13.5px/600, „Company · platform · țară" 12px muted, buton „Import" (secundar mic 12px). Rejected: opacity .45, badge „Below gate". Deja văzut: badge „Skipped — holds package" amber. După import: badge verde „Imported", jobul apare în Inbox ca draft.
- „New search" (ghost, border borderS) resetează.

### 7. Pipeline
- Subtitlu: „Last full run · today 06:00 · 4m 12s".
- **Card funnel**: 5 rânduri label (86px, 12px muted) + bară orizontală 8px (accent, lățime proporțională, min 3%) + valoare 13px/700 tabular: Scraped 412 / Prefilter 48 / Analyzed 22 / Generated 9 / Sent 3.
- **Card rate limit**: identic cu cel din Transmit + notă „Daily cap persisted on disk · resets at midnight".
- **Card agenți**: rânduri cu dot status 8px (verde SUCCESS / amber PARTIAL / roșu FAILED), nume 13.5px/600, detaliu 12px muted, pastilă status. Agenții: ScraperAgent, AnalyzerAgent, CoverLetterAgent, DocumentAgent, EmailAgent, n8n SyncAgent.
- „Run full pipeline (dry-run)" accent full; în rulare: dezactivat, „Running… scrape → analyze → generate".

### 8. Sent
- Header „Sent" + „N applications".
- Card: titlu + badge GESENDET (verde) sau APPLIED (sky); „Company · Location"; jos stânga-dreapta: canal („GMX SMTP" / „Brevo API" / „Portal · url") și data „07.08 · 16:42".
- Empty state: text centrat în border dashed.

### 9. Settings
Secțiuni cu heading uppercase 11px/600 muted; carduri surface radius 14px, rânduri despărțite de border 1px:
- **Sending**: Provider (segmented GMX | Brevo), Max emails/day (stepper − 10 + , butoane 30×30 radius 8px), Min match score (read-only 50).
- **Appearance**: Theme (segmented Dark | Light | True Black).
- **Markets (N active)**: 10 rânduri cu nume + detaliu (ex: „de · letters in German · email flow", „ro · eJobs · portal only") + **switch iOS**: 44×26, radius 13, knob alb 20px, on = fundal accent, tranziție .18s.

### Tab bar
5 tab-uri egale: Inbox (inbox icon), Search (lupă), Pipeline (activity), Sent (send), Settings (gear). Icon 22px stroke 1.8 + label 10px/600. Activ: culoare accent; inactiv: muted. Fundal surface, border-top, padding 8px 6px 18px (safe area jos).

## Interactions & Behavior
- Navigare rădăcină: schimbare instant de ecran, animație `rise` (fade + translateY 10px, .2s ease) la fiecare ecran.
- Push-uri (Detail → Preview/Transmit → Success): straturi absolute peste feed, z-index crescător; back revine fără pierdere de stare.
- Generate: stare `generating` cu spinner ~1.6s (în producție: poll la status sau răspuns HTMX).
- Transmit: spinner ~1.7s → Success → incrementare rate limit → jobul devine SENT.
- Import din search: rezultatul primește badge „Imported", jobul se inserează la începutul Inbox-ului ca draft.
- Editările din Preview se salvează explicit („Save edits" → „Saved ✓"); mesaj că re-generarea le suprascrie.
- Toggle-uri piețe și segmented controls: schimbare optimistă instant.
- Spinner: cerc 15-16px border 2px, border-top accent, `spin .8s linear infinite`.

## State Management
- Stare per job: `draft → generating → generated → sent`, ramuri `error` (recover → generated) și `applied` (doar rută portal).
- Rută derivată din date: email de aplicare prezent → `email`; altfel → `portal`.
- Contoare filtre derivate din lista de joburi; Sent include `sent` + `applied`.
- Rate limit: `sentToday / cap`, partajat cu pipeline-ul CLI (persistat pe disc).
- Preview: buffer local (subject/body/letter) separat de job până la Save.

## Design Tokens

Temă Dark (implicită — aliniată cu dashboard-ul desktop existent):
| Token | Valoare |
|---|---|
| bg | #0f172a |
| surface | #1e293b |
| surface2 | #272f42 |
| fg | #f8fafc |
| muted | #94a3b8 |
| border | #334155 |
| borderS (strong) | #475569 |
| accent | #22c55e (text pe accent: #052e16) |
| ok / warn / err | #22c55e / #f59e0b / #ef4444 |
| info (sky) | #38bdf8 |

Teme alternative: Light (bg #f1f5f9, surface #fff, fg #0f172a, muted #64748b, border #e2e8f0) și True Black (bg #000, surface #101114). Accente alternative: #38bdf8, #f59e0b, #e879f9.

- Font: system stack (-apple-system, Segoe UI, Roboto…); monospace ui-monospace/Menlo pentru email/URL/log.
- Radius: carduri 14px, inputuri/butoane/bannere 10-12px, pastile 20px, segmented 10/7px.
- Spațiere: padding ecran 20px lateral; gap liste 8-10px; padding card 14-16px.
- Text: titlu ecran 24px/700; titlu card 14-14.5px/600; secundar 12-13px; pastile 10px/700; min 10px.
- Pastile de stare: fundal = culoarea la 15% alpha, text = culoarea plină.

## Assets
Fără imagini. Toate iconurile sunt SVG inline stil Feather (stroke 1.8-2, round): inbox, search, activity, send, settings, chevron-left, file, check-circle.

## Files
- `JobApp Mobile.dc.html` — prototipul complet (markup + logică + date demo în clasa `Component`).
