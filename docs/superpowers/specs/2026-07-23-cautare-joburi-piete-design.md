# Design — Revizia sistemului de căutare joburi (piețe DE + RO remote)

**Data:** 2026-07-23
**Stare:** aprobat, gata de plan de implementare
**Context:** JOB-34+

---

## 1. Problema

Sistemul actual de scraping e ineficient în patru feluri, toate măsurabile pe codul și datele din repo.

| Problemă | Dovadă |
|---|---|
| O singură sursă vie | `data/jobs_scraped.json`: 91 joburi, **100% `indeed`**. Glassdoor și Google returnează 0. |
| Randament slab | 7 keywords × 7 locații = **49 apeluri** `scrape_jobs`, `results_wanted=50` fiecare → potențial 2450, obținut 91 (~1.9/apel). |
| Erori invizibile | `scrapers/jobspy_scraper.py:66` prinde excepțiile și le scrie cu `print()`. Un run în care 40 din 49 de apeluri eșuează raportează `SUCCESS`. |
| Filtre = cod mort | `scraping.filters.exclude_keywords`, `min_salary`, `experience_max_years` din `config/settings.yaml` **nu sunt citite nicăieri** în cod. Roluri de Director/VP intră în pipeline și consumă tokeni AI. |
| Hardcodat pe Germania | `country_indeed="Germany"` (`jobspy_scraper.py:60`). |
| „Remote" tratat ca text | `locations` conține `"Remote"`, deși JobSpy are parametrul dedicat `is_remote`. |
| Fără memorie între rulări | `_is_duplicate` compară doar în JSON-ul local, O(n²), și nu știe nimic despre ce s-a aplicat deja (Sheet-ul). |

## 2. Obiective

1. Volum mai mare de joburi relevante, din surse care chiar funcționează.
2. Filtrare deterministă înaintea AI, ca să scadă costul.
3. Rulări mai rapide (paralel, mai puține apeluri).
4. Căutare remote în România, cu profil propriu — nu ca „încă o locație".
5. Fără muncă manuală până la momentul deciziei de trimitere.

**Non-obiectiv:** trimitere automată de emailuri. Decizia de a aplica rămâne umană (`send` în col M).

## 3. Ideea centrală: conceptul de piață (market)

Listele plate `search_keywords` × `locations` se înlocuiesc cu piețe explicite. Piața e singurul lucru care variază; restul codului primește un obiect `Market` și nu știe nimic despre țări.

```yaml
scraping:
  markets:
    de:
      country: "GERMANY"          # jobspy Country enum
      language: "de"              # → col „Sprache" → limba Anschreiben
      locations: ["Germany"]      # la nivel de țară, NU pe orașe
      remote: false
      keyword_groups:
        - ["Python Developer", "Django Developer", "Backend Engineer"]
        - ["AI Engineer", "LLM Engineer", "Machine Learning Engineer"]
        - ["Data Engineer", "Automation Engineer"]
      sources: [jobspy, career_pages]
    ro_remote:
      country: "ROMANIA"
      language: "en"              # scrisoare EN, nu Anschreiben german
      locations: ["Romania"]
      remote: true                # → jobspy `is_remote=True`
      keyword_groups:
        - ["Python Developer", "Django Developer"]
        - ["AI Engineer", "LLM Engineer"]
      sources: [jobspy, remote_boards, ro_boards]

  hours_old: 72                   # era 168, cu interval de 6h
  max_results_per_call: 50        # redenumit din `max_results_per_source`
  filters:                        # devin efective (azi sunt cod mort)
    exclude_keywords: ["Senior Staff", "Principal", "Director", "VP", "Head of"]
    min_salary: 0
    min_prescore: 40              # pragul sub care nu se cheltuie AI
```

### Alternative respinse

| Variantă | De ce nu |
|---|---|
| Flag `--country` la CLI | Keywords, limba și sursele rămân globale — exact problema de azi, doar mutată în linia de comandă. |
| Clase Python per țară | Diferențele dintre piețe sunt **date**, nu comportament. O ierarhie de clase e peste nevoie. |

## 4. Componente

Fiecare are o singură responsabilitate și poate fi înțeleasă și testată izolat.

| Fișier | Rol | Depinde de |
|---|---|---|
| `scrapers/base.py` *(nou)* | `Market` dataclass, protocolul `Source.scrape(market) -> list[dict]`, normalizarea comună a jobului | — |
| `scrapers/jobspy_scraper.py` *(rescris)* | Un apel per `(market, keyword_group, site)`. `is_remote` și `country_indeed` din market. Paralel + backoff pe 429. | jobspy |
| `scrapers/remote_boards.py` *(nou)* | RemoteOK, Remotive, WeWorkRemotely — JSON public, fără scraping HTML | httpx |
| `scrapers/ro_boards.py` *(nou)* | eJobs, BestJobs — HTML. **best-effort**, vezi §7 | httpx, bs4 |
| `scrapers/career_page_scraper.py` *(neschimbat)* | Career pages studiouri | httpx, bs4 |
| `filters/relevance.py` *(nou)* | Funcții pure: `prescore(job, cfg) -> (int, list[str])`. Zero I/O. Aici trăiesc `exclude_keywords`, senioritatea, potrivirea de skills | — |
| `store/seen.py` *(nou)* | Index persistent de joburi văzute, cu seed din Sheet | — |
| `agents/scraper_agent.py` *(refactor)* | Orchestrează `market × sursă`, izolează erorile per sursă | toate de sus |
| `agents/analyzer_agent.py` *(atingere minimă)* | Primește doar supraviețuitorii prefiltrului | — |

## 5. Fluxul de date

```
[market × sursă]  →  normalize  →  dedupe  →  prescore  →  AI  →  Anschreiben+PDF  →  Drive  →  Sheet
   paralel           base.py      seen.py    relevance   analyzer  cover_letter      n8n      n8n ingest
```

### 5.1 Reducerea apelurilor: 49 → ~12

Nu prin tăiere de acoperire, ci pentru că bucla actuală e greșit construită:

- **Locațiile de oraș ies din buclă.** Indeed cu `location="Germany"` returnează oricum joburi din toate orașele. Cele 5 căutări pe oraș sunt variante ale aceleiași căutări care se lovesc reciproc de rate-limit.
- **Keywords se grupează** în interogări booleene (`"Python Developer" OR "Django Developer"`) în loc de una per keyword.
- Rezultat: `2 piețe × ~3 grupuri × ~2 site-uri ≈ 12 apeluri`, rulate în paralel cu `ThreadPoolExecutor`, cu backoff exponențial pe 429.
- `hours_old`: 168h → 72h. La interval de 6h, 168h înseamnă re-descărcarea acelorași joburi de ~28 de ori.

### 5.2 Deduplicare cu memorie între rulări

- **Cheie primară:** URL normalizat — host lowercase, fără parametri de tracking (`utm_*`, `gh_src`, `from`, `src`), fără slash final. Azi același job cu doi parametri diferiți intră de două ori.
- **Cheie secundară:** `slug(firma)|slug(titlu)` — prinde același job postat pe două board-uri.
- **Stocare:** `data/seen_jobs.json` → `{cheie: {first_seen, last_seen, decision}}`, cu `decision ∈ {respins_prefiltru, analizat, trimis_in_sheet, aplicat}`. Încărcat într-un `set` → lookup O(1).

### 5.3 Prescore (poarta ieftină)

Funcție pură, fără rețea și fără AI. Returnează scor 0-100 + motive:

- `-∞` (respingere imediată) dacă titlul conține un `exclude_keywords`.
- Puncte pentru suprapunerea cu `skills.primary` (greutate mare) și `skills.secondary` (greutate mică) în titlu și descriere.
- Penalizare pentru senioritate peste profil.
- Bonus pentru potrivirea cu `keyword_groups` ale pieței.
- Joburile sub `filters.min_prescore` sunt marcate `respins_prefiltru` în `seen.json` și **nu ajung niciodată la AI**.

Câmpul `Keywords Match` din Sheet (există deja, dar nu e în `FIELD_MAP`) primește motivele — devine auditabil de ce a trecut un job.

### 5.4 Scrierea în Sheet — fără coloane noi

Piața se codifică în coloane care **există deja**, ca să nu se creeze coloane fantomă la upsert:

- `Quelle` ← `indeed-de`, `indeed-ro`, `remoteok`, `ejobs`, `linkedin`, `career_page`
- `Sprache` ← `de` / `en`, luat din `market.language`; asta comandă limba scrisorii în `cover_letter_gen`.
- `Match Score` ← scorul AI (comportament existent).

Se păstrează regulile din `sender/n8n_mapping.py`: se emit doar cheile cu valoare (o cheie cu `""` **golește** celula la upsert), iar `status="new"` rămâne filtrat — col M aparține mașinii de stări a Apps Script.

## 6. Infrastructură n8n nouă: `JobApp Urls`

Pentru ca joburile deja aplicate să nu reapară, `store/seen.py` trebuie alimentat din Sheet. `JobApp Pending` returnează **doar** rândurile cu `Sende Befehl = SENDEN`, deci nu dă istoricul.

**`JobApp Pending` NU se modifică** — e trigger-ul de producție, riscul nu se justifică.

Workflow nou, read-only: `Webhook (Header Auth X-JobApp-Secret) → Google Sheets read → Code (proiecție) → Respond`, care returnează **doar 3 coloane**: `Apply URL`, `Status`, `Email Status`.

Efect secundar util: azi un poll transferă 1086 rânduri × 1168 chei (28 headere reale + ~1140 coloane fantomă `col_29`…`col_1167`). `JobApp Urls` transferă 1086 × 3.

Se adaugă în `config/settings.yaml` la `n8n.paths.urls` și se versionează în `n8n_workflows/jobapp_urls.json` prin `scripts/deploy_n8n_workflows.py`.

## 7. Tratarea erorilor

Principiu: **o sursă căzută nu doboară runul, dar nici nu tace.**

- Fiecare pereche `(market, sursă)` rulează izolat. Excepția se prinde, se logează cu context (`[ro_remote/ejobs] HTTP 503`) și intră în `AgentResult.errors`.
- `AgentStatus` devine onest:
  - `SUCCESS` — toate sursele cerute au răspuns;
  - `PARTIAL` — unele au căzut, dar există joburi;
  - `FAILED` — nu s-a întors nimic.
- `print()` din `jobspy_scraper.py:66` se înlocuiește cu `self.logger`.
- `ro_boards` e marcat `best_effort: true` în config: eșecul lui nu setează niciodată `FAILED`.

### Riscuri asumate explicit

| Risc | Poziție |
|---|---|
| **LinkedIn blochează** | jobspy suportă `linkedin`, dar e cel mai agresiv la rate-limit; fără proxy dă frecvent 429. Se activează ca sursă *best-effort*, nu ca pilon. Dacă e mort după prima rulare reală, `remote_boards` acoperă golul. |
| **eJobs/BestJobs se strică** | Nu au API public. Scraping HTML **se va strica** la primul redesign. Acceptat prin design, izolat într-un modul propriu, cu teste pe fixture salvat ca să se vadă imediat ce s-a rupt. |
| **Headerele Sheet-ului** | Vechiul spec (`2026-06-22-...`) e **depășit** (zice `URL`/`Gefunden am`/`Anschreiben`, realitatea e `Apply URL`/`Datum gefunden`/`Anschreiben PDF`). Lista reală se citește din Sheet la implementare, nu din documentație. |

## 8. Teste

Regula existentă se păstrează: **suita pytest nu atinge rețeaua.**

- **Unitare, offline** (partea grea a logicii noi): `filters/relevance.prescore`, `store/seen` (chei, normalizare URL, persistență), rezolvarea `Market` din YAML.
- **Scrapere:** fixture-uri salvate (un JSON RemoteOK real, un HTML eJobs real) + `httpx` mock-uit, ca la testele n8n existente.
- **Non-regresie pe zona delicată:** `job_to_sheet_row` nu emite chei în afara headerelor reale; `status="new"` rămâne filtrat; o cheie fără valoare nu ajunge `""` în payload.
- **E2E real:** doar în `scripts/test_e2e_manual.py`, în afara suitei.

## 9. Ce NU intră (YAGNI)

Proxy-uri rotative, bază de date în loc de JSON, dashboard, model de scoring propriu, trimitere automată de emailuri. Se adaugă când apare nevoia reală, nu preventiv.

## 10. Criterii de succes

1. Un run pe ambele piețe face ≤ 15 apeluri de scraping și durează sub ~3 minute.
2. Rezultatele conțin cel puțin două platforme distincte, nu doar `indeed`.
3. Prescore respinge local ≥ 50% din joburi; AI-ul rulează doar pe rest.
4. Un job deja marcat `aplicat` în Sheet nu mai apare ca nou la rularea următoare.
5. Piața `ro_remote` produce joburi remote cu `Sprache = en`.
6. O sursă căzută dă `PARTIAL` cu eroarea vizibilă în raport, nu `SUCCESS`.
7. `pytest -q` trece, fără rețea.
