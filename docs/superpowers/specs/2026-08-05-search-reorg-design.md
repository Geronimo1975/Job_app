# Reorganizarea sistemului de căutare — profil part-time RO/UAE/SA/CH

**Data:** 2026-08-05 · **Status:** aprobat de utilizator (design verbal) · **Înlocuiește** profilul de căutare actual (Python/Django full-time DE).

> **Constrângere fermă (utilizator):** se schimbă DOAR structura de căutare — configurația piețelor și filtrul de relevanță. Structura operațională (pipeline, generare, trimitere, Sheet/n8n, dashboard, dedupe) funcționează foarte bine și NU se atinge. Obiectivul final: joburi mai relevante în tabelul din Google Sheet.

## Scop

Căutarea trece integral pe noul profil profesional al utilizatorului:

- **Ce:** roluri pe Claude Code / „vibe coding", automatizare n8n, dezvoltare chatbots / conversational AI, optimizare SEO / AIO / GEO.
- **Unde:** remote lucrabil din altă țară — prioritar **România**, apoi **UAE**, **Arabia Saudită**, **Elveția**.
- **Cum:** part-time SAU full-time remote flexibil (definiție largă — filtrul `job_type` al board-urilor e nesigur, triajul real se face la prefiltru).
- **Limbă:** engleză peste tot, inclusiv Elveția (decizia utilizatorului) → col „Sprache" = `en`, scrisori EN.

Zgomotul principal de eliminat: **roluri greșite** (sales, consultanță SAP, suport etc.) care trec de prefiltru.

## Ce se schimbă

### 1. `config/settings.yaml` — piețe noi (înlocuire completă)

Piețele `de` și `ro_remote` (vechiul conținut) dispar. Intră 4 piețe care partajează aceleași grupuri de keywords printr-o ancoră YAML:

```yaml
markets:
  ro_remote:                       # PRIORITAR
    country: "ROMANIA"
    language: "en"
    locations: ["Romania"]
    remote: true
    keyword_groups: &skills
      - ["Claude Code", "AI Developer", "vibe coding", "AI Automation"]
      - ["n8n", "workflow automation", "Make.com", "Zapier"]
      - ["chatbot developer", "conversational AI", "LLM integration"]
      - ["SEO specialist", "technical SEO", "AI SEO", "GEO optimization"]
    sources: ["jobspy", "ro_boards"]
    best_effort_sources: ["linkedin", "ro_boards"]
  uae:
    country: "UNITEDARABEMIRATES"  # enum jobspy verificat
    language: "en"
    locations: ["United Arab Emirates"]
    remote: true
    keyword_groups: *skills
    sources: ["jobspy"]
    best_effort_sources: ["linkedin"]
  saudi:
    country: "SAUDIARABIA"
    # analog uae
  ch:
    country: "SWITZERLAND"
    # analog uae
```

- Enum-urile jobspy verificate în mediul instalat: `ROMANIA`, `UNITEDARABEMIRATES`, `SAUDIARABIA`, `SWITZERLAND`.
- `remote: true` peste tot — condiție, nu opțiune (utilizatorul lucrează din altă țară decât angajatorul).
- `career_pages` iese din surse (scraper pre-piețe, companii germane — nu mai corespunde profilului). Codul `CareerPageScraper` NU se șterge.
- Cuvintele exacte din grupuri se pot ajusta la implementare fără a modifica designul.

### 2. FĂRĂ filtru `job_type` la board

Definiția „largă" aleasă (part-time + full-time remote flexibil) face filtrul `job_type` de board inutil — nu se trimite. Triajul part-time/flexibil se face exclusiv la prefiltru (secțiunea 3). Consecință: **zero schimbări în `Market`/`SearchQuery`/`jobspy_scraper.py`**.

### 3. `filters/relevance.py` — penalizări + bonus flexibilitate

Două extensii, ambele configurabile din `scraping.filters` (fără cod nou la schimbarea listelor):

- **`negative_keywords`** (listă nouă în YAML): termeni de rol greșit — ex. `sales`, `account manager`, `sap consultant`, `support agent`, `recruiter`, `warehouse`, `nurse`. Prezența în **titlu** aplică o penalizare mare (suficientă ca scorul să cadă sub `min_prescore` în absența altor semnale puternice). Motivul apare în `keywords_match` (câmp intern), ca la restul regulilor.
- **`flex_signals`** (listă nouă în YAML): `part-time`, `parttime`, `part time`, `contract`, `freelance`, `flexible hours`, `remote` — prezența în titlu/descriere adaugă bonus. Valori concrete de punctaj: la implementare, calibrate pe date reale (criteriu: joburile-mostră cu roluri greșite cad sub prag, cele flexibile-relevante trec).

Neatinse: dedupe (`store/seen.py`), bonusul pe keyword-urile pieței (JOB-46), mecanica `min_prescore`. Operațional, o singură dată după schimbare: se șterg intrările `respins_prefiltru` din `data/seen_jobs.json` ca noul scoring să re-evalueze (intrările `aplicat` nu se ating niciodată).

### 4. Ce NU se atinge (explicit)

- Dashboard: zero cod — presetele din pagina Căutare se populează din `markets` (cele 4 apar automat), prescore-ul ad-hoc folosește deja keyword-urile căutate.
- Generare/Sheet: `language: en` pe toate piețele → scrisori EN prin mecanismul existent; col „Sprache" = `en` prin fluxul existent. Nimic de schimbat în generatoare, sender, n8n, Apps Script.
- Pipeline (`main.py`, agenți), dedupe (`store/seen.py`), rate limit email, `Market`/`SearchQuery`/scrapere.

Singurele fișiere modificate: `config/settings.yaml`, `filters/relevance.py` (+ testele aferente).

## Testare

- Teste actualizate: config/piețe (nume noi), eventualele referințe la piețele vechi.
- Teste noi în `relevance.py`: titlu cu negative keyword cade sub prag; descriere cu semnale flexibile primește bonus; interacțiunea cu bonusul de keywords al pieței.
- Suita rămâne offline (mock-uri), ca până acum.
- Verificare live la final: `--step scrape` pe `ro_remote` + o căutare din dashboard, cu inspecția distribuției de prescore.

## Ne-inclus intenționat (fază 2, la nevoie)

- Parsarea eJobs `window.__NUXT__` (~19× randament pe sursa RO).
- Board-uri Golf dedicate (Bayt, GulfTalent).
- Strat de „profiluri de căutare" comutabile.

## Criteriu de succes

O rulare de scrape pe cele 4 piețe întoarce majoritar roluri din profilul nou (AI/automatizare/SEO, remote, semnale flexibile), fără roluri evident greșite peste `min_prescore`.
