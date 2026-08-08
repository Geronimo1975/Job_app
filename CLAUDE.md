    

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

python dashboard/manage.py runserver   # Dashboard Django (SQLite local, dashboard/db.sqlite3)
```

## Teste

```bash
python -m pytest -q                              # Toată suita (tests/, configurat în pytest.ini)
python -m pytest tests/test_n8n_client.py -q     # Un fișier
python -m pytest tests/test_analyzer_agent.py::TestAnalyzerAgent::test_detect_language_german

python dashboard/manage.py test jobs             # Testele dashboard-ului (Django runner, NU sunt în suita pytest)
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

**Piețe, nu liste plate.** `scraping.markets` din `settings.yaml` definește 10 piețe (JOB-50), fiecare cu țară, `keyword_groups` (aceleași 4 grupuri, partajate via YAML anchor `&skills_groups`/`*skills_groups`) și surse proprii. Structura urmează cererea userului — „toate remote, ca un job de tip nomad", cu hibrid acceptat în 9 locații:

- **`remote_global`** — nucleul nomad: `country: worldwide`, `remote: true`, **`jobspy_sites: ["linkedin"]`**. Indeed NU acoperă asta: pe domeniul `www` întoarce 0 rezultate (măsurat 2026-08-07), deci fără override piața ar face 4 apeluri inutile per rulare. LinkedIn dă exact profilul căutat, dar zero adrese de email.
- **9 piețe de țară** unde e acceptat și hibridul: `de`, `at` (ambele `language: de`), `ch`, `nl`, `se`, `no`, `ro`, `saudi`, `uae`. Toate au **`remote: false`**, ceea ce NU înseamnă „doar on-site": înseamnă că filtrul remote nu se aplică, deci vin ȘI anunțurile remote, ȘI cele hibride. Aici sunt și adresele de email.

⚠ **Cheia `no` (Norvegia) TREBUIE citată în YAML**: YAML 1.1 citește `no`/`yes`/`on`/`off` ca booleeni, iar piața ajungea înregistrată sub cheia `False`.

`Market.jobspy_sites` (gol = lista globală `scraping.jobspy_sites`) există tocmai pentru că nu orice site acoperă orice piață. `market.language` ajunge în col „Sprache" și comandă limba scrisorii — de aceea `de`/`at` primesc scrisori în germană, restul în engleză. Codul primește un `scrapers.base.Market` și nu știe nimic despre țări — o piață nouă se adaugă în YAML, fără cod nou. `market.language` ajunge în col „Sprache" și comandă limba scrisorii. Bucla veche `keywords × locations` (49 de apeluri, majoritatea pe rate-limit) a fost înlocuită cu `keyword_groups × site-uri` (~10 apeluri jobspy, interogări booleene, paralel, max 4 workeri). `CareerPageScraper` e pre-piețe și rămâne cu semnătura veche (joburile lui n-au câmpul `market` — primesc reuniunea keyword-urilor la scoring).

**Poarta dintre scrape și analyze.** `main.apply_prefilter_gate()` rulează `store/seen.py` (index de dedupe cu memorie între rulări, `data/seen_jobs.json`, seed din Sheet prin workflow-ul read-only `JobApp Urls`) și `filters/relevance.py` (scor determinist 0-100 din `scraping.filters` + **bonus pentru keyword-urile de căutare ale pieței** găsite în titlu). Scoringul combină `negative_keywords` (penalizare -60 pe titlu, pentru roluri greșite ca „sales"/„recruiter"), `flex_signals` (bonus part-time/freelance — „contract" și „remote" au fost scoase din listă fiindcă toate piețele au `remote: true` și transformau bonusul într-un ofset constant, vezi JOB-47) și bonusul de keyword al pieței (+15/keyword, plafonat). Pragul e `min_prescore: 30`. Doar joburile peste prag ajung la AI — pe date reale poarta respinge local ~88%. Lotul reținut se persistă în `data/jobs_filtered.json`, pe care `--step analyze` standalone îl preferă fișierului brut (altfel ar ocoli poarta). Ștergerea intrărilor `respins_prefiltru` din `seen_jobs.json` forțează re-evaluarea după o schimbare de scoring; intrările `aplicat` nu se șterg niciodată.

**Emailul e o condiție, nu un câmp opțional (JOB-49).** `scraping.filters.require_email: true` face poarta să respingă joburile fără `email_hr` — fără destinatar, `EmailAgent` doar loghează „lipsă email" și sare rândul, iar jobul ar ocupa un rând în Sheet fără să poată fi trimis vreodată. Statistica îl numără separat (`fara_email`), ca să nu se confunde cu „sub prag"; ordinea contează — scorul se evaluează primul. Flagul e opt-in, ca o config existentă să nu-și golească poarta la update.

**Emailul NU mai e filtru — col AA „Bewerbungsart" spune ruta (JOB-50).** `require_email` s-a dovedit auto-contradictoriu față de ce caută userul: joburile remote/nomad se aplică prin portal ATS și n-au adresă (LinkedIn: **0%**), pe când adresele vin din anunțurile locale care scriu „Bewerbung an …" (DE 17%, AT 13%, SE 10%, NL 10%) — deci cerând emailul, Sheet-ul rămânea fix fără joburile remote. Acum `require_email: false`, iar `job_to_sheet_row()` completează col AA cu `Email` (există adresă) sau `Portal` (nu există → se aplică din „Apply URL"). Marcajul se emite **doar când payload-ul chiar poartă cheia `email_hr`**: comanda din `JobApp Pending` conține un subset de câmpuri, iar a scrie „Portal" pe baza unei chei absente ar șterge marcajul corect al unui rând trimisibil. Mecanismul `require_email` rămâne implementat și testat — pus pe `true` (global sau per piață), poarta respinge din nou joburile fără adresă.

⚠ **Col AA are DOUĂ vocabulare, de la două sisteme.** Serviciul FastAPI de pe VPS (`/opt/job-webhook/`, NU e în acest repo — Apps Script-ul nu atinge coloana) scrie `auto` în „Bewerbungsart"; acest pipeline scrie `Email`/`Portal`. Verificat live 2026-08-07: 26 de rânduri `auto` (toate cu `Quelle: indeed`, adică din producție) lângă rândurile pipeline-ului. Cele două nu se ciocnesc atâta timp cât nu găsesc ACELAȘI job — dar upsert-ul face match pe „Apply URL", deci un job găsit de ambele ar primi valoarea celui care scrie ultimul. Înainte de a schimba semantica coloanei, vezi ce face serviciul de pe VPS cu `auto`.

**Regula de email e PER PIAȚĂ, nu globală.** `filters.requires_email(job, config)` citește `scraping.markets.<piață>.require_email` și cade pe `scraping.filters.require_email` doar dacă piața nu o suprascrie (joburile fără piață cunoscută — career pages — urmează tot regula globală). Motivul e empiric: emailul nu există pe toate piețele, deci o regulă globală golește exact piețele care nu-l au. `ro_remote` e scutită explicit (`require_email: false`) — anunțurile din România se aplică prin platformă, iar eJobs dă un email la 106 anunțuri. Concret: cu regulă globală poarta reținea 9 joburi, toate germane; cu regulă per piață reține 22, din care 13 românești. Poarta evaluează regula PER JOB, nu o dată pentru tot lotul.

**Emailul depinde de piață, nu de scraper.** Singura sursă de `email_hr` e coloana `emails` a jobspy, populată prin parsarea DESCRIERII. Consecințe măsurate live (2026-08-06, aceleași keyword-uri): Indeed DE **17%** și Indeed NL **10%** cu adrese reale de candidatură (`amelie@ecoza.de`, `torecruiting@allianzgi.com`, `kevin.zandbergen@haskoning.com`); RO/UAE/SA/CH dau 3-17%, dar aproape numai clauze legale (`privacy@`, `reasonable.accommodations@`, `paytransparency@`) și gmail-uri personale. LinkedIn e o fundătură: 3%, artefacte de parsare (`_mb@oracle.com`) — `linkedin_fetch_description: true` NU o repară și nu ajută nici scorul (măsurat: media urcă 2,8→9,4, dar numărul care trece pragul rămâne identic, fiindcă descrierea dă +8/+3 iar pragul cere potrivire în TITLU). Motivul e structural: anunțurile DE/NL scriu „Bewerbung an …", cele remote/EN trimit la un portal ATS. De aceea piețele `de` (fără restricție remote — userul e în Germania, iar filtrul ar tăia ~80% exact unde sunt adresele) și `nl` (remote, nu e commutabil) sunt singurele care alimentează fluxul de trimitere; celelalte 4 rămân listă de aplicare manuală prin „Apply URL".

**Nu orice adresă e o adresă de candidatură.** `_first_email()` NU mai întoarce prima adresă plauzibilă: ordinea din anunț nu spune nimic despre destinatar, iar clauzele legale apar de regulă ÎNAINTEA adresei de recrutare. `_email_rank()` clasifică în trei: `_RANK_APPLICATION` (`bewerbung`, `recruit`, `career`, `talent`, `stellen`… — plus token-ii scurți `hr`/`jobs`/`apply`, care pe subșir ar da fals pozitiv în „chris@"), `_RANK_NEUTRAL`, `_RANK_AVOID` (`privacy`, `accommodation`, `paytransparency`, `press`, `legal`, `noreply`…). Adresele `_RANK_AVOID` nu se folosesc DELOC — jobul trebuie să arate ca unul fără adresă, ca `require_email` să-l respingă. O candidatură trimisă la `privacy@` e mai rea decât lipsa adresei.

**Căutare din dashboard.** `SearchQuery` (`scrapers/base.py`) e unitatea de lucru a scraperului; `Market.to_query(scrape_config)` o produce din YAML, pentru calea CLI. `JobSpyScraper.scrape_query(q)` e SINGURUL loc care apelează `scrape_jobs()` — `scrape_market(market)` e doar un wrapper peste el (`scrape_query(market.to_query(...))`). Dashboard-ul (`dashboard/jobs/search_bridge.py`) rulează căutări ad-hoc într-un thread `daemon`, cu staging în `SearchRun`/`SearchResult` și import selectiv în `Job` (fără apel AI — JobSpy livrează deja structura). `import_results()` nu suprascrie niciodată un `Job` care ține un pachet — predicatul e `search_bridge._holds_package()`: `send_state` în `LOCKED_STATES = ("generated", "sending", "sent")` **SAU** `error` cu `generated_at` completat. `error` e condiționat, nu în tuplu: un `error` cu pachet ține editările din Preview ȘI calea de recuperare (`views.recover_job` cere `send_state == "error"`, deci resetarea la `draft` ar lăsa doar re-`Execută`, care cheltuie un apel AI și suprascrie chiar acele editări), pe când un `error` fără `generated_at` (generarea a eșuat) n-are ce pierde și rămâne reîmprospătabil. Rândul sărit e doar legat (`imported_job`) și marcat `import_skipped=True` — flagul e persistat, nu dedus din starea jobului la randare (un job chiar importat ajunge `sent` pe calea normală și ar apărea retroactiv drept „sărit"); din el se aleg badge-ul din tabel și `imported_count`, ca să nu contrazică mesajul. La UPDATE, importul nu golește niciodată un câmp completat manual (`_PRESERVE_IF_BLANK`) — `update_or_create(defaults=)` se aplică și la update. Indexul de dedupe e citit read-only din dashboard: `data/seen_jobs.json` aparține pipeline-ului CLI.

**Generare + trimitere din dashboard.** `dashboard/dashboard/settings.py` inserează rădăcina repo-ului în `sys.path`, deci dashboard-ul importă direct modulele pipeline (read-only). `dashboard/jobs/generation_bridge.py`: „Execută" produce pachetul (email + Anschreiben + PDF-uri) refolosind `generators/`, „Transmite" trimite prin `SmtpSender` — același rate-limit pe disc partajat cu `--step poll`; eșecurile sunt codate în `{ok, error}`, niciodată excepții spre views. `dashboard/jobs/sheet_bridge.py` face upsert în Sheet prin n8n și marchează `GESENDET`, dar NU emite niciodată col M „Status" și nici col P „Sende Befehl" — fluxul `SENDEN` (col P) a fost înlocuit de trimiterea directă din dashboard.

**Filtre incompatibile per site.** Upstream: pe Indeed poți folosi DOAR unul din `hours_old` / `(job_type & is_remote)` / `easy_apply`. `_sanitize()` renunță la filtrul mai puțin valoros și raportează asta cu prefix `[avertisment]`; `ScraperAgent` exclude avertismentele din `blocking_errors`, deci o degradare nu coboară statusul la PARTIAL. **Ordinea din `_sanitize()` contează**: `is_remote` se evaluează PRIMUL, fiindcă renunțarea la el poate face `hours_old` folosibil din nou.

**`is_remote` anulează `search_term` pe Indeed (JOB-48).** Nu e doar un conflict de filtre — cu `is_remote=True`, Indeed **ignoră complet** termenul de căutare. Verificat live 2026-08-06 pe UAE: aceeași interogare booleană întorcea „Senior Real Estate Broker" / „Nursery Admin Manager" pentru „AI Developer", și **identic aceleași 50 de rânduri pentru toate cele 4 grupuri de keywords** (fiecare URL ×4) — semnătura clară a unui termen ignorat. Efect măsurat: din 400 de joburi Indeed, 0 treceau prefiltrul. Fără `is_remote`: „AI Engineer / Agentic AI Developer", „AI Automation Engineer". De aceea `_sanitize()` scoate `is_remote` pe Indeed când există `search_term`, iar filtrul remote se aplică **local**, în `_call()`, prin `_is_remote_row()` (flagul `is_remote` întors de jobspy SAU „remote" ca cuvânt întreg în titlu/locație — Indeed nu setează mereu flagul). Condiția de filtrare locală se citește din `kwargs` (`"is_remote" not in kwargs`), nu dintr-un flag separat: pe LinkedIn filtrul a ajuns la upstream, iar o a doua filtrare ar tăia rândurile pe un câmp pe care LinkedIn nu-l completează.

**NaN pandas ≠ gol.** `_text()` (`scrapers/jobspy_scraper.py`) verifică `NaN` explicit (`value != value`) înainte de `str()` — `str(value or "")` NU-l prinde, fiindcă `NaN` e truthy, și producea literal `"nan"` în descrieri/companii, mai departe în promptul AI și în Sheet. Orice normalizare nouă a unui rând jobspy trebuie să treacă prin `_text()`/`_first_email()`, nu prin `str(x or "")` direct.

**Surse best-effort.** `market.best_effort_sources` listează surse SAU sub-site-uri al căror eșec NU coboară statusul (dar se raportează în `errors`): `linkedin` (site jobspy, rate-limit agresiv — potrivit pe eticheta `[piață/site]` din mesajul de eroare, nu pe numele sursei) și `ro_boards` (eJobs, scraping HTML fără API). `ScraperAgent` dă `PARTIAL` doar pe erori blocante și `FAILED` doar la zero joburi + erori blocante. **eJobs: `ItemList`, nu carduri (JOB-49).** Doar ~3 carduri per căutare sunt randate server-side; restul sunt placeholdere `job-card-wrapper__temp` (titlu + firmă în două `<strong>`, fără link) — atenție, clasa `__temp` e pe **div-ul interior**, nu pe `li`. Nota veche („datele complete există în `window.__NUXT__`") era un diagnostic greșit: `window.__NUXT__` ține doar config, iar payload-ul Nuxt 3 (`<script id="__NUXT_DATA__">`) conține filtre/orașe, nu anunțuri. Toate cele ~40 de anunțuri ale paginii sunt în blocul JSON-LD **`ItemList`** (nume + URL), inclusiv în fixture-ul salvat din iulie. `_parse_ejobs()` citește de acolo, folosește cardurile doar pentru firmă/locație și cade înapoi pe parsarea din carduri dacă `ItemList` dispare. Măsurat live 2026-08-06: **11 → 106 joburi** per rulare (~10×), în 8s. Descrierea și emailul NU sunt în listă — `_enrich_from_detail()` cere pagina fiecărui anunț (JSON-LD `JobPosting`: `description`, `hiringOrganization`, `jobLocation`, `datePosted`). Fără acest pas, joburile eJobs ajungeau la prescore doar cu titlul și aveau `email_hr` mereu gol — deci cu `require_email: true` niciunul n-ar fi putut trece poarta.

**eJobs limitează agresiv paginile de detaliu — și 429 arăta ca succes.** Măsurat 2026-08-06: cu 4 workeri, **81 din 106** cereri au primit HTTP 429. Fiindcă `_enrich_from_detail()` nu apela `raise_for_status()`, un 429 era indistinct de „pagină fără `JobPosting`" și se înghițea tăcut — scraperul raporta „0 erori" cu 76% din îmbogățire pierdută. Acum funcția întoarce un status (`ok`/`rate_limited`/`unavailable`), `_enrich_all()` numără, **se oprește după `_RATE_LIMIT_GIVE_UP = 8` răspunsuri 429** (odată limita activă, restul eșuează oricum și insistența prelungește blocarea) și raportează câte anunțuri au rămas neîmbogățite, cu prefix `[avertisment]`. Workerii au coborât la 2. Chiar și secvențial cu 0,4s pauză, 19 din 25 de cereri primeau 429 după ce limita se activase — deci îmbogățirea completă a lotului NU e realizabilă într-o rulare. Consecință practică: randamentul de email pe eJobs e ~1%, iar sub `require_email: true` eJobs contribuie efectiv 0 rânduri în Sheet — anunțurile din România se aplică prin platformă, nu pe email. eJobs rămâne util ca listă de aplicare manuală.

**Upsert-ul golește ce nu trimiți.** Nodul Google Sheets din `JobApp Ingest` rulează `appendOrUpdate` + `autoMapInputData`, cu `Apply URL` drept `matchingColumns`. O cheie **absentă** din payload e ignorată, dar o cheie cu `""` **șterge** celula din rândul găsit. De aceea `job_to_sheet_row()` emite doar câmpurile cu valoare, iar `N8nClient.ingest()` aruncă rândurile fără `Apply URL` (fără cheie de match, n8n cade pe append și adaugă rânduri goale).

**Col M vs col P — cine scrie ce.** Col M „Status" aparține mașinii de stări a Apps Script (`plan→send→running→done/error`); `--step poll` NU o atinge, iar valorile străine (ex. `new` de la scrapere) sunt filtrate în `n8n_mapping`. Col P „Sende Befehl" e trigger-ul scris de om — poll-ul o golește **doar** după un email chiar trimis, altfel același rând s-ar reprocesa la fiecare tick. Col R „Email Status" are două forme de „trimis", pentru că o scriu două sisteme: `sent` (Apps Script→FastAPI) și `GESENDET` (`--step poll`); ambele filtre le recunosc pe amândouă.

**Generare text.** `cover_letter_gen.py` (Anschreiben AI, system prompt DE/EN, injectează `matching_skills`+cerințe via `special_instructions`) și `email_body_gen.py` (body din `config/email_templates/`, personalizat din `analysis`, salut german pe gen). Ambele cad pe template/fallback la eroare AI — `cover_letter_gen` avertizează pe stderr ca să nu fie silențios.

**PDF.** `generators/pdf_gen.py` (`text_to_pdf` → `merge_pdfs` → `assemble_bewerbung`) e apelat din `CoverLetterAgent`, nu din `main.py`: pasul `generate` produce și Anschreiben PDF + „Bewerbung Komplett" (Anschreiben + CV + certificate). `_latin1_safe()` transliterează textul — FPDF pe font core nu suportă UTF-8.

**Rate limit email persistat pe disc.** `sender/smtp_sender.py` ține contorul zilnic în `output/logs/email_rate_state.json`, protejat de `email_rate_state.lock` (`flock`), tocmai pentru că `--step poll` rulează ca proces separat la fiecare tick. Limita vine din `email.max_emails_per_day` (implicit 10) + `delay_between_emails_sec`. Ștergerea fișierului de stare resetează contorul.

### Două sisteme de trigger pe ACELAȘI Sheet (atenție — se pot bate)

|          | Sistem A (Apps Script, producție)                                                                                             | Sistem B (acest repo)                                               |
| -------- | ------------------------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------- |
| Trigger  | scrii`send` în **coloana M „Status"**                                                                                | `--step poll`: `SENDEN` în **coloana P „Sende Befehl"** |
| Mecanism | Apps Script`onEdit` → POST webhook                                                                                          | Python interoghează n8n (poll)                                     |
| Backend  | **`job.n8n-studio.com/trigger`** — serviciul FastAPI de pe VPS, NU n8n (vechiul `jobs.get-hub.io` e **mort**) | n8n`JobApp Pending`                                               |
| Stare    | mașină de stări col M:`plan→send→running→done/error`; erori logate în col W „Notizen"                                | `Email Status=GESENDET`                                           |

Apps Script-ul (bound la Sheet, sursă trackuită la `scripts/jobapp_appscript.gs`) e front-end-ul producției: declanșează și pe URL paste în col E (scrape) și pe col AB (Danke). La eșec scrie `error` în M + motivul în col W.

### n8n — detalii critice

- **Instanța proiectului = `app.n8n-studio.com`** (config `n8n.base_url`). Acces programatic prin **API public cu `N8N_API_KEY`** din `.env` (vezi `scripts/deploy_n8n_workflows.py`).
- **Tool-urile MCP n8n (`mcp__n8n-mcp__*`, `mcp__claude_ai_n8n__*`) sunt conectate la ALTĂ instanță** (`n8n.rotocon.world`) — NU folosi MCP pentru workflow-urile acestui proiect; folosește API-ul public.
- **`$env` e blocat în nodurile Code** pe această instanță → pentru secrete pe webhook folosește credential nativ **Header Auth** (`X-JobApp-Secret`), nu `$env`.
- Workflow-uri active: `JobApp Ingest`/`Pending` (upsert/read Sheet), `JobApp Upload` (PDF → Google Drive cu link public read-only; NU mai scrie col O direct — linkul întors intră în col O prin upsert-ul Ingest pe cheia `cover_letter`; apelat din dashboard la „Salvează în Sheet" via `sheet_bridge.upload_anschreiben`, idempotent pe `Job.anschreiben_drive_link`), `JobApp Urls` (read-only: întoarce doar `Apply URL`/`Status`/`Email Status` pentru tot tab-ul — alimentează indexul de dedupe la `--step scrape`; proiecția pe 3 coloane + range `A1:AB` sunt intenționate, tab-ul are ~1140 coloane fantomă `col_29…col_1167` pe care o citire completă le-ar transfera pe toate). `JobApp Trigger` e un stub ping/pong **neconectat** — NU e endpoint-ul Apps Script (acela e `job.n8n-studio.com/trigger`, alt serviciu, vezi mai jos); păstrat activ + în git (`n8n_workflows/jobapp_trigger.json`) pentru istoric/eventuală reutilizare. Sheet real: doc `1fTjvs6...`, tab `Bewerbungen` (28 coloane), Google Sheets OAuth credential în n8n.
- **Autentificarea Google în n8n = OAuth (din 2026-08-02), nu service account.** Credentialul `googleSheetsOAuth2Api` „Google Sheets account1" (`lwLv81Aaab8h0Zm3`, contul personal al utilizatorului) e folosit de TOATE nodurile Google: Sheets cu `authentication: "oAuth2"`, HTTP Request cu `nodeCredentialType: "googleSheetsOAuth2Api"` (scope-ul include `drive.file`, suficient și pentru upload-ul Drive). Istoric: se folosise un service account pentru că OAuth-urile expirau, dar Google a interzis stocarea Drive pentru service accounts („Service Accounts do not have storage quota"), iar credentialul SA a fost șters. ⚠️ La un viitor 500 pe `jobapp-*`, primul suspect e OAuth-ul expirat → Reconnect în UI-ul n8n; nodurile Sheets trebuie să aibă `authentication` setat EXPLICIT (`oAuth2`) — lăsat gol, nodul cade pe default-ul service account și dă „Credential … does not exist".
- Pentru apeluri Google Sheets API arbitrare (citire/ștergere/validări) când nu există un workflow: creează un workflow temporar Webhook→HTTP Request (cu credentialul OAuth de mai sus)→Respond, execută, apoi șterge-l.
- **`*.get-hub.io` NU mai e valabil** (instanța veche pe `2.57.91.92`, jos). Nu mai folosi `jobs.get-hub.io` / `n8n.get-hub.io` ca backend. Job Tracker Webhook (uvicorn :8765 pe VPS `85.215.69.83`) e acum expus pe **`https://job.n8n-studio.com`** — ruta Traefik `Host(\`job.n8n-studio.com\`)`în`/opt/gethub/traefik/dynamic/jobs.yml`(TLS Let's Encrypt OK;`/health`și`/docs`→ 200). Toate domeniile active ale proiectului sunt pe`*.n8n-studio.com`(VPS`85.215.69.83`).

### Serviciul de producție de pe VPS (NU e în git)

Calea care chiar trimite aplicații (Sistem A) rulează pe VPS `85.215.69.83`, în afara acestui repo:

| cale                                             | rol                                                                                                  |
| ------------------------------------------------ | ---------------------------------------------------------------------------------------------------- |
| `/opt/job-webhook/webhook/server.py`           | FastAPI:`/trigger`, `/scrape`, `/auto-fix`, `/digest`, `/health`                           |
| `/opt/job-webhook/tracker/sheets_tracker.py`   | acces Sheet prin**service account** `config/get-hub-489416-*.json` (legat pe numele tabului) |
| `/opt/job-webhook/sender/send_from_tracker.py` | trimitere SMTP                                                                                       |
| `/etc/systemd/system/job-webhook.service`      | uvicorn pe`172.29.0.1:8765`, `EnvironmentFile=/opt/job-webhook/.env`                             |
| `/etc/systemd/system/job-scrape.timer`         | scrape la 07:00 și 18:00 Europe/Berlin                                                              |
| `crontab -l` → `*/15 * * * * … /auto-fix`  | reîncearcă rândurile`error`                                                                     |

⚠️ **CV-ul trimis efectiv stă pe VPS, nu în acest repo.** Toate trimiterile reale trec pe aici (`Email Status: sent`, minusculă — `--step poll` din repo ar scrie `GESENDET`). Semn diagnostic decisiv: dacă `output/logs/email_rate_state.json` NU există local, prin repo n-a plecat niciodată vreun email, oricâte rânduri ar arăta „trimis" în Sheet. `sender/send_from_tracker.py::get_default_attachments(language)` alege CV-ul pe limbă, dar cu **căi hardcodate** (nu citește `documents.cv_file` din configul lui): `Resume George Sebastian Cucuiet.pdf` (EN) / `Bewerbung George Sebastian Cucuiet.pdf` (DE), în `/opt/job-webhook/documents/cv/`. Actualizarea CV-ului = **înlocuirea acelor două fișiere, cu numele păstrate** (scp; fără restart — sunt citite la fiecare trimitere). Făcut 2026-08-08, cu backup `*.bak-20260808`. Schimbarea CV-ului doar în repo NU are niciun efect asupra candidaturilor reale.

Serviciul are **propriul `.env`** (`WEBHOOK_SECRET`, `GMX_PASSWORD`, `ANTHROPIC_API_KEY`) și **propriul `config/settings.yaml`** — schimbarea modelului AI sau a cheii în repo NU se propagă acolo. `server.py` nu apelează `load_dotenv`: variabilele intră doar prin `EnvironmentFile`, deci după orice editare a `.env` e nevoie de `systemctl restart job-webhook`.

⚠️ Cele două sisteme de trigger scriu în același Sheet cu chei diferite. Vezi „Col M vs col P" mai sus înainte de a atinge orice logică de status.

### Scheduling & scripturi

- `scripts/com.jobapp.poll.plist` — job launchd (macOS) care rulează periodic `--step poll`.
- `scripts/deploy_n8n_workflows.py` — push/pull workflow-uri din `n8n_workflows/*.json` prin API-ul public n8n (`N8N_API_KEY`).
- `scripts/test_e2e_manual.py` — E2E manual (lovește servicii reale, NU face parte din suita pytest).
- `scripts/jobapp_appscript.gs` — copie trackuită a Apps Script-ului bound la Sheet; editarea aici nu îl deployează, trebuie copiat manual în editorul Apps Script.

### Documente

**CV-ul se alege după limba jobului (JOB-51).** Profilul are două CV-uri echivalente: `Lebenslauf_George_Sebastian_Cucuiet.pdf` (DE) și `Resume_George_Sebastian_Cucuiet.pdf` (EN). `documents.cv_files.<limbă>` bate `documents.cv_file` (rezerva pentru limbile neconfigurate, deci o config veche fără `cv_files` merge neschimbată). Rezolvarea e în `agents/base_agent.cv_path_for(config, language)` + `job_language(job)` — analiza AI → `market.language` (col „Sprache") → `de`. Treapta din mijloc lipsea: fără analiză, un job de pe piață engleză primea email ȘI CV în germană. Folosit în `EmailAgent._collect_attachments`, `CoverLetterAgent` (unde „Bewerbung Komplett" lipește scrisoarea de CV, deci nepotrivirea de limbă e vizibilă direct), `DocumentAgent._prepare_package` și `dashboard/jobs/generation_bridge._docs_paths`. `DocumentAgent._verify_documents` NU are job, deci verifică **toate** variantele configurate — altfel lipsa Lebenslauf-ului ar ieși la iveală abia la prima trimitere pe DE.

⚠ **„Bewerbung Komplett" e un CACHE, nu un link — poate trimite CV-ul vechi.** PDF-ul combinat are CV-ul și certificatele **lipite înăuntru la generare**, iar `_collect_attachments` îl preferă și îl trimite SINGUR. Un pachet generat înainte de schimbarea CV-ului continuă deci să trimită CV-ul vechi, tăcut (întâmplat 2026-08-08: pachetul din 23 iulie a plecat cu CV-ul vechi deși configul arăta spre cel nou). Două apărări, ambele necesare:

1. `agents/base_agent.package_is_stale(package, *sources)` — pachetul se folosește DOAR dacă e mai nou decât CV-ul și certificatele lui; altfel se cade pe Anschreiben + CV curent (cu `[avertisment]` în log). Aplicat în `EmailAgent._collect_attachments` ȘI în `dashboard/jobs/generation_bridge._collect_attachments`.
2. `send_package()` **recalculează** lista de atașamente la trimitere și o persistă. Fără asta, reparația de la (1) n-ar fi ajuns nicăieri: trimiterea folosea `Job.attachments_json`, înghețat la generare. Garda LIVE se leagă acum de existența **scrisorii** (`bewerbung_pdf_path` sau `anschreiben_pdf_path`), nu de „lista e nevidă" — recalcularea găsește mereu CV-ul, deci o verificare pe lungime ar lăsa să plece un pachet fără Anschreiben.

⚠ **CV-urile trebuie să aibă STRAT DE TEXT.** Variantele primite inițial erau o singură imagine per pagină (1333×2000 px, zero fonturi): niciun ATS nu putea extrage nimic din ele, iar majoritatea rândurilor din Sheet se aplică prin portal (`Bewerbungsart: Portal`). Cele în uz au fost regenerate din sursele HTML ale bundle-ului de design (Claude Design handoff, `*-canva-export.html`) prin Playwright `page.pdf()`, apoi cutia de pagină a fost fixată exact pe A4 (595,28×841,89 pt = 210×297 mm) cu pypdf, fiindcă Chrome rotunjea la 596×843. Rezultat: 2 pagini, ~5.100 (EN) / ~5.800 (DE) caractere extractibile. Chrome headless via CLI (`--print-to-pdf`) ATÂRNĂ în acest mediu chiar și pe un HTML trivial — folosește Playwright. La orice CV nou: verifică `PdfReader(f).pages[0]["/Resources"]["/Font"]` — dacă e gol, e imagine.

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
