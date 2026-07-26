# Căutare JobSpy în dashboard + închiderea gap-urilor upstream

Data: 2026-07-26

## Problema

`python-jobspy` (upstream: `speedyapply/JobSpy`) e deja în `requirements.txt` și e
folosit de `scrapers/jobspy_scraper.py`, dar:

1. **Dashboard-ul n-are căutare.** Singura intrare e „lipește un URL" →
   `extract_job()` (apel AI). Pipeline-ul CLI găsește joburi, dashboard-ul nu le
   vede niciodată. Cele două jumătăți ale sistemului nu se ating.
2. **Folosim o felie mică din upstream** (versiune instalată: 1.1.82):

   | Upstream oferă | Repo-ul folosește azi |
   |---|---|
   | 8 site-uri (`linkedin, indeed, glassdoor, google, zip_recruiter, bayt, naukri, bdjobs`) | 2 (`indeed`, `linkedin`) |
   | `linkedin_fetch_description` | ❌ — joburile LinkedIn ajung la AI **fără descriere** |
   | coloana `emails` | ❌ — deși `Job.email_hr` e obligatoriu pentru „Transmite" |
   | `google_search_term`, `job_type`, `distance`, `offset` | ❌ |

3. **Bug real:** upstream spune explicit că pe Indeed poți folosi **doar unul**
   din `hours_old` / `(job_type & is_remote)` / `easy_apply`. `_call()` trimite
   `hours_old=72` **și** `is_remote=True` pentru piața `ro_remote` — un filtru
   cade silențios.

## Decizii luate

| Întrebare | Decizie |
|---|---|
| Model de căutare în dashboard | **Hibrid**: preset din `settings.yaml`, toate câmpurile editabile |
| Flux rezultate | **Tabel de staging** (`SearchResult`) + import selectiv în `Job` |
| Prefiltru | **Da** — scor `filters/relevance.py` + marcaj „deja văzut", fără a ascunde nimic |
| Execuție | **Thread de fundal** + pagină care se auto-reîmprospătează |
| Scope CLI | Toate trei fix-urile: bug Indeed, descrieri LinkedIn + email HR, site-uri noi |

## Arhitectură

### Un singur punct de apel către `scrape_jobs()`

Alternative respinse:

- *Wrapper JobSpy separat în dashboard* — două locuri de reparat la fiecare bug
  upstream; bug-ul Indeed s-ar duplica imediat.
- *`Market` sintetic construit de dashboard* — `Market` e `frozen` și modelat pe
  YAML; nu poate exprima `job_type`/`distance`/site-uri per căutare.

Ales: `SearchQuery` devine unitatea de lucru a scraperului.

```python
# scrapers/base.py
@dataclass(frozen=True)
class SearchQuery:
    keyword_groups: list[list[str]]
    sites: list[str]
    location: str
    country: str                       # → country_indeed
    language: str                      # → col „Sprache" → limba Anschreiben
    market_key: str = "adhoc"
    remote: bool = False
    hours_old: int | None = 72
    results_wanted: int = 50
    job_type: str = ""
    distance: int | None = None
    google_search_term: str = ""
    linkedin_fetch_description: bool = False
```

- `Market.to_query(scrape_config) -> SearchQuery` — construiește interogarea din YAML.
- `JobSpyScraper.scrape_query(q) -> (jobs, errors)` — singurul loc care apelează
  `scrape_jobs()`.
- `JobSpyScraper.scrape_market(market)` devine wrapper de o linie peste
  `scrape_query(market.to_query(self.scrape_config))` — contractul extern
  `(jobs, errors)` rămâne neschimbat pentru `ScraperAgent`.

Consecință: `make_job()` primește `market_key: str` + `language: str` în loc de
`market: Market`. Jobul n-a avut niciodată nevoie de tot obiectul, doar de două
câmpuri. Call-site-uri afectate: `scrapers/remote_boards.py` (3),
`scrapers/ro_boards.py` (1), `scrapers/jobspy_scraper.py` (1).

### Sanitizarea parametrilor per site

`_call()` construiește kwargs, apoi elimină combinațiile interzise de upstream:

| Site | Regulă upstream | Precedență aleasă |
|---|---|---|
| `indeed` | doar unul din `hours_old` / `(job_type & is_remote)` / `easy_apply` | căutare remote → `is_remote` câștigă, `hours_old` se omite; altfel `hours_old` câștigă |
| `linkedin` | `hours_old` nu se combină cu `easy_apply` | `hours_old` câștigă (nu folosim `easy_apply`) |

Filtrul renunțat **nu dispare în silențiu**: intră în `errors` cu prefix
`[avertisment]`. `ScraperAgent` exclude aceste intrări din `blocking_errors`
(nu coboară statusul la PARTIAL) — același tipar ca `_is_best_effort_error`.
`search_bridge` împarte lista `errors` după acest prefix: intrările
`[avertisment]` ajung în `SearchRun.warnings_json`, restul în `errors_json`.

Parametrii opționali negoliți se **omit** din apel, nu se trimit goi:
`job_type=""`, `distance=None`, `google_search_term=""` nu ajung niciodată ca
kwargs la `scrape_jobs()` (un `job_type` gol ar fi interpretat ca filtru).

### Normalizare îmbogățită

`_normalize_job()` preia în plus:

- `emails` → `email_hr` (primul email valid) — exact câmpul fără care „Transmite" refuză.
- `is_remote`, `job_type`, `currency`, `interval`, `company_url` → `raw_json`.

## Model de date (dashboard)

```python
class SearchRun(models.Model):
    label = CharField                  # ex. "de · Python Developer +2 grupuri"
    params_json = JSONField            # parametrii exacți, ca să poți repeta căutarea
    status = CharField                 # running | done | error
    started_at / finished_at = DateTimeField
    found_count = IntegerField          # rezultate salvate (nimic nu se ascunde)
    imported_count = IntegerField       # câte au devenit rânduri Job
    warnings_json = JSONField(default=list)   # [avertisment] — afișate separat
    errors_json = JSONField(default=list)     # erori per sursă

class SearchResult(models.Model):
    run = FK(SearchRun, related_name="results", on_delete=CASCADE)
    url, title, company, location, platform, date_posted, language
    description = TextField
    email_hr = CharField
    salary_min / salary_max = IntegerField(null=True)
    prescore = IntegerField
    prescore_reasons_json = JSONField(default=list)
    seen_state = CharField              # "" | aplicat | respins_prefiltru
    imported_job = FK(Job, null=True, blank=True, on_delete=SET_NULL)
    raw_json = JSONField
    class Meta:
        unique_together = ("run", "url")
        ordering = ["-prescore"]
```

Unicitatea e **per run**, nu globală: același job poate apărea legitim în două
căutări din zile diferite, iar fiecare rulare trebuie să rămână un snapshot
fidel. Dublura între rulări se vede din `seen_state` + badge „importat", nu prin
ștergerea rândului.

## Execuție în fundal

`dashboard/jobs/search_bridge.py` — același tipar ca `sheet_bridge` /
`generation_bridge`: nicio excepție nu urcă spre views, doar `{ok, error}`.

1. POST → `SearchRun(status="running")` → `threading.Thread(daemon=True)` →
   redirect la `/cauta/<pk>/` (PRG, ca `add_job`).
2. Worker: `scrape_query()` → `prescore()` + adnotare `SeenIndex` → bulk-insert
   → `status=done`. Orice excepție → `status=error` + mesaj. La final
   `connections.close_all()` (thread + SQLite).
3. `SeenIndex` e folosit **read-only** — dashboard-ul nu scrie în
   `data/seen_jobs.json`, ca să nu se bată cu rularea CLI. Nici importul nu
   marchează `aplicat`: „aplicat" înseamnă email trimis, iar seed-ul din Sheet
   la `--step scrape` prinde oricum jobul.

**Gardă de concurență:** refuz o căutare nouă dacă alta e `running` — două
rulări simultane înseamnă 429 garantat pe LinkedIn.

**Rulări moarte:** un `running` mai vechi de 20 min e considerat întrerupt
(thread-ul `daemon` moare la restart de `runserver` și altfel ar bloca
căutările pe veci). UI: pastilă „întrerupt"; căutare nouă permisă.

## UI — secțiunea „Căutare"

Sidebar: `Joburi` (rămâne home `/`) · **`Căutare`** · `Trimise`.
CSS: doar **adaosuri** în `base.html` (`.pill.running`, `.pill.stale`,
`.bulkbar`, `.checkcol`, `details.adv`, `.chips`) — nicio regulă existentă
modificată. Formularul refolosește `.form-grid`/`.field`, tabelul
refolosește `.table-scroll`.

### `/cauta/` — formular + istoric

Progressive disclosure, ca să nu arunce 8 câmpuri în față:

- **Vizibil mereu:** `Preset` (`de`/`ro_remote`), `Cuvinte cheie` (textarea, *un
  grup pe linie*, helper text „cuvintele de pe aceeași linie se caută cu OR"),
  `Locație`, buton `Caută`.
- **`<details class="adv">` „Filtre avansate":** site-uri (8 checkbox-uri în
  `fieldset`+`legend`), `hours_old`, `results_wanted`, `remote`, `job_type`,
  `distance`, `google_search_term` (helper: sintaxa specială Google Jobs),
  `linkedin_fetch_description` (helper: „încetinește mult — un request per job").
- Presetul pre-completează câmpurile prin `json_script` + ~15 linii JS inline.
  **Fără JS formularul funcționează**: valorile presetului implicit vin randate
  din server.
- Dedesubt: „Rulări recente" — label, status, găsite/importate, durată, timestamp.

### `/cauta/<pk>/` — rulare + rezultate

URL propriu ⇒ deep-linkable și refresh-safe.

- Cap: label + pastilă status + parametrii ca chips `.meta`.
- Cât e `running`: banner `aria-live="polite"` („Caut pe 2 site-uri × 3
  grupuri…") + `<meta http-equiv="refresh" content="5">` **randat doar în starea
  running** (o pagină terminată nu se mai reîncarcă), plus link manual
  „Reîmprospătează" ca auto-refresh-ul să nu fie o surpriză.
- Avertismentele în banner portocaliu — ex. „Indeed: am renunțat la `hours_old`,
  `is_remote` are prioritate". Erorile per sursă în `<details>` „Erori pe surse (2)".
- Tabel: coloană checkbox (+ „selectează tot" în `th`), `Scor` (tabular, motivele
  în `title`), `Titlu` (link extern `rel="noopener"`), Companie, Locație,
  Platformă, `Email HR`, Vechime, `Stare` (`nou` / `văzut: aplicat` /
  `importat`). Sortare pe scor (implicit) sau dată, cu `aria-sort`.
- **Bară de acțiune în bloc** sub tabel: „N selectate · Importă selectate" —
  dezactivată la 0 selectate; fără JS butonul rămâne activ și serverul validează.
- Empty state: „Nicio potrivire. Încearcă mai puține cuvinte-cheie sau crește
  `hours_old`."

Accesibilitate: pastilele au text, nu doar culoare; fiecare câmp are `<label>`;
`fieldset`+`legend` pentru grupul de site-uri; `aria-sort` pe capetele
sortabile; `aria-live` pe banner-ul de progres.

## Import → `Job`

`Job.objects.update_or_create(url=...)` cu `extraction_status="ok"`,
`send_state="draft"`, `source="jobspy:<platform>"`.

**Fără apel AI** — JobSpy livrează deja structura, deci importul e instant și
gratis. `extract_job()` rămâne doar pentru URL-urile lipite manual.

Gardă: un `Job` care ține un pachet **nu se suprascrie niciodată** — ar
distruge un pachet în zbor sau arhiva unui email trimis. „Ține un pachet"
înseamnă `send_state` în `generated` / `sending` / `sent`, **sau** `error`
cu `generated_at` completat: acolo trăiesc editările din Preview și calea de
recuperare (`views.recover_job` cere `send_state == "error"`, deci o resetare
la `draft` ar lăsa ca singură ieșire re-`Execută` — un apel AI care suprascrie
exact acele editări). Un `error` **fără** `generated_at` (generarea însăși a
eșuat) n-are ce pierde și rămâne reîmprospătabil de un import nou.
Raport onest la final: „5 importate, 2 sărite (deja trimise), 3 fără email HR".

## Erori

- Erorile per site nu opresc rularea (contractul `(jobs, errors)` rămâne).
- 0 rezultate + erori → `status=error`; 0 rezultate fără erori → `status=done`
  + empty state.
- Avertismentele de filtre renunțate sunt vizibile, niciodată silențioase.

## Teste

Toate cu `scrape_jobs` mock-uit — zero rețea, ca restul suitei.

`tests/test_jobspy_scraper.py`:
- sanitizare per site: Indeed pierde `hours_old` când căutarea e remote;
  LinkedIn primește `linkedin_fetch_description`
- `emails` → `email_hr`
- `google_search_term` ajunge în apel
- **non-regresie CLI**: `Market.to_query()` produce exact parametrii de azi

`dashboard/jobs/tests.py`:
- validarea formularului (keyword_groups din textarea, site-uri necunoscute respinse)
- worker: cale OK și cale de eroare (`status=error` + mesaj)
- adnotare `prescore` + `seen_state`
- importul creează `Job` cu `send_state=draft`
- importul **refuză** să calce peste `sent` / `generated` / `sending`
- garda de concurență (a doua căutare e refuzată)
- rularea stale (>20 min `running`) nu blochează o căutare nouă

## În afara scope-ului

- Fără Celery/Redis — thread de fundal e suficient pentru un dashboard local.
- Fără push automat în Sheet la import (rămâne butonul manual „Salvează în Sheet").
- `offset` / `proxies` / `ca_cert` din upstream nu se expun în UI.
- `CareerPageScraper` rămâne neatins (e pre-piețe, semnătură veche).
