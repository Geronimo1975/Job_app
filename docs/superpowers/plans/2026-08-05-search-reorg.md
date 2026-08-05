# Reorganizarea căutării — Plan de implementare

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Căutarea trece pe profilul part-time/flexibil RO/UAE/SA/CH (Claude Code, n8n, chatbots, SEO/AIO/GEO), cu prefiltru care taie rolurile greșite — joburi mai relevante în Sheet.

**Architecture:** Doar configurație + prefiltru: piețe noi în `config/settings.yaml` (arhitectura `markets` există) și două extensii pure în `filters/relevance.py` (penalizare `negative_keywords`, bonus `flex_signals`), ambele citite din `scraping.filters`.

**Tech Stack:** Python 3.12, pytest (suită offline, config-uri inline în teste), YAML cu ancore.

## Global Constraints

- **NU se modifică** decât: `config/settings.yaml`, `filters/relevance.py`, `tests/test_relevance.py`. (Constrângere fermă din spec: pipeline, generare, Sheet/n8n, dashboard, dedupe, `Market`/`SearchQuery`/scrapere — neatinse.)
- Funcțiile din `filters/relevance.py` rămân pure: fără rețea, fără disc, fără AI.
- Comentarii și mesaje în română; commit-uri în română cu tag `JOB-47`.
- Suita nu lovește rețeaua. Rulare: `python -m pytest -q` din rădăcina repo-ului (venv: `source .venv/bin/activate`).
- `min_prescore` rămâne 40; semantica `HARD_REJECT` (=-1) neatinsă.

---

### Task 1: Penalizare `negative_keywords` în prescore

**Files:**
- Modify: `filters/relevance.py` (după blocul de excludere dură, ~linia 95)
- Test: `tests/test_relevance.py`

**Interfaces:**
- Consumes: `prescore(job, config, search_keywords=None) -> tuple[int, list[str]]` (existent), `_contains(text, token)` (existent).
- Produces: `prescore` citește lista nouă `scraping.filters.negative_keywords`; motiv nou `rol nepotrivit: <kw>`; constanta `_W_NEGATIVE = -60`. Scorul final e plafonat jos la 0 (fără scoruri negative în Sheet), distinct de `HARD_REJECT`.

- [ ] **Step 1: Scrie testele care pică**

În `tests/test_relevance.py`, adaugă clasa (folosește stilul claselor existente; `prescore`/`passes_prefilter` sunt deja importate în fișier):

```python
class TestNegativeKeywords:
    CFG = {
        "scraping": {"filters": {
            "min_prescore": 40,
            "negative_keywords": ["sales", "SAP consultant", "recruiter"],
        }},
        "skills": {"primary": ["n8n"], "secondary": []},
    }

    def test_negative_keyword_in_title_sinks_score(self):
        job = {"title": "Sales Manager n8n", "description": ""}
        score, reasons = prescore(job, self.CFG)
        assert score == 0                      # 20 (primary în titlu) - 60, plafonat la 0
        assert "rol nepotrivit: sales" in reasons

    def test_negative_matches_whole_words_only(self):
        # „sales" nu trebuie să se prindă în „Salesforce" — potrivire pe cuvânt întreg
        job = {"title": "Salesforce n8n Developer", "description": ""}
        score, reasons = prescore(job, self.CFG)
        assert not any(r.startswith("rol nepotrivit") for r in reasons)

    def test_negative_only_on_title_not_description(self):
        # Descrierile menționează des alte departamente („work with sales team")
        job = {"title": "n8n Automation Engineer", "description": "you support the sales team"}
        score, reasons = prescore(job, self.CFG)
        assert not any(r.startswith("rol nepotrivit") for r in reasons)

    def test_multiword_negative_rejects(self):
        job = {"title": "SAP Consultant (m/w/d)", "description": ""}
        score, reasons = prescore(job, self.CFG)
        assert "rol nepotrivit: SAP consultant" in reasons

    def test_strong_signals_can_survive_penalty(self):
        # Penalizarea e mare, dar nu HARD_REJECT: semnale multe pot compensa
        cfg = {
            "scraping": {"filters": {"min_prescore": 40,
                                     "negative_keywords": ["recruiter"]}},
            "skills": {"primary": ["n8n", "chatbot", "Claude Code",
                                   "workflow automation", "conversational AI"],
                       "secondary": []},
        }
        job = {"title": "Recruiter for n8n chatbot Claude Code "
                        "workflow automation conversational AI team",
               "description": ""}
        score, _ = prescore(job, cfg)
        assert score == 40                     # 5×20 - 60 = 40 → exact pragul
```

- [ ] **Step 2: Rulează testele — trebuie să pice**

Run: `python -m pytest tests/test_relevance.py::TestNegativeKeywords -q`
Expected: FAIL (motivul `rol nepotrivit: …` nu există încă; scorurile nu scad).

- [ ] **Step 3: Implementarea minimă**

În `filters/relevance.py`, sub constantele existente adaugă:

```python
# Penalizare pentru rol greșit în titlu (sales, consultant SAP…): mare cât să
# scufunde jobul sub min_prescore, dar NU hard-reject — un titlu cu multe
# semnale bune poate compensa (spre deosebire de exclude_keywords).
_W_NEGATIVE = -60
```

În `prescore`, imediat DUPĂ bucla `exclude_keywords` (păstrează `score = 0` / `reasons` existente, penalizarea intră în acumulator):

```python
    # ── Penalizare rol nepotrivit: doar pe titlu, cuvânt întreg ──
    # (pe descriere ar penaliza „work with the sales team")
    for negative in filters.get("negative_keywords", []) or []:
        if _contains(title_l, negative.lower()):
            score += _W_NEGATIVE
            reasons.append(f"rol nepotrivit: {negative}")
```

Iar la final, în loc de `return min(score, 100), reasons`:

```python
    return max(0, min(score, 100)), reasons
```

- [ ] **Step 4: Verifică**

Run: `python -m pytest tests/test_relevance.py -q`
Expected: PASS toate (inclusiv cele vechi — plafonul jos la 0 nu schimbă niciun scor pozitiv existent).

- [ ] **Step 5: Commit**

```bash
git add filters/relevance.py tests/test_relevance.py
git commit -m "feat(filters): penalizare negative_keywords pe titlu (JOB-47)"
```

---

### Task 2: Bonus `flex_signals` în prescore

**Files:**
- Modify: `filters/relevance.py` (după blocul de bonus keyword de piață, ~linia 127)
- Test: `tests/test_relevance.py`

**Interfaces:**
- Consumes: `prescore` (cu penalizarea din Task 1), `_contains`.
- Produces: `prescore` citește lista nouă `scraping.filters.flex_signals`; motiv nou `flexibil: <semnal>`; constante `_W_FLEX = 10`, `_MAX_FLEX_BONUS = 20`. Semnalele se caută în titlu ȘI descriere.

- [ ] **Step 1: Scrie testele care pică**

```python
class TestFlexSignals:
    CFG = {
        "scraping": {"filters": {
            "min_prescore": 40,
            "flex_signals": ["part-time", "freelance", "flexible hours", "remote"],
        }},
        "skills": {"primary": ["chatbot"], "secondary": []},
    }

    def test_flex_signal_in_title_adds_bonus(self):
        base = {"title": "Chatbot Developer", "description": ""}
        flex = {"title": "Chatbot Developer (part-time)", "description": ""}
        s_base, _ = prescore(base, self.CFG)
        s_flex, reasons = prescore(flex, self.CFG)
        assert s_flex == s_base + 10
        assert "flexibil: part-time" in reasons

    def test_flex_signal_in_description_counts(self):
        job = {"title": "Chatbot Developer",
               "description": "freelance collaboration, flexible hours"}
        _, reasons = prescore(job, self.CFG)
        assert "flexibil: freelance" in reasons
        assert "flexibil: flexible hours" in reasons

    def test_flex_bonus_is_capped(self):
        job = {"title": "Chatbot Developer",
               "description": "remote, part-time, freelance, flexible hours"}
        s, _ = prescore(job, self.CFG)
        base = {"title": "Chatbot Developer", "description": ""}
        s_base, _ = prescore(base, self.CFG)
        assert s == s_base + 20                # 4 semnale × 10, plafonat la 20

    def test_no_signals_no_bonus_no_reasons(self):
        job = {"title": "Chatbot Developer", "description": "on-site role"}
        _, reasons = prescore(job, self.CFG)
        assert not any(r.startswith("flexibil") for r in reasons)
```

- [ ] **Step 2: Rulează — trebuie să pice**

Run: `python -m pytest tests/test_relevance.py::TestFlexSignals -q`
Expected: FAIL (bonusul nu există).

- [ ] **Step 3: Implementarea minimă**

Constante, lângă `_MAX_KEYWORD_BONUS`:

```python
# Bonus pentru semnale de flexibilitate (part-time/freelance/remote…): profilul
# caută explicit angajamente sub 40h, deci flexibilitatea E relevanță, nu
# doar un beneficiu. Plafonat: două semnale ajung.
_W_FLEX = 10
_MAX_FLEX_BONUS = 20
```

În `prescore`, după blocul de bonus keyword de piață:

```python
    # ── Bonus flexibilitate: titlu SAU descriere ────────────────
    # (aici descrierea contează — „remote, part-time possible" apare rar în titlu)
    flex_bonus = 0
    for signal in filters.get("flex_signals", []) or []:
        if _contains(text_l, signal.lower()):
            flex_bonus += _W_FLEX
            reasons.append(f"flexibil: {signal}")
    score += min(flex_bonus, _MAX_FLEX_BONUS)
```

- [ ] **Step 4: Verifică**

Run: `python -m pytest tests/test_relevance.py -q`
Expected: PASS toate.

- [ ] **Step 5: Commit**

```bash
git add filters/relevance.py tests/test_relevance.py
git commit -m "feat(filters): bonus flex_signals pentru part-time/remote (JOB-47)"
```

---

### Task 3: `config/settings.yaml` — piețe, skills, filtre

**Files:**
- Modify: `config/settings.yaml` (blocurile `skills:`, `scraping.markets:`, `scraping.filters:`)

**Interfaces:**
- Consumes: structura `markets` existentă (`Market.from_config`), cheile `negative_keywords`/`flex_signals` din Task 1-2.
- Produces: 4 piețe (`ro_remote`, `uae`, `saudi`, `ch`) cu `keyword_groups` partajate prin ancoră YAML; `skills.primary/secondary` pe profilul nou (alimentează și prescore, și `matching_skills` la generare); `gaming_specific` rămâne neatins.

- [ ] **Step 1: Rescrie blocul `skills.primary`/`skills.secondary`**

```yaml
skills:
  primary:
    - "Claude Code"
    - "AI Automation"
    - "n8n"
    - "Workflow Automation (Make.com, Zapier)"
    - "Chatbot Development"
    - "Conversational AI"
    - "SEO/AIO/GEO"
    - "LLM APIs (OpenAI, Claude, Gemini)"
  secondary:
    - "Python"
    - "Django"
    - "JavaScript"
    - "Docker"
    - "PostgreSQL"
    - "REST APIs"
    - "Git"
  # gaming_specific rămâne neschimbat (nefolosit de prescore)
```

- [ ] **Step 2: Înlocuiește blocul `scraping.markets`** (piețele `de` și vechiul `ro_remote` dispar integral)

```yaml
  markets:
    ro_remote:                        # PRIORITAR — remote lucrabil din DE
      country: "ROMANIA"
      language: "en"
      locations: ["Romania"]
      remote: true
      keyword_groups: &skills_groups
        - ["Claude Code", "AI Developer", "vibe coding", "AI Automation"]
        - ["n8n", "workflow automation", "Make.com", "Zapier"]
        - ["chatbot developer", "conversational AI", "LLM integration"]
        - ["SEO specialist", "technical SEO", "AI SEO", "GEO optimization"]
      sources: ["jobspy", "ro_boards"]
      best_effort_sources: ["linkedin", "ro_boards"]

    uae:
      country: "UNITEDARABEMIRATES"   # enum jobspy verificat
      language: "en"
      locations: ["United Arab Emirates"]
      remote: true
      keyword_groups: *skills_groups
      sources: ["jobspy"]
      best_effort_sources: ["linkedin"]

    saudi:
      country: "SAUDIARABIA"
      language: "en"
      locations: ["Saudi Arabia"]
      remote: true
      keyword_groups: *skills_groups
      sources: ["jobspy"]
      best_effort_sources: ["linkedin"]

    ch:
      country: "SWITZERLAND"
      language: "en"                  # scrisori EN și pentru CH (decizia userului)
      locations: ["Switzerland"]
      remote: true
      keyword_groups: *skills_groups
      sources: ["jobspy"]
      best_effort_sources: ["linkedin"]
```

Notă: `career_pages` iese din toate sursele (scraper pre-piețe pe companii germane); codul lui NU se șterge.

- [ ] **Step 3: Extinde `scraping.filters`** (păstrează `exclude_keywords` și `min_prescore: 40` existente)

```yaml
  filters:
    exclude_keywords:
      - "Senior Staff"
      - "Principal"
      - "Director"
      - "VP"
      - "Head of"
    # Roluri greșite: penalizare mare în prescore (nu hard-reject)
    negative_keywords:
      - "sales"
      - "account manager"
      - "SAP consultant"
      - "support agent"
      - "customer support"
      - "recruiter"
      - "warehouse"
      - "nurse"
      - "accountant"
    # Semnale part-time/flexibil: bonus în prescore
    flex_signals:
      - "part-time"
      - "parttime"
      - "part time"
      - "contract"
      - "freelance"
      - "flexible hours"
      - "remote"
    min_salary: 0
    min_prescore: 40
```

- [ ] **Step 4: Verifică config-ul se încarcă și piețele se parsează**

```bash
source .venv/bin/activate
python3 -c "
import yaml
from scrapers.base import Market
cfg = yaml.safe_load(open('config/settings.yaml', encoding='utf-8'))
ms = Market.from_config(cfg)
assert {m.key for m in ms} == {'ro_remote', 'uae', 'saudi', 'ch'}, {m.key for m in ms}
assert all(m.keyword_groups for m in ms)          # ancora YAML s-a propagat
assert all(m.language == 'en' for m in ms)
print('OK:', [(m.key, m.country) for m in ms])
"
```

Expected: `OK: [('ro_remote', 'ROMANIA'), ('uae', 'UNITEDARABEMIRATES'), ('saudi', 'SAUDIARABIA'), ('ch', 'SWITZERLAND')]` (ordinea poate diferi).

- [ ] **Step 5: Suita completă**

Run: `python -m pytest -q`
Expected: PASS integral (testele folosesc config-uri inline; dacă totuși ceva referă piețele vechi pe config-ul real, actualizează DOAR acel test la piețele noi).

- [ ] **Step 6: Commit**

```bash
git add config/settings.yaml
git commit -m "feat(config): piețe part-time RO/UAE/SA/CH + profil skills nou (JOB-47)"
```

---

### Task 4: Verificare live + reset dedupe (manual, cu servicii reale)

**Files:** niciunul de modificat — doar verificare operațională. `data/seen_jobs.json` se golește (Sheet-ul a fost deja golit pe 2026-08-05, cu backup în `data/backup_bewerbungen_2026-08-05.json`).

- [ ] **Step 1: Golește indexul de dedupe** (Sheet-ul e gol, deci nu mai există `aplicat` de protejat — verifică totuși înainte)

```bash
python3 -c "
import json
d = json.load(open('data/seen_jobs.json'))
aplicate = [k for k, v in d.items() if isinstance(v, dict) and v.get('status') == 'aplicat']
print('aplicate:', len(aplicate))"
# Dacă aplicate == 0:
python3 -c "import json; json.dump({}, open('data/seen_jobs.json','w'))"
```

Dacă `aplicate > 0`: NU șterge — raportează utilizatorului și așteaptă decizia.

- [ ] **Step 2: Scrape live pe piața prioritară**

```bash
set -a; source .env; set +a
python3 main.py --step scrape
```

Expected: rulează pe cele 4 piețe fără erori blocante (`linkedin`/`ro_boards` pot da avertismente best-effort); `data/jobs_filtered.json` conține joburi cu prescore ≥ 40.

- [ ] **Step 3: Inspecția relevanței (criteriul de succes din spec)**

```bash
python3 -c "
import json
jobs = json.load(open('data/jobs_filtered.json'))
for j in sorted(jobs, key=lambda x: -x.get('prescore', 0))[:15]:
    print(j.get('prescore'), '|', j.get('market'), '|', j.get('title')[:70])
print('total:', len(jobs))"
```

Expected: titlurile din top sunt majoritar AI/automatizare/chatbot/SEO; zero roluri evident greșite (sales/SAP/suport). Dacă nu: ajustează listele din `scraping.filters` (doar YAML) și repetă.

- [ ] **Step 4: Verificare dashboard** — o căutare din pagina Căutare cu presetul `ro_remote` (apare automat); rezultatele staged au prescore-uri coerente.

- [ ] **Step 5: Commit final** (dacă au fost ajustări de liste la Step 3)

```bash
git add config/settings.yaml
git commit -m "tune(config): calibrare liste negative/flex după scrape live (JOB-47)"
```

---

## Self-review (făcut)

- Acoperire spec: §1 markets → Task 3; §2 fără job_type → nicio sarcină (confirmat zero cod); §3 relevance → Task 1-2; §4 neatinse → Global Constraints; Testare → pași TDD + Task 4; criteriu de succes → Task 4 Step 3.
- Fără placeholder-e; tipuri/nume consistente (`negative_keywords`, `flex_signals`, `_W_NEGATIVE`, `_W_FLEX`, `_MAX_FLEX_BONUS` folosite identic în toate task-urile).
- Ordinea buclelor în `prescore`: penalizarea (Task 1) intră înainte de bonusuri, dar suma e comutativă — nu contează pentru rezultat; plafonul `max(0, …)` se aplică o singură dată, la return.
