# Design: Dashboard → Sheet → trimitere email (SENDEN)

**Data:** 2026-07-23
**Status:** aprobat (brainstorming)
**Autor:** Claude Code + Geronimo
**Extinde:** `2026-07-23-dashboard-paste-link-design.md` (JOB-39)

## Scop

Dashboard-ul devine front-end pentru fluxul de aplicare: completezi câmpurile
pe care le are Sheet-ul „Bewerbungen" (setul FIELD_MAP), împingi rândul în
Sheet prin n8n și declanșezi trimiterea de email scriind `SENDEN` în col P
„Sende Befehl" — exact ce faci azi manual în Sheet. Trimiterea efectivă rămâne
treaba poll-ului existent (`--step poll`, launchd): Anschreiben, rate-limit,
`GESENDET`, golirea col P.

## Decizii luate (brainstorming)

| Decizie | Alegere | Motiv |
|---|---|---|
| Mecanism trimitere | **Sheet + poll** (SENDEN în col P via `N8nClient.ingest()`) | Zero cod nou de trimitere; Sheet rămâne sursa de adevăr; refolosește calea testată în producție |
| Set de câmpuri | **FIELD_MAP** (nu toate cele 28 coloane) | Doar ce scrie pipeline-ul; evită coloane fantomă la upsert |
| Siguranță | **Pagină de confirmare + mod poll vizibil** | Nu poți trimite din greșeală; vezi exact rândul care pleacă |

## Revizuirea deliberată a izolării

Spec-ul anterior cerea izolare totală. Se revizuiește **limitat și explicit**:

- Dashboard-ul **importă** `sender/n8n_client.py` + `sender/n8n_mapping.py`
  (refolosire read-only; NU le modifică). Alternativa — duplicarea FIELD_MAP —
  ar garanta desincronizare.
- Rămân interzise: importuri din `agents/`, SMTP direct, scrierea col M
  „Status" (mașina de stări Apps Script), orice modificare în `sender/`.
- Tehnic: `dashboard/manage.py` adaugă rădăcina repo-ului în `sys.path`.

## Modelul `Job` — extindere (migrație nouă)

Câmpuri noi:

| Câmp | Tip Django | Corespondent Sheet |
|---|---|---|
| `contact_person` | `CharField(max_length=200, blank=True)` | Kontakt Person |
| `notizen` | `TextField(blank=True)` | Notizen |
| `match_score` | `IntegerField(null=True, blank=True)` | Match Score |
| `send_state` | `CharField(max_length=20, default="draft")` | — (stare locală) |
| `sheet_pushed_at` | `DateTimeField(null=True, blank=True)` | — (stare locală) |

`send_state`: `draft` → `senden_pushed` (SENDEN împins în Sheet) → `processed`
(dedus: URL-ul nu mai apare în pending după un push SENDEN).

Derivate la push (nu câmpuri noi): `gefunden_am` = `created_at.date()`
(format `YYYY-MM-DD`, ca în `make_job`). Câmpul `status` (col M) **nu se emite
niciodată** — dublu-protejat: dashboard-ul nu pune cheia, iar `n8n_mapping`
filtrează oricum valorile din afara mașinii de stări.

Câmpurile existente rămân neschimbate (`description` și `raw_json` sunt doar
locale — nu au coloană în FIELD_MAP și nu se împing).

## Componente noi

### 1. `jobs/sheet_bridge.py` — puntea spre pipeline

Funcții pure + un wrapper subțire peste `N8nClient`:

- `load_pipeline_config() -> dict` — citește `config/settings.yaml` cu
  `yaml.safe_load` (fără import din `agents/`).
- `job_to_internal(job: Job, sende_befehl: str | None) -> dict` — modelul
  Django → dict intern cu chei engleze: `company, title, location, url,
  source, email_hr, contact_person, language, gefunden_am, notizen,
  match_score` (doar cele cu valoare) + `sende_befehl` doar când e cerut.
  NU emite `status`, `description`, `raw_json`.
- `push_to_sheet(job: Job, senden: bool) -> dict` — construiește dict-ul,
  apelează `N8nClient(config).ingest([...])`, întoarce
  `{ok, error?, skipped?}`. Verifică întâi `N8N_WEBHOOK_SECRET` în env și
  `n8n.enabled` — lipsă → `{ok: False, error: "..."}` fără apel HTTP.
- `poll_mode() -> str` — citește `scripts/com.jobapp.poll.plist`; dacă
  argumentele conțin `--live` → `"LIVE"`, altfel/absent → `"dry-run"`.
  La orice eroare → `"necunoscut (dry-run implicit)"`.
- `refresh_states(jobs: QuerySet) -> int` — `get_pending()`; pentru rândurile
  cu `send_state="senden_pushed"`: URL prezent în pending → rămâne
  `senden_pushed`; absent → `processed`. Întoarce câte s-au schimbat.
  Eroare HTTP → excepție prinsă de view (mesaj, nimic modificat).

### 2. Views noi / modificate

- `job_detail` → devine și **formular de editare** (ModelForm cu: title,
  company, location, email_hr, contact_person, language, match_score,
  notizen). POST salvează local + mesaj. Descrierea și raw_json rămân afișate
  read-only.
- `push_job` (POST) — „Salvează în Sheet": `push_to_sheet(job, senden=False)`.
  Succes → mesaj; NU schimbă `send_state` (e doar sincronizare de date).
- `send_job` (GET+POST) — GET: pagina de confirmare cu tabelul exact al
  câmpurilor care pleacă (rezultatul `job_to_internal`, chei germane prin
  `job_to_sheet_row`) + modul poll (`poll_mode()`) + avertisment că emailul
  pleacă la următorul tick. POST: `push_to_sheet(job, senden=True)` → succes:
  `send_state="senden_pushed"`, `sheet_pushed_at=now` + mesaj.
- `refresh_states_view` (POST) — buton global pe index; apelează
  `refresh_states`, mesaj cu numărul actualizat.

### 3. UI

- Index: coloană nouă „Trimitere" (badge `draft`/`senden_pushed`/`processed`),
  buton global „Actualizează starea".
- Detaliu: formular editabil + butoanele „Salvează local", „Salvează în
  Sheet", „Trimite (SENDEN)" (ultimul duce la confirmare).
- Confirmare: tabel cu rândul exact (chei germane), banner cu modul poll
  (`LIVE` roșu / `dry-run` galben), butoane „Confirmă trimiterea" / „Înapoi".
- Starea `processed` afișată onest: „procesat de poll (verifică Email Status
  în Sheet)" + link spre Sheet.

## Fluxul complet

```
completezi câmpurile (detaliu, POST)
  → „Trimite (SENDEN)" → pagina de confirmare (rândul exact + mod poll)
    → Confirmă → N8nClient.ingest([{...,"sende_befehl":"SENDEN"}])
      → n8n upsert în Sheet (match pe Apply URL) + col P = SENDEN
        → poll-ul launchd (la ~10 min): Anschreiben → email (dry-run/LIVE)
          → Email Status=GESENDET, col P golită
  → „Actualizează starea" → get_pending(): URL absent → processed
```

## Tratarea erorilor (nimic silențios)

| Situație | Comportament |
|---|---|
| `N8N_WEBHOOK_SECRET` lipsă | mesaj eroare pe pagină, fără apel HTTP |
| `n8n.enabled: false` | mesaj „n8n dezactivat în settings.yaml", fără push |
| n8n HTTP eșuat (după retry-ul din client) | mesaj cu motivul; `send_state` neschimbat |
| Job fără URL | nu are butoane Sheet (ingest l-ar arunca oricum) |
| plist absent / necititbil | modul poll = „necunoscut (dry-run implicit)" |
| pending HTTP eșuat la refresh | mesaj eroare; nicio stare modificată |

## Ce NU face (limite conștiente)

- NU scrie col M „Status" (Apps Script o deține).
- NU citește autoritativ Email Status/GESENDET din Sheet (nu există workflow
  de read arbitrar); starea `processed` e deducție din pending, etichetată ca
  atare.
- NU trimite email direct (fără SMTP în dashboard).
- NU golește col P (treaba poll-ului, doar după email chiar trimis).
- NU împinge `description` (nu există coloană mapată; ar crea coloană fantomă).

## Testare

Mock-uri pe `N8nClient` (în `sheet_bridge`) — niciun test nu lovește rețeaua:

- `job_to_internal`: mapare corectă; omite goluri; fără `status`/`description`;
  `sende_befehl` doar la cerere; `gefunden_am` din `created_at`.
- `push_to_sheet`: secret lipsă → `{ok: False}` fără HTTP; `enabled: false` →
  la fel; ingest OK → `{ok: True}`.
- `send_job` view: GET arată confirmarea; POST cu succes → `send_state`
  actualizat; POST cu eroare → mesaj + stare neschimbată.
- `push_job` view: nu modifică `send_state`.
- `refresh_states`: prezent în pending → neschimbat; absent → `processed`;
  doar rândurile `senden_pushed` sunt atinse.
- Formularul de editare salvează câmpurile noi.
- `poll_mode`: plist cu `--live` → LIVE; fără → dry-run; absent → necunoscut.

Suita pipeline existentă rămâne neatinsă.

## Configurare

Rulare cu secretele încărcate (la fel ca pipeline-ul):
```bash
set -a; source .env; set +a   # ANTHROPIC_API_KEY + N8N_WEBHOOK_SECRET
python dashboard/manage.py migrate
python dashboard/manage.py runserver
```
