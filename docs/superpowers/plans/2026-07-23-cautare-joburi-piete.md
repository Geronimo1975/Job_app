# Revizia căutării de joburi (piețe DE + RO remote) — Plan de implementare

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Înlocuiește bucla `keywords × locations` din scraping cu piețe configurabile (DE + RO remote), adaugă un prefiltru local înaintea AI-ului și un index de deduplicare cu memorie între rulări.

**Architecture:** Un `Market` (dataclass citit din YAML) e singurul lucru care variază între țări; sursele primesc un `Market` și nu știu nimic despre țări. Joburile trec prin `store/seen.py` (dedupe cu memorie, alimentat și din Google Sheet) și `filters/relevance.py` (scor determinist 0-100, fără AI) înainte să ajungă la analiza AI. Fiecare pereche `(piață, sursă)` rulează izolat, în paralel, iar eșecul uneia dă `PARTIAL`, nu `SUCCESS`.

**Tech Stack:** Python 3.12+, `python-jobspy`, `httpx`, `BeautifulSoup4`, `PyYAML`, `pytest`, `concurrent.futures.ThreadPoolExecutor`, `defusedxml` (dependență nouă — vezi Task 5).

**Spec:** `docs/superpowers/specs/2026-07-23-cautare-joburi-piete-design.md`

## Global Constraints

- Python 3.12+. Comentariile, docstring-urile și mesajele de log se scriu **în limba română**.
- Commit-uri în română cu tag `JOB-NN` secvențial. Ultimul folosit e `JOB-34` (spec-ul). Prima sarcină din plan folosește `JOB-35`, apoi crescător.
- **Suita `pytest` NU atinge rețeaua.** Orice apel HTTP se mock-uiește sau se servește dintr-un fixture salvat pe disc.
- **Nimic nu încarcă `.env` automat** — nu adăuga `load_dotenv`. Variabilele se exportă în shell.
- **Python nu atinge Google direct.** Orice interacțiune cu Sheet-ul trece prin webhook n8n.
- În payload-ul către Sheet: o cheie cu `""` **golește** celula la upsert. Se emit doar cheile cu valoare. Nu scrie niciodată coloane care nu există în Sheet.
- Coloana M „Status" aparține mașinii de stări a Apps Script (`plan→send→running→done/error`). Valoarea `status="new"` a scraperelor rămâne filtrată în `sender/n8n_mapping.py`.
- Config centralizat în `config/settings.yaml`; secretele doar în `.env`.
- Rulează testele cu venv-ul activ: `source .venv/bin/activate`.

---

## Structura fișierelor

| Fișier | Responsabilitate | Stare |
|---|---|---|
| `filters/__init__.py` | marker pachet | creat (T1) |
| `filters/relevance.py` | scor determinist 0-100 + motive, fără I/O și fără AI | creat (T1) |
| `store/__init__.py` | marker pachet | creat (T2) |
| `store/seen.py` | normalizare URL, chei de dedupe, index persistent | creat (T2) |
| `scrapers/base.py` | `Market` dataclass, `make_job()`, `save_jobs()` | creat (T3) |
| `config/settings.yaml` | blocul `scraping.markets` + `filters` | modificat (T3) |
| `scrapers/jobspy_scraper.py` | un apel per `(piață, grup keywords, site)`, paralel | rescris (T4) |
| `scrapers/remote_boards.py` | RemoteOK + Remotive (JSON) + WeWorkRemotely (RSS) | creat (T5) |
| `scrapers/ro_boards.py` | eJobs + BestJobs, HTML, best-effort | creat (T6) |
| `agents/scraper_agent.py` | orchestrare `piață × sursă`, status onest | refactorizat (T7) |
| `main.py` | poarta prefiltru între `scrape` și `analyze` | modificat (T8) |
| `n8n_workflows/jobapp_urls.json` | workflow read-only care întoarce 3 coloane | creat (T9) |
| `sender/n8n_client.py` | `get_known_urls()` | modificat (T9) |
| `CLAUDE.md` | documentarea piețelor și a noilor module | modificat (T10) |

Ordinea dependențelor: T1, T2, T3 sunt independente între ele. T4–T6 depind de T3. T7 depinde de T3–T6. T8 depinde de T1, T2, T7. T9 depinde de T2. T10 la final.

---

### Task 1: Prefiltru determinist (`filters/relevance.py`)

Aici ajung în sfârșit să conteze `filters.exclude_keywords` din `config/settings.yaml`, care azi e cod mort (grep-ul confirmă că nu e citit nicăieri).

**Files:**
- Create: `filters/__init__.py`
- Create: `filters/relevance.py`
- Test: `tests/test_relevance.py`

**Interfaces:**
- Consumes: nimic (modul frunză, fără dependențe interne).
- Produces:
  - `HARD_REJECT: int = -1`
  - `prescore(job: dict, config: dict) -> tuple[int, list[str]]` — întoarce `(scor, motive)`. Scorul e `HARD_REJECT` (-1) la excludere, altfel `0..100`.
  - `passes_prefilter(job: dict, config: dict) -> bool`
  - `skill_tokens(skill: str) -> list[str]`

- [ ] **Step 1: Scrie testele care eșuează**

Creează `tests/test_relevance.py`:

```python
"""
Teste pentru prefiltrul determinist — funcții pure, fără rețea și fără AI.
"""

from filters.relevance import (
    HARD_REJECT,
    passes_prefilter,
    prescore,
    skill_tokens,
)


CFG = {
    "skills": {
        "primary": ["Python", "Django", "AI/ML"],
        "secondary": ["Docker", "PostgreSQL"],
    },
    "scraping": {
        "filters": {
            "exclude_keywords": ["Director", "VP", "Head of"],
            "min_prescore": 40,
        }
    },
}


def _job(title, description="", **extra):
    job = {"title": title, "description": description}
    job.update(extra)
    return job


class TestSkillTokens:
    def test_splits_on_slash(self):
        assert skill_tokens("AI/ML") == ["ai", "ml"]

    def test_strips_parenthetical(self):
        assert skill_tokens("LLM APIs (OpenAI, Claude)") == ["llm apis"]

    def test_simple_skill(self):
        assert skill_tokens("Python") == ["python"]


class TestHardReject:
    def test_excluded_keyword_in_title_rejects(self):
        score, reasons = prescore(_job("Director of Engineering"), CFG)
        assert score == HARD_REJECT
        assert any("Director" in r for r in reasons)

    def test_exclusion_is_case_insensitive(self):
        score, _ = prescore(_job("vp of python engineering"), CFG)
        assert score == HARD_REJECT

    def test_exclusion_only_on_title_not_description(self):
        """Un job normal care menționează „Director" în text nu se respinge."""
        score, _ = prescore(
            _job("Python Developer", "Du berichtest an den Director."), CFG
        )
        assert score > 0

    def test_rejected_job_does_not_pass(self):
        assert passes_prefilter(_job("VP Engineering"), CFG) is False


class TestScoring:
    def test_primary_skill_in_title_scores_high(self):
        score, reasons = prescore(_job("Python Developer"), CFG)
        assert score >= 20
        assert "Python" in reasons

    def test_primary_skill_in_description_scores_less_than_title(self):
        in_title, _ = prescore(_job("Python Developer"), CFG)
        in_desc, _ = prescore(_job("Backend Developer", "Wir nutzen Python."), CFG)
        assert in_desc < in_title
        assert in_desc > 0

    def test_secondary_skill_scores_less_than_primary(self):
        primary, _ = prescore(_job("Python Developer"), CFG)
        secondary, _ = prescore(_job("Docker Developer"), CFG)
        assert secondary < primary

    def test_score_is_capped_at_100(self):
        score, _ = prescore(
            _job(
                "Python Django AI ML Developer",
                "Python Django Docker PostgreSQL AI ML",
            ),
            CFG,
        )
        assert score <= 100

    def test_word_boundary_prevents_false_match(self):
        """„ml" nu trebuie să se potrivească în interiorul lui „html"."""
        score, reasons = prescore(_job("HTML Developer"), CFG)
        assert "AI/ML" not in reasons

    def test_irrelevant_job_scores_zero(self):
        score, reasons = prescore(_job("Truck Driver", "Führerschein C+E"), CFG)
        assert score == 0
        assert reasons == []


class TestThreshold:
    def test_score_above_threshold_passes(self):
        assert passes_prefilter(_job("Python Django Developer"), CFG) is True

    def test_score_below_threshold_fails(self):
        assert passes_prefilter(_job("Truck Driver"), CFG) is False

    def test_missing_filters_config_defaults_to_permissive(self):
        """Fără blocul `filters`, nimic nu se respinge pe prag."""
        score, _ = prescore(_job("Python Developer"), {"skills": {"primary": ["Python"]}})
        assert score > 0
        assert passes_prefilter(_job("Python Developer"), {"skills": {"primary": ["Python"]}}) is True
```

- [ ] **Step 2: Rulează testele ca să confirmi că eșuează**

```bash
source .venv/bin/activate && python -m pytest tests/test_relevance.py -q
```

Așteptat: `ModuleNotFoundError: No module named 'filters'`.

- [ ] **Step 3: Implementează modulul**

Creează `filters/__init__.py` gol, apoi `filters/relevance.py`:

```python
"""
Prefiltru determinist — decide ce joburi merită analiza AI.

Funcții pure: fără rețea, fără AI, fără citiri de pe disc. Aici se aplică
`scraping.filters` din settings.yaml, care până acum era config mort.
"""

import re

# Scor special: jobul e respins din start (ex. rol de conducere).
HARD_REJECT = -1

# Greutăți de scor. Un skill în titlu contează mult mai mult decât în
# descriere: descrierile enumeră tehnologii pe care firma le folosește
# undeva, titlul spune ce cauți de fapt.
_W_PRIMARY_TITLE = 20
_W_PRIMARY_DESC = 8
_W_SECONDARY_TITLE = 8
_W_SECONDARY_DESC = 3


def skill_tokens(skill: str) -> list[str]:
    """Sparge un skill din config în termeni căutabili.

    „AI/ML" → ["ai", "ml"]; „LLM APIs (OpenAI, Claude)" → ["llm apis"].
    Parantezele se aruncă: sunt exemple, nu termeni de căutat.
    """
    without_parens = re.sub(r"\([^)]*\)", "", skill)
    parts = [p.strip().lower() for p in without_parens.split("/")]
    return [p for p in parts if p]


def _contains(text: str, token: str) -> bool:
    """Potrivire pe cuvânt întreg, ca „ml" să nu se prindă în „html"."""
    return re.search(rf"\b{re.escape(token)}\b", text) is not None


def _matches(text: str, skill: str) -> bool:
    return any(_contains(text, tok) for tok in skill_tokens(skill))


def prescore(job: dict, config: dict) -> tuple[int, list[str]]:
    """Calculează un scor 0-100 (sau HARD_REJECT) plus motivele.

    Motivele ajung în coloana „Keywords Match" din Sheet, ca să fie
    auditabil de ce a trecut un job.
    """
    title = str(job.get("title") or "")
    description = str(job.get("description") or "")
    title_l = title.lower()
    text_l = f"{title_l}\n{description.lower()}"

    filters = (config.get("scraping", {}) or {}).get("filters", {}) or {}
    skills = config.get("skills", {}) or {}

    # ── Excludere dură: doar pe titlu ───────────────────────────
    # Pe descriere ar respinge joburi bune care doar menționează
    # „berichtest an den Director".
    for excluded in filters.get("exclude_keywords", []) or []:
        if excluded.lower() in title_l:
            return HARD_REJECT, [f"exclus: {excluded}"]

    score = 0
    reasons: list[str] = []

    for skill in skills.get("primary", []) or []:
        if _matches(title_l, skill):
            score += _W_PRIMARY_TITLE
            reasons.append(skill)
        elif _matches(text_l, skill):
            score += _W_PRIMARY_DESC
            reasons.append(skill)

    for skill in skills.get("secondary", []) or []:
        if _matches(title_l, skill):
            score += _W_SECONDARY_TITLE
            reasons.append(skill)
        elif _matches(text_l, skill):
            score += _W_SECONDARY_DESC
            reasons.append(skill)

    return min(score, 100), reasons


def passes_prefilter(job: dict, config: dict) -> bool:
    """True dacă jobul merită cheltuiala de analiză AI."""
    score, _ = prescore(job, config)
    if score == HARD_REJECT:
        return False
    filters = (config.get("scraping", {}) or {}).get("filters", {}) or {}
    return score >= int(filters.get("min_prescore", 0))
```

- [ ] **Step 4: Rulează testele ca să confirmi că trec**

```bash
source .venv/bin/activate && python -m pytest tests/test_relevance.py -q
```

Așteptat: toate testele PASS.

- [ ] **Step 5: Commit**

```bash
git add filters/ tests/test_relevance.py
git commit -m "feat(filters): prefiltru determinist înainte de analiza AI (JOB-35)"
```

---

### Task 2: Index de deduplicare cu memorie (`store/seen.py`)

Azi `_is_duplicate` compară O(n²) în JSON-ul local și nu știe nimic despre ce s-a aplicat deja.

**Files:**
- Create: `store/__init__.py`
- Create: `store/seen.py`
- Test: `tests/test_seen.py`

**Interfaces:**
- Consumes: nimic (modul frunză).
- Produces:
  - `normalize_url(url: str) -> str`
  - `url_key(job: dict) -> str`
  - `company_title_key(job: dict) -> str`
  - Constante: `RESPINS_PREFILTRU`, `ANALIZAT`, `TRIMIS_IN_SHEET`, `APLICAT` (toate `str`)
  - `class SeenIndex` cu: `load(path: Path) -> SeenIndex` (classmethod), `is_seen(job: dict) -> bool`, `mark(job: dict, decision: str) -> None`, `seed_urls(urls: Iterable[str], decision: str = APLICAT) -> int`, `filter_new(jobs: list[dict]) -> list[dict]`, `save() -> None`, `__len__() -> int`

- [ ] **Step 1: Scrie testele care eșuează**

Creează `tests/test_seen.py`:

```python
"""
Teste pentru indexul de joburi văzute — dedupe cu memorie între rulări.
"""

import json

from store.seen import (
    APLICAT,
    RESPINS_PREFILTRU,
    SeenIndex,
    company_title_key,
    normalize_url,
    url_key,
)


class TestNormalizeUrl:
    def test_lowercases_host_only(self):
        assert normalize_url("https://Example.COM/Jobs/Dev") == "https://example.com/Jobs/Dev"

    def test_strips_trailing_slash(self):
        assert normalize_url("https://example.com/jobs/") == "https://example.com/jobs"

    def test_drops_tracking_params(self):
        assert (
            normalize_url("https://example.com/j?utm_source=x&jk=abc123&from=serp")
            == "https://example.com/j?jk=abc123"
        )

    def test_keeps_meaningful_params_sorted(self):
        assert (
            normalize_url("https://example.com/j?b=2&a=1")
            == "https://example.com/j?a=1&b=2"
        )

    def test_drops_fragment(self):
        assert normalize_url("https://example.com/j#apply") == "https://example.com/j"

    def test_two_tracking_variants_collapse_to_one(self):
        a = normalize_url("https://example.com/j?jk=1&utm_source=a")
        b = normalize_url("https://example.com/j?jk=1&utm_source=b")
        assert a == b

    def test_empty_url_is_safe(self):
        assert normalize_url("") == ""


class TestKeys:
    def test_company_title_key_is_slugified(self):
        job = {"company": "Test GmbH & Co.", "title": "Python Developer (m/w/d)"}
        assert company_title_key(job) == "test-gmbh-co|python-developer-m-w-d"

    def test_url_key_uses_normalized_url(self):
        job = {"url": "https://Example.com/j/?utm_source=x"}
        assert url_key(job) == "https://example.com/j"


class TestSeenIndex:
    def test_new_index_is_empty(self, tmp_path):
        idx = SeenIndex.load(tmp_path / "seen.json")
        assert len(idx) == 0

    def test_unseen_job_is_not_seen(self, tmp_path):
        idx = SeenIndex.load(tmp_path / "seen.json")
        assert idx.is_seen({"url": "https://a.com/1", "company": "A", "title": "T"}) is False

    def test_marked_job_is_seen(self, tmp_path):
        idx = SeenIndex.load(tmp_path / "seen.json")
        job = {"url": "https://a.com/1", "company": "A", "title": "T"}
        idx.mark(job, RESPINS_PREFILTRU)
        assert idx.is_seen(job) is True

    def test_same_job_with_tracking_params_is_seen(self, tmp_path):
        idx = SeenIndex.load(tmp_path / "seen.json")
        idx.mark({"url": "https://a.com/1?jk=9", "company": "A", "title": "T"}, APLICAT)
        assert idx.is_seen({"url": "https://a.com/1?jk=9&utm_source=z", "company": "A", "title": "T"})

    def test_same_job_on_another_board_is_seen_via_company_title(self, tmp_path):
        idx = SeenIndex.load(tmp_path / "seen.json")
        idx.mark({"url": "https://boardA.com/1", "company": "ACME", "title": "Python Dev"}, APLICAT)
        assert idx.is_seen({"url": "https://boardB.com/9", "company": "ACME", "title": "Python Dev"})

    def test_persists_across_reload(self, tmp_path):
        path = tmp_path / "seen.json"
        idx = SeenIndex.load(path)
        idx.mark({"url": "https://a.com/1", "company": "A", "title": "T"}, APLICAT)
        idx.save()

        reloaded = SeenIndex.load(path)
        assert reloaded.is_seen({"url": "https://a.com/1", "company": "A", "title": "T"})

    def test_save_writes_valid_json(self, tmp_path):
        path = tmp_path / "seen.json"
        idx = SeenIndex.load(path)
        idx.mark({"url": "https://a.com/1", "company": "A", "title": "T"}, APLICAT)
        idx.save()

        data = json.loads(path.read_text(encoding="utf-8"))
        entry = data["https://a.com/1"]
        assert entry["decision"] == APLICAT
        assert "first_seen" in entry and "last_seen" in entry

    def test_mark_twice_keeps_first_seen(self, tmp_path):
        idx = SeenIndex.load(tmp_path / "seen.json")
        job = {"url": "https://a.com/1", "company": "A", "title": "T"}
        idx.mark(job, RESPINS_PREFILTRU)
        first = idx.entries["https://a.com/1"]["first_seen"]
        idx.mark(job, APLICAT)
        assert idx.entries["https://a.com/1"]["first_seen"] == first
        assert idx.entries["https://a.com/1"]["decision"] == APLICAT

    def test_seed_urls_marks_them_seen(self, tmp_path):
        idx = SeenIndex.load(tmp_path / "seen.json")
        added = idx.seed_urls(["https://a.com/1?utm_source=x", "https://b.com/2"])
        assert added == 2
        assert idx.is_seen({"url": "https://a.com/1", "company": "", "title": ""})

    def test_seed_urls_ignores_empty(self, tmp_path):
        idx = SeenIndex.load(tmp_path / "seen.json")
        assert idx.seed_urls(["", None, "  "]) == 0

    def test_filter_new_removes_seen_and_intra_batch_duplicates(self, tmp_path):
        idx = SeenIndex.load(tmp_path / "seen.json")
        idx.mark({"url": "https://a.com/1", "company": "A", "title": "T"}, APLICAT)

        batch = [
            {"url": "https://a.com/1", "company": "A", "title": "T"},   # deja văzut
            {"url": "https://a.com/2", "company": "B", "title": "U"},   # nou
            {"url": "https://a.com/2?utm_source=q", "company": "B", "title": "U"},  # dublură în lot
        ]
        fresh = idx.filter_new(batch)

        assert len(fresh) == 1
        assert fresh[0]["url"] == "https://a.com/2"

    def test_filter_new_does_not_mark(self, tmp_path):
        """filter_new doar filtrează; marcarea e decizia apelantului."""
        idx = SeenIndex.load(tmp_path / "seen.json")
        job = {"url": "https://a.com/1", "company": "A", "title": "T"}
        idx.filter_new([job])
        assert idx.is_seen(job) is False

    def test_corrupt_file_starts_empty(self, tmp_path):
        path = tmp_path / "seen.json"
        path.write_text("{ nu e json valid", encoding="utf-8")
        assert len(SeenIndex.load(path)) == 0
```

- [ ] **Step 2: Rulează testele ca să confirmi că eșuează**

```bash
source .venv/bin/activate && python -m pytest tests/test_seen.py -q
```

Așteptat: `ModuleNotFoundError: No module named 'store'`.

- [ ] **Step 3: Implementează modulul**

Creează `store/__init__.py` gol, apoi `store/seen.py`:

```python
"""
Index persistent de joburi deja văzute.

Rezolvă două probleme ale variantei vechi: comparația O(n²) peste tot
fișierul de joburi și faptul că joburile deja aplicate reapăreau ca noi,
pentru că nimic nu se uita la Sheet.
"""

import json
import re
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

# Deciziile posibile pentru un job văzut.
RESPINS_PREFILTRU = "respins_prefiltru"
ANALIZAT = "analizat"
TRIMIS_IN_SHEET = "trimis_in_sheet"
APLICAT = "aplicat"

# Parametri de URL care nu identifică jobul, doar de unde ai venit.
# NU se aruncă tot query string-ul: pe Indeed id-ul jobului stă în `jk`.
_TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "gh_src", "from", "src", "ref", "referrer", "source", "trk", "trkinfo",
}


def normalize_url(url: str) -> str:
    """Aduce URL-ul la o formă canonică, ca aceeași ofertă să dea aceeași cheie."""
    if not url or not str(url).strip():
        return ""
    parts = urlsplit(str(url).strip())
    query = urlencode(
        sorted((k, v) for k, v in parse_qsl(parts.query) if k.lower() not in _TRACKING_PARAMS)
    )
    path = parts.path.rstrip("/")
    return urlunsplit((parts.scheme, parts.netloc.lower(), path, query, ""))


def _slug(value: str) -> str:
    """Normalizează un text pentru comparație: minuscule, doar litere/cifre/cratime."""
    slug = re.sub(r"[^a-z0-9]+", "-", str(value or "").lower())
    return slug.strip("-")


def url_key(job: dict) -> str:
    """Cheia primară: URL-ul normalizat."""
    return normalize_url(job.get("url", ""))


def company_title_key(job: dict) -> str:
    """Cheia secundară: prinde același job postat pe două board-uri diferite."""
    company = _slug(job.get("company", ""))
    title = _slug(job.get("title", ""))
    if not company or not title:
        return ""
    return f"{company}|{title}"


class SeenIndex:
    """Set persistent de chei de joburi văzute, cu decizia luată pentru fiecare."""

    def __init__(self, path: Path, entries: dict | None = None):
        self.path = Path(path)
        self.entries: dict = entries or {}

    @classmethod
    def load(cls, path: Path) -> "SeenIndex":
        """Încarcă indexul; un fișier corupt sau lipsă dă un index gol.

        Un index pierdut înseamnă doar că vezi din nou niște joburi vechi —
        nu merită să oprească pipeline-ul.
        """
        path = Path(path)
        if not path.exists():
            return cls(path)
        try:
            with open(path, "r", encoding="utf-8") as f:
                return cls(path, json.load(f))
        except (json.JSONDecodeError, OSError):
            return cls(path)

    def __len__(self) -> int:
        return len(self.entries)

    def _keys(self, job: dict) -> list[str]:
        return [k for k in (url_key(job), company_title_key(job)) if k]

    def is_seen(self, job: dict) -> bool:
        return any(k in self.entries for k in self._keys(job))

    def mark(self, job: dict, decision: str) -> None:
        """Marchează jobul cu o decizie, păstrând `first_seen` la prima vedere."""
        now = datetime.now().isoformat(timespec="seconds")
        for key in self._keys(job):
            existing = self.entries.get(key)
            self.entries[key] = {
                "first_seen": existing["first_seen"] if existing else now,
                "last_seen": now,
                "decision": decision,
            }

    def seed_urls(self, urls: Iterable[str], decision: str = APLICAT) -> int:
        """Alimentează indexul cu URL-uri din Sheet. Întoarce câte au fost adăugate."""
        added = 0
        for url in urls:
            key = normalize_url(url or "")
            if not key or key in self.entries:
                continue
            self.mark({"url": key}, decision)
            added += 1
        return added

    def filter_new(self, jobs: list[dict]) -> list[dict]:
        """Întoarce doar joburile nevăzute, eliminând și dublurile din lot.

        NU marchează nimic: decizia despre ce s-a întâmplat cu jobul aparține
        apelantului, care abia după prefiltru știe ce să scrie.
        """
        fresh: list[dict] = []
        batch_keys: set[str] = set()
        for job in jobs:
            keys = self._keys(job)
            if not keys:
                continue
            if self.is_seen(job) or any(k in batch_keys for k in keys):
                continue
            batch_keys.update(keys)
            fresh.append(job)
        return fresh

    def save(self) -> None:
        """Scrie indexul pe disc, creând directorul dacă lipsește."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(self.entries, f, indent=2, ensure_ascii=False)
```

- [ ] **Step 4: Rulează testele ca să confirmi că trec**

```bash
source .venv/bin/activate && python -m pytest tests/test_seen.py -q
```

Așteptat: toate testele PASS.

- [ ] **Step 5: Commit**

```bash
git add store/ tests/test_seen.py
git commit -m "feat(store): index de dedupe cu memorie între rulări (JOB-36)"
```

---

### Task 3: Conceptul de piață (`scrapers/base.py` + config)

**Files:**
- Create: `scrapers/base.py`
- Modify: `config/settings.yaml` (blocul `scraping`, liniile ~42-77)
- Test: `tests/test_market.py`

**Interfaces:**
- Consumes: nimic intern.
- Produces:
  - `@dataclass(frozen=True) class Market` cu câmpurile: `key: str`, `country: str`, `language: str`, `locations: list[str]`, `remote: bool`, `keyword_groups: list[list[str]]`, `sources: list[str]`, `best_effort_sources: tuple[str, ...]`
  - `Market.from_config(config: dict) -> list[Market]` (staticmethod)
  - `make_job(*, source: str, platform: str, title: str, company: str, location: str, url: str, description: str, market: Market, date_posted: str = "", salary_min=None, salary_max=None, email_hr: str = "") -> dict`
  - `save_jobs(jobs: list[dict], path: Path) -> Path`

- [ ] **Step 1: Scrie testele care eșuează**

Creează `tests/test_market.py`:

```python
"""
Teste pentru conceptul de piață și normalizarea comună a jobului.
"""

import json

import pytest

from scrapers.base import Market, make_job, save_jobs


CFG = {
    "scraping": {
        "markets": {
            "de": {
                "country": "GERMANY",
                "language": "de",
                "locations": ["Germany"],
                "remote": False,
                "keyword_groups": [["Python Developer", "Django Developer"]],
                "sources": ["jobspy", "career_pages"],
            },
            "ro_remote": {
                "country": "ROMANIA",
                "language": "en",
                "locations": ["Romania"],
                "remote": True,
                "keyword_groups": [["Python Developer"], ["AI Engineer"]],
                "sources": ["jobspy", "remote_boards", "ro_boards"],
                "best_effort_sources": ["ro_boards"],
            },
        }
    }
}


class TestMarketFromConfig:
    def test_parses_all_markets(self):
        markets = Market.from_config(CFG)
        assert {m.key for m in markets} == {"de", "ro_remote"}

    def test_parses_fields(self):
        ro = next(m for m in Market.from_config(CFG) if m.key == "ro_remote")
        assert ro.country == "ROMANIA"
        assert ro.language == "en"
        assert ro.remote is True
        assert ro.keyword_groups == [["Python Developer"], ["AI Engineer"]]
        assert "ro_boards" in ro.sources

    def test_best_effort_defaults_to_empty(self):
        de = next(m for m in Market.from_config(CFG) if m.key == "de")
        assert de.best_effort_sources == ()

    def test_best_effort_is_parsed(self):
        ro = next(m for m in Market.from_config(CFG) if m.key == "ro_remote")
        assert ro.best_effort_sources == ("ro_boards",)

    def test_missing_markets_block_returns_empty(self):
        assert Market.from_config({"scraping": {}}) == []

    def test_market_is_immutable(self):
        de = next(m for m in Market.from_config(CFG) if m.key == "de")
        with pytest.raises(Exception):
            de.country = "FRANCE"


class TestMakeJob:
    @pytest.fixture
    def market(self):
        return next(m for m in Market.from_config(CFG) if m.key == "ro_remote")

    def test_sets_language_from_market(self, market):
        job = make_job(
            source="remoteok", platform="remoteok", title="Python Dev",
            company="ACME", location="Remote", url="https://x.com/1",
            description="desc", market=market,
        )
        assert job["language"] == "en"

    def test_sets_market_key(self, market):
        job = make_job(
            source="remoteok", platform="remoteok", title="T", company="C",
            location="L", url="https://x.com/1", description="d", market=market,
        )
        assert job["market"] == "ro_remote"

    def test_gefunden_am_is_iso_date(self, market):
        job = make_job(
            source="remoteok", platform="remoteok", title="T", company="C",
            location="L", url="https://x.com/1", description="d", market=market,
        )
        assert len(job["gefunden_am"]) == 10
        assert job["gefunden_am"][4] == "-"

    def test_status_is_new(self, market):
        """`new` NU e o stare din col M; n8n_mapping îl filtrează la push."""
        job = make_job(
            source="remoteok", platform="remoteok", title="T", company="C",
            location="L", url="https://x.com/1", description="d", market=market,
        )
        assert job["status"] == "new"

    def test_description_is_truncated(self, market):
        job = make_job(
            source="remoteok", platform="remoteok", title="T", company="C",
            location="L", url="https://x.com/1", description="x" * 9000,
            market=market,
        )
        assert len(job["description"]) == 5000

    def test_strips_whitespace(self, market):
        job = make_job(
            source="remoteok", platform="remoteok", title="  Python Dev \n",
            company=" ACME ", location="L", url=" https://x.com/1 ",
            description="d", market=market,
        )
        assert job["title"] == "Python Dev"
        assert job["company"] == "ACME"
        assert job["url"] == "https://x.com/1"


class TestSaveJobs:
    def test_writes_json(self, tmp_path, ):
        market = next(m for m in Market.from_config(CFG) if m.key == "de")
        job = make_job(
            source="jobspy", platform="indeed", title="T", company="C",
            location="L", url="https://x.com/1", description="d", market=market,
        )
        path = save_jobs([job], tmp_path / "sub" / "jobs.json")

        assert path.exists()
        assert json.loads(path.read_text(encoding="utf-8"))[0]["title"] == "T"

    def test_overwrites_instead_of_appending(self, tmp_path):
        """Persistența istorică e treaba lui store/seen.py, nu a acestui fișier."""
        market = next(m for m in Market.from_config(CFG) if m.key == "de")
        path = tmp_path / "jobs.json"
        for title in ("A", "B"):
            job = make_job(
                source="jobspy", platform="indeed", title=title, company="C",
                location="L", url="https://x.com/1", description="d", market=market,
            )
            save_jobs([job], path)

        data = json.loads(path.read_text(encoding="utf-8"))
        assert len(data) == 1
        assert data[0]["title"] == "B"
```

- [ ] **Step 2: Rulează testele ca să confirmi că eșuează**

```bash
source .venv/bin/activate && python -m pytest tests/test_market.py -q
```

Așteptat: `ImportError: cannot import name 'Market' from 'scrapers.base'`.

- [ ] **Step 3: Implementează `scrapers/base.py`**

```python
"""
Fundația comună a scraperelor: conceptul de piață și forma canonică a jobului.

Sursele primesc un `Market` și nu știu nimic despre țări. O piață nouă se
adaugă editând YAML-ul, fără cod nou.
"""

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

# Descrierile lungi umflă payload-ul spre AI fără să adauge informație.
_MAX_DESCRIPTION_CHARS = 5000


@dataclass(frozen=True)
class Market:
    """O piață de căutat: țară, limbă, keywords și surse proprii."""

    key: str
    country: str
    language: str
    locations: list[str] = field(default_factory=list)
    remote: bool = False
    keyword_groups: list[list[str]] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    # Surse al căror eșec NU trebuie să marcheze runul ca FAILED
    # (ex. scraping HTML pe site-uri fără API, care se strică la redesign).
    best_effort_sources: tuple[str, ...] = ()

    @staticmethod
    def from_config(config: dict) -> list["Market"]:
        """Citește `scraping.markets` din settings.yaml."""
        markets_cfg = (config.get("scraping", {}) or {}).get("markets", {}) or {}
        markets = []
        for key, cfg in markets_cfg.items():
            markets.append(
                Market(
                    key=key,
                    country=str(cfg.get("country", "")),
                    language=str(cfg.get("language", "en")),
                    locations=list(cfg.get("locations", []) or []),
                    remote=bool(cfg.get("remote", False)),
                    keyword_groups=[list(g) for g in (cfg.get("keyword_groups", []) or [])],
                    sources=list(cfg.get("sources", []) or []),
                    best_effort_sources=tuple(cfg.get("best_effort_sources", []) or []),
                )
            )
        return markets


def make_job(
    *,
    source: str,
    platform: str,
    title: str,
    company: str,
    location: str,
    url: str,
    description: str,
    market: Market,
    date_posted: str = "",
    salary_min=None,
    salary_max=None,
    email_hr: str = "",
) -> dict:
    """Construiește forma canonică a unui job, indiferent de sursă.

    `language` vine din piață și ajunge în col „Sprache" din Sheet, care
    comandă limba scrisorii — de asta un job din RO nu primește Anschreiben
    în germană.
    """
    now = datetime.now()
    return {
        "id": f"{source}_{abs(hash(str(url).strip()))}",
        "source": source,
        "platform": platform,
        "market": market.key,
        "language": market.language,
        "title": str(title or "").strip(),
        "company": str(company or "").strip(),
        "location": str(location or "").strip(),
        "url": str(url or "").strip(),
        "description": str(description or "")[:_MAX_DESCRIPTION_CHARS],
        "email_hr": str(email_hr or "").strip(),
        "date_posted": str(date_posted or ""),
        "salary_min": salary_min,
        "salary_max": salary_max,
        "scraped_at": now.isoformat(),
        "gefunden_am": now.strftime("%Y-%m-%d"),
        "applied": False,
        # `new` nu face parte din mașina de stări a col M; `n8n_mapping`
        # îl filtrează la push, ca să nu suprascrie rânduri deja `done`.
        "status": "new",
    }


def save_jobs(jobs: list[dict], path: Path) -> Path:
    """Scrie rezultatele rulării curente. NU face merge cu ce era înainte —
    memoria între rulări e treaba lui `store/seen.py`."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(jobs, f, indent=2, ensure_ascii=False, default=str)
    return path
```

- [ ] **Step 4: Rulează testele ca să confirmi că trec**

```bash
source .venv/bin/activate && python -m pytest tests/test_market.py -q
```

Așteptat: toate testele PASS.

- [ ] **Step 5: Rescrie blocul `scraping` din `config/settings.yaml`**

Înlocuiește integral blocul `# ── Scraping ──` (de la `scraping:` până înainte de `# ── AI / LLM ──`) cu:

```yaml
# ── Scraping ────────────────────────────────────────────────
scraping:
  # Intervall între scraping runs (minute)
  interval_minutes: 360
  # Max joburi per apel de scraping
  max_results_per_call: 50
  # Vechimea maximă a anunțului (ore). La interval de 6h, 168h însemna
  # re-descărcarea acelorași joburi de ~28 de ori.
  hours_old: 72

  # ── Piețe ─────────────────────────────────────────────────
  # O piață = țară + limbă + keywords + surse. Codul primește un obiect
  # Market; adăugarea unei țări noi se face aici, fără cod nou.
  markets:
    de:
      country: "GERMANY"          # enum jobspy Country
      language: "de"              # → col „Sprache" → limba Anschreiben
      locations: ["Germany"]      # la nivel de țară, NU pe orașe:
                                  # Indeed întoarce oricum toate orașele
      remote: false
      keyword_groups:
        - ["Python Developer", "Django Developer", "Backend Engineer"]
        - ["AI Engineer", "LLM Engineer", "Machine Learning Engineer"]
        - ["Data Engineer", "Automation Engineer"]
      sources: ["jobspy", "career_pages"]
      best_effort_sources: ["linkedin"]

    ro_remote:
      country: "ROMANIA"
      language: "en"              # scrisoare EN, nu Anschreiben german
      locations: ["Romania"]
      remote: true                # → jobspy is_remote=True
      keyword_groups:
        - ["Python Developer", "Django Developer"]
        - ["AI Engineer", "LLM Engineer"]
      sources: ["jobspy", "remote_boards", "ro_boards"]
      best_effort_sources: ["ro_boards", "linkedin"]

  # Site-uri jobspy folosite per piață
  jobspy_sites: ["indeed", "linkedin"]

  # Filtre — citite de filters/relevance.py
  filters:
    exclude_keywords:
      - "Senior Staff"
      - "Principal"
      - "Director"
      - "VP"
      - "Head of"
    min_salary: 0
    # Sub acest scor local jobul NU ajunge la AI
    min_prescore: 40
```

- [ ] **Step 6: Verifică faptul că YAML-ul se încarcă și piețele se parsează**

```bash
source .venv/bin/activate && python3 -c "
from agents.base_agent import load_config
from scrapers.base import Market
ms = Market.from_config(load_config())
for m in ms:
    print(m.key, m.country, m.language, 'remote=' + str(m.remote), len(m.keyword_groups), 'grupuri')
"
```

Așteptat, exact două linii:
```
de GERMANY de remote=False 3 grupuri
ro_remote ROMANIA en remote=True 2 grupuri
```

- [ ] **Step 7: Commit**

```bash
git add scrapers/base.py config/settings.yaml tests/test_market.py
git commit -m "feat(scraping): piețe configurabile DE + RO remote (JOB-37)"
```

---

### Task 4: Rescrierea `JobSpyScraper` pe piețe

Bucla `keywords × locations` (49 apeluri) devine `keyword_groups × sites` (~12), rulate în paralel.

**Files:**
- Modify: `scrapers/jobspy_scraper.py` (rescriere integrală)
- Modify: `tests/test_jobspy_scraper.py` (semnătura `_normalize_job` se schimbă)

**Interfaces:**
- Consumes: `scrapers.base.Market`, `scrapers.base.make_job`
- Produces:
  - `class JobSpyScraper` cu: `__init__(config: dict, project_root: Path)`, `scrape_market(market: Market) -> tuple[list[dict], list[str]]` (întoarce `(joburi, erori)`), `_normalize_job(row, market: Market) -> dict | None`

- [ ] **Step 1: Rescrie testele**

Înlocuiește integral `tests/test_jobspy_scraper.py`:

```python
"""
Teste pentru JobSpyScraper — jobspy e mock-uit, nicio conexiune reală.
"""

import re
from unittest.mock import patch

import pytest

from scrapers.base import Market
from scrapers.jobspy_scraper import JobSpyScraper


CFG = {
    "scraping": {
        "max_results_per_call": 10,
        "hours_old": 72,
        "jobspy_sites": ["indeed", "linkedin"],
        "markets": {
            "de": {
                "country": "GERMANY", "language": "de", "locations": ["Germany"],
                "remote": False,
                "keyword_groups": [["Python Developer", "Django Developer"]],
                "sources": ["jobspy"],
            },
            "ro_remote": {
                "country": "ROMANIA", "language": "en", "locations": ["Romania"],
                "remote": True,
                "keyword_groups": [["Python Developer"]],
                "sources": ["jobspy"],
            },
        },
    }
}


class FakeDataFrame:
    """Imită doar ce folosește scraperul din pandas: `empty` și `iterrows`."""

    def __init__(self, rows):
        self._rows = rows

    @property
    def empty(self):
        return not self._rows

    def iterrows(self):
        return enumerate(self._rows)


def _market(key="de"):
    return next(m for m in Market.from_config(CFG) if m.key == key)


ROW = {
    "job_url": "https://example.com/job/1",
    "title": "Python Developer",
    "company": "TestCo",
    "site": "indeed",
    "location": "Berlin",
    "description": "Python and Django backend.",
}


class TestNormalizeJob:
    def test_includes_gefunden_am(self, tmp_path):
        scraper = JobSpyScraper(CFG, tmp_path)
        job = scraper._normalize_job(ROW, _market("de"))
        assert job is not None
        assert re.match(r"^\d{4}-\d{2}-\d{2}$", job["gefunden_am"])

    def test_language_comes_from_market(self, tmp_path):
        scraper = JobSpyScraper(CFG, tmp_path)
        assert scraper._normalize_job(ROW, _market("de"))["language"] == "de"
        assert scraper._normalize_job(ROW, _market("ro_remote"))["language"] == "en"

    def test_source_encodes_platform_and_market(self, tmp_path):
        """Col „Quelle" trebuie să spună de unde și din ce piață vine jobul."""
        scraper = JobSpyScraper(CFG, tmp_path)
        job = scraper._normalize_job(ROW, _market("ro_remote"))
        assert job["source"] == "indeed-ro_remote"

    def test_row_without_url_is_dropped(self, tmp_path):
        """Fără URL nu există cheie de upsert în Sheet — jobul e inutil."""
        scraper = JobSpyScraper(CFG, tmp_path)
        assert scraper._normalize_job({**ROW, "job_url": ""}, _market("de")) is None


class TestScrapeMarket:
    def test_passes_country_and_remote_from_market(self, tmp_path):
        scraper = JobSpyScraper(CFG, tmp_path)
        with patch("scrapers.jobspy_scraper.scrape_jobs") as mock:
            mock.return_value = FakeDataFrame([])
            scraper.scrape_market(_market("ro_remote"))

        kwargs = mock.call_args.kwargs
        assert kwargs["country_indeed"] == "ROMANIA"
        assert kwargs["is_remote"] is True
        assert kwargs["location"] == "Romania"
        assert kwargs["hours_old"] == 72

    def test_keyword_group_becomes_boolean_query(self, tmp_path):
        scraper = JobSpyScraper(CFG, tmp_path)
        with patch("scrapers.jobspy_scraper.scrape_jobs") as mock:
            mock.return_value = FakeDataFrame([])
            scraper.scrape_market(_market("de"))

        term = mock.call_args.kwargs["search_term"]
        assert '"Python Developer" OR "Django Developer"' == term

    def test_one_call_per_group_times_site(self, tmp_path):
        """1 grup × 2 site-uri = 2 apeluri, nu keywords × locații."""
        scraper = JobSpyScraper(CFG, tmp_path)
        with patch("scrapers.jobspy_scraper.scrape_jobs") as mock:
            mock.return_value = FakeDataFrame([])
            scraper.scrape_market(_market("de"))

        assert mock.call_count == 2

    def test_returns_normalized_jobs(self, tmp_path):
        scraper = JobSpyScraper(CFG, tmp_path)
        with patch("scrapers.jobspy_scraper.scrape_jobs") as mock:
            mock.return_value = FakeDataFrame([ROW])
            jobs, errors = scraper.scrape_market(_market("de"))

        assert errors == []
        assert all(j["title"] == "Python Developer" for j in jobs)

    def test_failing_site_is_reported_not_raised(self, tmp_path):
        scraper = JobSpyScraper(CFG, tmp_path)

        def boom(**kwargs):
            if kwargs["site_name"] == ["linkedin"]:
                raise RuntimeError("429 Too Many Requests")
            return FakeDataFrame([ROW])

        with patch("scrapers.jobspy_scraper.scrape_jobs", side_effect=boom):
            jobs, errors = scraper.scrape_market(_market("de"))

        assert len(jobs) >= 1                      # indeed a mers
        assert len(errors) == 1                    # linkedin a căzut
        assert "linkedin" in errors[0]
        assert "429" in errors[0]

    def test_missing_jobspy_raises_import_error(self, tmp_path):
        with patch("scrapers.jobspy_scraper.HAS_JOBSPY", False):
            scraper = JobSpyScraper(CFG, tmp_path)
            with pytest.raises(ImportError):
                scraper.scrape_market(_market("de"))
```

- [ ] **Step 2: Rulează testele ca să confirmi că eșuează**

```bash
source .venv/bin/activate && python -m pytest tests/test_jobspy_scraper.py -q
```

Așteptat: eșec la `scraper.scrape_market` (`AttributeError`) și la importul `scrape_jobs` din modul.

- [ ] **Step 3: Rescrie `scrapers/jobspy_scraper.py`**

```python
"""
JobSpy Scraper — agregare multi-platformă, organizată pe piețe.

Varianta veche făcea produs cartezian keywords × locații (49 de apeluri
pentru 7 keywords și 7 orașe), din care majoritatea se loveau reciproc de
rate-limit. Acum: un apel per (grup de keywords, site), rulate în paralel.
"""

from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from scrapers.base import Market, make_job

try:
    from jobspy import scrape_jobs
    HAS_JOBSPY = True
except ImportError:
    scrape_jobs = None
    HAS_JOBSPY = False

# Câte apeluri jobspy rulează simultan. Peste 4 crește rata de 429.
_MAX_WORKERS = 4


class JobSpyScraper:
    """Scraper bazat pe python-jobspy, cu o piață ca unitate de lucru."""

    def __init__(self, config: dict, project_root: Path):
        self.config = config
        self.project_root = project_root
        self.scrape_config = config.get("scraping", {}) or {}

    def scrape_market(self, market: Market) -> tuple[list[dict], list[str]]:
        """Caută joburi pentru o piață.

        Întoarce `(joburi, erori)`. Erorile se întorc, nu se ridică: un site
        blocat nu trebuie să anuleze rezultatele celorlalte.
        """
        if not HAS_JOBSPY:
            raise ImportError(
                "python-jobspy nu este instalat. Rulează: pip install python-jobspy"
            )

        sites = self.scrape_config.get("jobspy_sites", ["indeed"])
        location = market.locations[0] if market.locations else market.country

        units = [(group, site) for group in market.keyword_groups for site in sites]

        jobs: list[dict] = []
        errors: list[str] = []

        with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as pool:
            futures = {
                pool.submit(self._call, market, group, site, location): (group, site)
                for group, site in units
            }
            for future in as_completed(futures):
                group, site = futures[future]
                try:
                    jobs.extend(future.result())
                except Exception as e:
                    errors.append(f"[{market.key}/{site}] {group[0]}: {e}")

        return jobs, errors

    def _call(self, market: Market, group: list[str], site: str, location: str) -> list[dict]:
        """Un singur apel jobspy. Excepțiile urcă — le prinde `scrape_market`."""
        # Grupul de keywords devine o interogare booleană: un apel în loc de
        # câte unul per keyword.
        search_term = " OR ".join(f'"{k}"' for k in group)

        df = scrape_jobs(
            site_name=[site],
            search_term=search_term,
            location=location,
            results_wanted=int(self.scrape_config.get("max_results_per_call", 50)),
            hours_old=int(self.scrape_config.get("hours_old", 72)),
            country_indeed=market.country,
            is_remote=market.remote,
        )

        if df is None or df.empty:
            return []

        jobs = []
        for _, row in df.iterrows():
            job = self._normalize_job(row, market)
            if job:
                jobs.append(job)
        return jobs

    def _normalize_job(self, row: Any, market: Market) -> dict | None:
        """Transformă un rând jobspy în forma canonică."""
        try:
            url = str(row.get("job_url", "") or "").strip()
            # Fără URL nu există cheie de match la upsert („Apply URL"):
            # n8n ar cădea pe append și ar adăuga rânduri goale.
            if not url:
                return None

            platform = str(row.get("site", "unknown"))
            return make_job(
                source=f"{platform}-{market.key}",
                platform=platform,
                title=str(row.get("title", "")),
                company=str(row.get("company", "")),
                location=str(row.get("location", "") or market.country),
                url=url,
                description=str(row.get("description", "") or ""),
                market=market,
                date_posted=str(row.get("date_posted", "") or ""),
                salary_min=row.get("min_amount"),
                salary_max=row.get("max_amount"),
            )
        except Exception:
            return None
```

- [ ] **Step 4: Rulează testele ca să confirmi că trec**

```bash
source .venv/bin/activate && python -m pytest tests/test_jobspy_scraper.py -q
```

Așteptat: toate testele PASS.

- [ ] **Step 5: Commit**

```bash
git add scrapers/jobspy_scraper.py tests/test_jobspy_scraper.py
git commit -m "refactor(jobspy): un apel per grup de keywords, paralel, pe piață (JOB-38)"
```

---

### Task 5: Board-uri remote cu API public (`scrapers/remote_boards.py`)

Sursa cea mai stabilă din plan: RemoteOK și Remotive au JSON public, WeWorkRemotely are RSS. Zero scraping HTML.

**Securitate — de ce `defusedxml` și nu stdlib.** Feed-ul WWR e XML din sursă externă, deci input neîncredere. `xml.etree.ElementTree` din stdlib e vulnerabil la expansiune de entități („billion laughs" și quadratic blowup): un feed ostil sau compromis poate epuiza memoria procesului. `defusedxml` e un înlocuitor drop-in care refuză entitățile. Costă o dependență mică și o linie de import.

**Files:**
- Create: `scrapers/remote_boards.py`
- Modify: `requirements.txt` (adaugă `defusedxml`)
- Create: `tests/fixtures/remoteok_sample.json`
- Create: `tests/fixtures/remotive_sample.json`
- Create: `tests/fixtures/wwr_sample.xml`
- Test: `tests/test_remote_boards.py`

**Interfaces:**
- Consumes: `scrapers.base.Market`, `scrapers.base.make_job`
- Produces:
  - `class RemoteBoardsScraper` cu: `__init__(config: dict, project_root: Path)`, `scrape_market(market: Market) -> tuple[list[dict], list[str]]`, `_parse_remoteok(payload: list, market: Market) -> list[dict]`, `_parse_remotive(payload: dict, market: Market) -> list[dict]`, `_parse_wwr(xml_text: str, market: Market) -> list[dict]`

- [ ] **Step 1: Adaugă dependența `defusedxml`**

În `requirements.txt`, în secțiunea `# Scraping`, sub `lxml>=5.0.0`, adaugă:

```
# Parsare XML sigură pentru feed-uri RSS externe (stdlib ElementTree e
# vulnerabil la expansiune de entități)
defusedxml>=0.7.1
```

Instalează:

```bash
source .venv/bin/activate && pip install "defusedxml>=0.7.1"
```

- [ ] **Step 2: Creează fixture-urile**

Creează directorul și `tests/fixtures/remoteok_sample.json` — primul element e nota legală pe care API-ul RemoteOK o pune mereu pe poziția 0 și care trebuie sărită:

```json
[
  {
    "legal": "Please note that this data is for personal use only."
  },
  {
    "id": "1001",
    "position": "Senior Python Developer",
    "company": "Acme Remote",
    "location": "Europe",
    "url": "https://remoteok.com/remote-jobs/1001",
    "description": "<p>We need <b>Python</b> and Django experience.</p>",
    "date": "2026-07-20T10:00:00+00:00",
    "tags": ["python", "django"]
  },
  {
    "id": "1002",
    "position": "AI Engineer",
    "company": "Beta Labs",
    "location": "Worldwide",
    "url": "",
    "description": "LLM work.",
    "date": "2026-07-21T10:00:00+00:00",
    "tags": ["ai"]
  }
]
```

Creează `tests/fixtures/remotive_sample.json`:

```json
{
  "jobs": [
    {
      "id": 2001,
      "title": "Django Developer",
      "company_name": "Gamma SRL",
      "candidate_required_location": "Romania",
      "url": "https://remotive.com/remote-jobs/2001",
      "description": "<p>Django &amp; PostgreSQL</p>",
      "publication_date": "2026-07-19T08:00:00"
    }
  ]
}
```

Creează `tests/fixtures/wwr_sample.xml`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>We Work Remotely</title>
    <item>
      <title>Delta Inc: Backend Engineer (Python)</title>
      <link>https://weworkremotely.com/remote-jobs/3001</link>
      <description>&lt;p&gt;Python backend, remote EU.&lt;/p&gt;</description>
      <pubDate>Mon, 20 Jul 2026 09:00:00 +0000</pubDate>
    </item>
  </channel>
</rss>
```

- [ ] **Step 3: Scrie testele care eșuează**

Creează `tests/test_remote_boards.py`:

```python
"""
Teste pentru board-urile remote — se parsează fixture-uri salvate, fără rețea.
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from scrapers.base import Market
from scrapers.remote_boards import RemoteBoardsScraper


FIXTURES = Path(__file__).parent / "fixtures"

CFG = {
    "scraping": {
        "markets": {
            "ro_remote": {
                "country": "ROMANIA", "language": "en", "locations": ["Romania"],
                "remote": True,
                "keyword_groups": [["Python Developer"], ["AI Engineer"]],
                "sources": ["remote_boards"],
            }
        }
    }
}


def _market():
    return Market.from_config(CFG)[0]


def _scraper(tmp_path):
    return RemoteBoardsScraper(CFG, tmp_path)


class TestParseRemoteOK:
    def test_skips_legal_notice_element(self, tmp_path):
        payload = json.loads((FIXTURES / "remoteok_sample.json").read_text())
        jobs = _scraper(tmp_path)._parse_remoteok(payload, _market())
        assert all(j["title"] for j in jobs)
        assert not any("personal use" in j["description"] for j in jobs)

    def test_drops_entry_without_url(self, tmp_path):
        payload = json.loads((FIXTURES / "remoteok_sample.json").read_text())
        jobs = _scraper(tmp_path)._parse_remoteok(payload, _market())
        assert len(jobs) == 1
        assert jobs[0]["title"] == "Senior Python Developer"

    def test_strips_html_from_description(self, tmp_path):
        payload = json.loads((FIXTURES / "remoteok_sample.json").read_text())
        job = _scraper(tmp_path)._parse_remoteok(payload, _market())[0]
        assert "<p>" not in job["description"]
        assert "Python" in job["description"]

    def test_sets_platform_and_language(self, tmp_path):
        payload = json.loads((FIXTURES / "remoteok_sample.json").read_text())
        job = _scraper(tmp_path)._parse_remoteok(payload, _market())[0]
        assert job["platform"] == "remoteok"
        assert job["language"] == "en"
        assert job["market"] == "ro_remote"


class TestParseRemotive:
    def test_maps_fields(self, tmp_path):
        payload = json.loads((FIXTURES / "remotive_sample.json").read_text())
        job = _scraper(tmp_path)._parse_remotive(payload, _market())[0]
        assert job["title"] == "Django Developer"
        assert job["company"] == "Gamma SRL"
        assert job["location"] == "Romania"
        assert job["url"] == "https://remotive.com/remote-jobs/2001"

    def test_unescapes_html_entities(self, tmp_path):
        payload = json.loads((FIXTURES / "remotive_sample.json").read_text())
        job = _scraper(tmp_path)._parse_remotive(payload, _market())[0]
        assert "&amp;" not in job["description"]
        assert "Django & PostgreSQL" in job["description"]


class TestParseWwr:
    def test_splits_company_from_title(self, tmp_path):
        xml = (FIXTURES / "wwr_sample.xml").read_text()
        job = _scraper(tmp_path)._parse_wwr(xml, _market())[0]
        assert job["company"] == "Delta Inc"
        assert job["title"] == "Backend Engineer (Python)"

    def test_maps_link(self, tmp_path):
        xml = (FIXTURES / "wwr_sample.xml").read_text()
        job = _scraper(tmp_path)._parse_wwr(xml, _market())[0]
        assert job["url"] == "https://weworkremotely.com/remote-jobs/3001"

    def test_malformed_xml_returns_empty(self, tmp_path):
        assert _scraper(tmp_path)._parse_wwr("<rss><nu", _market()) == []

    def test_entity_expansion_attack_returns_empty(self, tmp_path):
        """Un feed ostil („billion laughs") nu trebuie să epuizeze memoria."""
        bomb = """<?xml version="1.0"?>
        <!DOCTYPE rss [
          <!ENTITY a "aaaaaaaaaa">
          <!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;">
          <!ENTITY c "&b;&b;&b;&b;&b;&b;&b;&b;&b;&b;">
        ]>
        <rss><channel><item><title>&c;</title><link>http://x</link></item></channel></rss>"""
        assert _scraper(tmp_path)._parse_wwr(bomb, _market()) == []


class TestScrapeMarket:
    def test_failing_board_is_reported_not_raised(self, tmp_path):
        with patch("scrapers.remote_boards.httpx.get", side_effect=RuntimeError("503")):
            jobs, errors = _scraper(tmp_path).scrape_market(_market())

        assert jobs == []
        assert len(errors) == 3          # remoteok + remotive + wwr
        assert all("503" in e for e in errors)

    def test_successful_boards_return_jobs(self, tmp_path):
        remoteok = json.loads((FIXTURES / "remoteok_sample.json").read_text())
        remotive = json.loads((FIXTURES / "remotive_sample.json").read_text())
        wwr = (FIXTURES / "wwr_sample.xml").read_text()

        def fake_get(url, **kwargs):
            resp = MagicMock()
            resp.raise_for_status.return_value = None
            if "remoteok" in url:
                resp.json.return_value = remoteok
            elif "remotive" in url:
                resp.json.return_value = remotive
            else:
                resp.text = wwr
            return resp

        with patch("scrapers.remote_boards.httpx.get", side_effect=fake_get):
            jobs, errors = _scraper(tmp_path).scrape_market(_market())

        assert errors == []
        assert {j["platform"] for j in jobs} == {"remoteok", "remotive", "weworkremotely"}

    def test_deduplicates_within_board_results(self, tmp_path):
        """Aceeași ofertă întoarsă de două interogări intră o singură dată."""
        remoteok = json.loads((FIXTURES / "remoteok_sample.json").read_text())
        scraper = _scraper(tmp_path)
        jobs = scraper._parse_remoteok(remoteok + remoteok, _market())
        assert len(jobs) == 1
```

- [ ] **Step 4: Rulează testele ca să confirmi că eșuează**

```bash
source .venv/bin/activate && python -m pytest tests/test_remote_boards.py -q
```

Așteptat: `ModuleNotFoundError: No module named 'scrapers.remote_boards'`.

- [ ] **Step 5: Implementează `scrapers/remote_boards.py`**

```python
"""
Board-uri remote cu acces public — RemoteOK, Remotive, WeWorkRemotely.

Cea mai stabilă categorie de surse din pipeline: primele două au JSON
public, a treia RSS. Fără scraping HTML, fără dependențe noi.
"""

import html
import re
from pathlib import Path

import httpx
# NU `xml.etree.ElementTree`: feed-ul RSS e input extern, iar parserul din
# stdlib e vulnerabil la expansiune de entități („billion laughs").
from defusedxml import ElementTree as ET

from scrapers.base import Market, make_job

_REMOTEOK_URL = "https://remoteok.com/api"
_REMOTIVE_URL = "https://remotive.com/api/remote-jobs"
_WWR_URL = "https://weworkremotely.com/categories/remote-programming-jobs.rss"

_TIMEOUT = 30
# RemoteOK cere un User-Agent identificabil; fără el întoarce 403.
_HEADERS = {"User-Agent": "JobApp/1.0 (+https://github.com/Geronimo1975)"}

_TAG_RE = re.compile(r"<[^>]+>")


def _clean(text: str) -> str:
    """Scoate marcajul HTML și decodează entitățile."""
    return html.unescape(_TAG_RE.sub(" ", str(text or ""))).strip()


class RemoteBoardsScraper:
    """Agregă joburi remote din board-uri cu API/feed public."""

    def __init__(self, config: dict, project_root: Path):
        self.config = config
        self.project_root = project_root

    def scrape_market(self, market: Market) -> tuple[list[dict], list[str]]:
        """Interoghează cele trei board-uri. Fiecare eșuează independent."""
        jobs: list[dict] = []
        errors: list[str] = []

        for name, fetch in (
            ("remoteok", self._fetch_remoteok),
            ("remotive", self._fetch_remotive),
            ("weworkremotely", self._fetch_wwr),
        ):
            try:
                jobs.extend(fetch(market))
            except Exception as e:
                errors.append(f"[{market.key}/{name}] {e}")

        return jobs, errors

    # ── Fetch ───────────────────────────────────────────────

    def _fetch_remoteok(self, market: Market) -> list[dict]:
        resp = httpx.get(_REMOTEOK_URL, headers=_HEADERS, timeout=_TIMEOUT)
        resp.raise_for_status()
        return self._parse_remoteok(resp.json(), market)

    def _fetch_remotive(self, market: Market) -> list[dict]:
        # Remotive acceptă un singur termen de căutare; folosim primul
        # keyword al fiecărui grup ca reprezentant.
        jobs: list[dict] = []
        for group in market.keyword_groups:
            resp = httpx.get(
                _REMOTIVE_URL, params={"search": group[0]},
                headers=_HEADERS, timeout=_TIMEOUT,
            )
            resp.raise_for_status()
            jobs.extend(self._parse_remotive(resp.json(), market))
        return self._dedupe(jobs)

    def _fetch_wwr(self, market: Market) -> list[dict]:
        resp = httpx.get(_WWR_URL, headers=_HEADERS, timeout=_TIMEOUT)
        resp.raise_for_status()
        return self._parse_wwr(resp.text, market)

    # ── Parse ───────────────────────────────────────────────

    def _parse_remoteok(self, payload: list, market: Market) -> list[dict]:
        jobs = []
        for entry in payload or []:
            # Primul element al feed-ului e mereu nota legală, fără `position`.
            if not isinstance(entry, dict) or not entry.get("position"):
                continue
            if not entry.get("url"):
                continue
            jobs.append(
                make_job(
                    source=f"remoteok-{market.key}",
                    platform="remoteok",
                    title=entry.get("position", ""),
                    company=entry.get("company", ""),
                    location=entry.get("location", "Remote"),
                    url=entry.get("url", ""),
                    description=_clean(entry.get("description", "")),
                    market=market,
                    date_posted=str(entry.get("date", "")),
                )
            )
        return self._dedupe(jobs)

    def _parse_remotive(self, payload: dict, market: Market) -> list[dict]:
        jobs = []
        for entry in (payload or {}).get("jobs", []):
            if not entry.get("url"):
                continue
            jobs.append(
                make_job(
                    source=f"remotive-{market.key}",
                    platform="remotive",
                    title=entry.get("title", ""),
                    company=entry.get("company_name", ""),
                    location=entry.get("candidate_required_location", "Remote"),
                    url=entry.get("url", ""),
                    description=_clean(entry.get("description", "")),
                    market=market,
                    date_posted=str(entry.get("publication_date", "")),
                )
            )
        return self._dedupe(jobs)

    def _parse_wwr(self, xml_text: str, market: Market) -> list[dict]:
        """Parsează feed-ul RSS. XML invalid sau ostil → listă goală.

        Se prinde `Exception`, nu doar `ParseError`: defusedxml ridică tipuri
        proprii (`EntitiesForbidden`, `DTDForbidden`) pentru feed-uri ostile,
        iar reacția corectă la oricare dintre ele e aceeași — zero joburi din
        sursa asta, runul merge mai departe.
        """
        try:
            root = ET.fromstring(xml_text)
        except Exception:
            return []

        jobs = []
        for item in root.iterfind(".//item"):
            link = (item.findtext("link") or "").strip()
            raw_title = (item.findtext("title") or "").strip()
            if not link or not raw_title:
                continue
            # WWR pune „Firmă: Poziție" într-un singur câmp.
            company, _, title = raw_title.partition(":")
            if not title:
                company, title = "", raw_title
            jobs.append(
                make_job(
                    source=f"weworkremotely-{market.key}",
                    platform="weworkremotely",
                    title=title.strip(),
                    company=company.strip(),
                    location="Remote",
                    url=link,
                    description=_clean(item.findtext("description") or ""),
                    market=market,
                    date_posted=(item.findtext("pubDate") or "").strip(),
                )
            )
        return self._dedupe(jobs)

    @staticmethod
    def _dedupe(jobs: list[dict]) -> list[dict]:
        """Dedupe local pe URL; dedupe-ul global e treaba lui store/seen.py."""
        seen: set[str] = set()
        unique = []
        for job in jobs:
            if job["url"] in seen:
                continue
            seen.add(job["url"])
            unique.append(job)
        return unique
```

- [ ] **Step 6: Rulează testele ca să confirmi că trec**

```bash
source .venv/bin/activate && python -m pytest tests/test_remote_boards.py -q
```

Așteptat: toate testele PASS, inclusiv `test_entity_expansion_attack_returns_empty`.

- [ ] **Step 7: Commit**

```bash
git add scrapers/remote_boards.py requirements.txt tests/test_remote_boards.py tests/fixtures/
git commit -m "feat(scrapers): board-uri remote cu API public (RemoteOK, Remotive, WWR) (JOB-39)"
```

---

### Task 6: Board-uri românești (`scrapers/ro_boards.py`) — best-effort

**Avertisment pentru implementator:** eJobs și BestJobs nu au API public. Structura HTML **nu e cunoscută în avans și nu trebuie ghicită**. Prima acțiune a acestei sarcini e să descarci o pagină reală, s-o salvezi ca fixture, și abia apoi să scrii parserul pe ce vezi acolo. Dacă selectorii din parser au fost inventați fără să te uiți la HTML-ul real, sarcina e greșită.

**Files:**
- Create: `scrapers/ro_boards.py`
- Create: `tests/fixtures/ejobs_sample.html` (descărcat, nu scris de mână)
- Test: `tests/test_ro_boards.py`

**Interfaces:**
- Consumes: `scrapers.base.Market`, `scrapers.base.make_job`
- Produces:
  - `class RoBoardsScraper` cu: `__init__(config: dict, project_root: Path)`, `scrape_market(market: Market) -> tuple[list[dict], list[str]]`, `_parse_ejobs(html_text: str, market: Market) -> list[dict]`

- [ ] **Step 1: Descarcă o pagină reală și salveaz-o ca fixture**

```bash
source .venv/bin/activate && python3 -c "
import httpx, pathlib
url = 'https://www.ejobs.ro/locuri-de-munca/python'
r = httpx.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=30, follow_redirects=True)
print('HTTP', r.status_code, 'bytes', len(r.text))
p = pathlib.Path('tests/fixtures/ejobs_sample.html')
p.write_text(r.text, encoding='utf-8')
print('salvat', p)
"
```

Așteptat: `HTTP 200` și un fișier de câteva sute de KB.

**Dacă întoarce 403 sau o pagină goală** (site-ul e în spatele unui zid anti-bot randat cu JavaScript): oprește-te aici, nu insista și nu încerca să ocolești protecția. Marchează sursa ca indisponibilă, notează-l în raportul de la finalul sarcinii, și livrează doar scheletul modulului cu `_parse_ejobs` întorcând `[]` plus testul care confirmă comportamentul gol. Restul planului nu depinde de această sursă — de asta e marcată `best_effort`.

- [ ] **Step 2: Inspectează HTML-ul real și identifică selectorii**

```bash
source .venv/bin/activate && python3 -c "
from bs4 import BeautifulSoup
import pathlib, collections
soup = BeautifulSoup(pathlib.Path('tests/fixtures/ejobs_sample.html').read_text(encoding='utf-8'), 'html.parser')
links = [a for a in soup.find_all('a', href=True) if '/loc-de-munca/' in a['href'] or '/job/' in a['href']]
print('link-uri candidate:', len(links))
for a in links[:5]:
    print(' href:', a['href'][:90])
    print(' text:', ' '.join(a.get_text(' ', strip=True).split())[:90])
    print(' clase parinte:', (a.parent.get('class') if a.parent else None))
    print('---')
"
```

Notează selectorii reali observați. Îi folosești la Step 4. Nu trece mai departe cu selectori pe care nu i-ai văzut în output-ul de mai sus.

- [ ] **Step 3: Scrie testele care eșuează**

Creează `tests/test_ro_boards.py`. Înlocuiește `EXPECTED_MIN_JOBS` cu numărul real de anunțuri observate la Step 2:

```python
"""
Teste pentru board-urile românești — parsare pe fixture salvat, fără rețea.

Sursă best-effort: eJobs/BestJobs nu au API public, iar HTML-ul se schimbă.
Când aceste teste pică pe fixture nou, înseamnă că site-ul s-a schimbat —
e semnalul de reparat parserul, nu un bug de regresie.
"""

from pathlib import Path
from unittest.mock import patch

from scrapers.base import Market
from scrapers.ro_boards import RoBoardsScraper


FIXTURES = Path(__file__).parent / "fixtures"
EXPECTED_MIN_JOBS = 1  # ← ajustează la ce ai văzut efectiv în fixture

CFG = {
    "scraping": {
        "markets": {
            "ro_remote": {
                "country": "ROMANIA", "language": "en", "locations": ["Romania"],
                "remote": True,
                "keyword_groups": [["Python Developer"]],
                "sources": ["ro_boards"],
                "best_effort_sources": ["ro_boards"],
            }
        }
    }
}


def _market():
    return Market.from_config(CFG)[0]


def _scraper(tmp_path):
    return RoBoardsScraper(CFG, tmp_path)


class TestParseEjobs:
    def test_extracts_jobs_from_real_fixture(self, tmp_path):
        html_text = (FIXTURES / "ejobs_sample.html").read_text(encoding="utf-8")
        jobs = _scraper(tmp_path)._parse_ejobs(html_text, _market())
        assert len(jobs) >= EXPECTED_MIN_JOBS

    def test_every_job_has_absolute_url(self, tmp_path):
        html_text = (FIXTURES / "ejobs_sample.html").read_text(encoding="utf-8")
        for job in _scraper(tmp_path)._parse_ejobs(html_text, _market()):
            assert job["url"].startswith("http")

    def test_every_job_has_title(self, tmp_path):
        html_text = (FIXTURES / "ejobs_sample.html").read_text(encoding="utf-8")
        for job in _scraper(tmp_path)._parse_ejobs(html_text, _market()):
            assert job["title"]

    def test_language_and_market_come_from_market(self, tmp_path):
        html_text = (FIXTURES / "ejobs_sample.html").read_text(encoding="utf-8")
        jobs = _scraper(tmp_path)._parse_ejobs(html_text, _market())
        if jobs:
            assert jobs[0]["language"] == "en"
            assert jobs[0]["market"] == "ro_remote"
            assert jobs[0]["platform"] == "ejobs"

    def test_garbage_html_returns_empty_not_exception(self, tmp_path):
        assert _scraper(tmp_path)._parse_ejobs("<html><body>nimic</body></html>", _market()) == []


class TestScrapeMarket:
    def test_network_failure_is_reported_not_raised(self, tmp_path):
        with patch("scrapers.ro_boards.httpx.get", side_effect=RuntimeError("403 Forbidden")):
            jobs, errors = _scraper(tmp_path).scrape_market(_market())

        assert jobs == []
        assert errors
        assert "403" in errors[0]
```

- [ ] **Step 4: Rulează testele ca să confirmi că eșuează**

```bash
source .venv/bin/activate && python -m pytest tests/test_ro_boards.py -q
```

Așteptat: `ModuleNotFoundError: No module named 'scrapers.ro_boards'`.

- [ ] **Step 5: Implementează `scrapers/ro_boards.py`**

Scheletul de mai jos e complet, **cu excepția selectorilor din `_parse_ejobs`**, care se completează cu ce ai observat la Step 2. Locul e marcat explicit.

```python
"""
Board-uri românești — eJobs, BestJobs.

Sursă BEST-EFFORT prin design: aceste site-uri nu au API public, deci
parsarea se face pe HTML și **se va strica** la primul redesign. Eșecul ei
nu trebuie să marcheze niciodată runul ca FAILED; de aceea piața o
declară în `best_effort_sources`.
"""

from pathlib import Path
from urllib.parse import urljoin

import httpx

from scrapers.base import Market, make_job

try:
    from bs4 import BeautifulSoup
    HAS_BS4 = True
except ImportError:
    BeautifulSoup = None
    HAS_BS4 = False

_EJOBS_BASE = "https://www.ejobs.ro"
_EJOBS_SEARCH = "https://www.ejobs.ro/locuri-de-munca/{keyword}"
_TIMEOUT = 30
# Fără User-Agent de browser, eJobs întoarce 403.
_HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}

# ── Selectori CSS ────────────────────────────────────────────
# Valorile de mai jos sunt PROVIZORII. Înlocuiește-le cu ce ai observat
# efectiv în fixture-ul descărcat (Step 2). Testul
# `test_extracts_jobs_from_real_fixture` e cel care confirmă că sunt corecte;
# dacă întoarce 0 joburi, selectorii sunt greșiți — nu testul.
# Sunt izolate aici tocmai pentru că eJobs nu are API și HTML-ul se schimbă:
# la următoarea stricare se editează doar acest bloc.
_SELECTOR_CARD = "div[class*='JobCard']"
_SELECTOR_LINK = "a[href*='/loc-de-munca/']"
_SELECTOR_COMPANY = "[class*='ompany']"
_SELECTOR_LOCATION = "[class*='ocation']"


class RoBoardsScraper:
    """Scraper pentru board-urile de joburi din România."""

    def __init__(self, config: dict, project_root: Path):
        self.config = config
        self.project_root = project_root

    def scrape_market(self, market: Market) -> tuple[list[dict], list[str]]:
        """Caută pe eJobs pentru fiecare grup de keywords."""
        if not HAS_BS4:
            return [], ["bs4 nu e instalat — ro_boards sărit"]

        jobs: list[dict] = []
        errors: list[str] = []

        for group in market.keyword_groups:
            keyword = group[0].lower().replace(" ", "-")
            try:
                resp = httpx.get(
                    _EJOBS_SEARCH.format(keyword=keyword),
                    headers=_HEADERS, timeout=_TIMEOUT, follow_redirects=True,
                )
                resp.raise_for_status()
                jobs.extend(self._parse_ejobs(resp.text, market))
            except Exception as e:
                errors.append(f"[{market.key}/ejobs] {group[0]}: {e}")

        return self._dedupe(jobs), errors

    def _parse_ejobs(self, html_text: str, market: Market) -> list[dict]:
        """Extrage anunțurile din pagina de rezultate eJobs.

        HTML-ul care nu conține anunțuri dă listă goală, nu excepție: e
        situația normală când site-ul se schimbă.
        """
        soup = BeautifulSoup(html_text, "html.parser")
        jobs = []

        for card in soup.select(_SELECTOR_CARD):
            link = card.select_one(_SELECTOR_LINK)
            if not link or not link.get("href"):
                continue
            title = link.get_text(" ", strip=True)
            if not title:
                continue

            company_el = card.select_one(_SELECTOR_COMPANY)
            location_el = card.select_one(_SELECTOR_LOCATION)

            jobs.append(
                make_job(
                    source=f"ejobs-{market.key}",
                    platform="ejobs",
                    title=title,
                    company=company_el.get_text(" ", strip=True) if company_el else "",
                    location=location_el.get_text(" ", strip=True) if location_el else "Romania",
                    # Link-urile din listă sunt relative.
                    url=urljoin(_EJOBS_BASE, link["href"]),
                    description="",  # descrierea completă e pe pagina jobului
                    market=market,
                )
            )

        return jobs

    @staticmethod
    def _dedupe(jobs: list[dict]) -> list[dict]:
        seen: set[str] = set()
        unique = []
        for job in jobs:
            if job["url"] in seen:
                continue
            seen.add(job["url"])
            unique.append(job)
        return unique
```

- [ ] **Step 6: Rulează testele ca să confirmi că trec**

```bash
source .venv/bin/activate && python -m pytest tests/test_ro_boards.py -q
```

Așteptat: toate testele PASS. Dacă `test_extracts_jobs_from_real_fixture` întoarce 0 joburi, selectorii sunt greșiți — întoarce-te la Step 2, nu slăbi testul.

- [ ] **Step 7: Commit**

```bash
git add scrapers/ro_boards.py tests/test_ro_boards.py tests/fixtures/ejobs_sample.html
git commit -m "feat(scrapers): board-uri românești eJobs (best-effort) (JOB-40)"
```

---

### Task 7: Orchestrarea pe piețe în `ScraperAgent`

`AgentStatus` devine onest: azi 40 de apeluri eșuate din 49 raportează `SUCCESS`.

**Files:**
- Modify: `agents/scraper_agent.py` (rescriere integrală)
- Modify: `tests/test_scraper_agent.py` (rescriere integrală — API-ul agentului se schimbă)

**Interfaces:**
- Consumes: `scrapers.base.Market`, `scrapers.base.save_jobs`, `JobSpyScraper.scrape_market`, `RemoteBoardsScraper.scrape_market`, `RoBoardsScraper.scrape_market`, `CareerPageScraper.scrape`
- Produces: `ScraperAgent.run(markets: list[str] | None = None, sources: list[str] | None = None, **kwargs) -> AgentResult`, cu `result.data = {"jobs": list[dict], "markets_used": list[str], "sources_used": list[str], "total_found": int}`

- [ ] **Step 1: Rescrie testele**

Înlocuiește integral `tests/test_scraper_agent.py`:

```python
"""
Teste pentru ScraperAgent — toate sursele mock-uite, fără rețea.
"""

from unittest.mock import MagicMock, patch

import pytest

from agents.base_agent import AgentStatus
from agents.scraper_agent import ScraperAgent


def _job(url, title="Python Developer", market="de"):
    return {
        "url": url, "title": title, "company": "TestCo",
        "market": market, "language": "de", "status": "new",
    }


@pytest.fixture
def agent(tmp_path):
    """ScraperAgent cu toate sursele mock-uite și config de test."""
    cfg = {
        "scraping": {
            "markets": {
                "de": {
                    "country": "GERMANY", "language": "de", "locations": ["Germany"],
                    "remote": False, "keyword_groups": [["Python Developer"]],
                    "sources": ["jobspy", "career_pages"],
                },
                "ro_remote": {
                    "country": "ROMANIA", "language": "en", "locations": ["Romania"],
                    "remote": True, "keyword_groups": [["Python Developer"]],
                    "sources": ["jobspy", "remote_boards", "ro_boards"],
                    "best_effort_sources": ["ro_boards"],
                },
            }
        },
        "output": {"logs_dir": "output/logs", "log_level": "CRITICAL"},
    }

    with patch("agents.scraper_agent.JobSpyScraper") as js, \
         patch("agents.scraper_agent.RemoteBoardsScraper") as rb, \
         patch("agents.scraper_agent.RoBoardsScraper") as ro, \
         patch("agents.scraper_agent.CareerPageScraper") as cp:
        js.return_value = MagicMock()
        rb.return_value = MagicMock()
        ro.return_value = MagicMock()
        cp.return_value = MagicMock()

        a = ScraperAgent()
        a.config = cfg
        a.logger.setLevel("CRITICAL")
        a.project_root = tmp_path

        # Implicit: totul întoarce gol și fără erori
        a.jobspy.scrape_market.return_value = ([], [])
        a.remote_boards.scrape_market.return_value = ([], [])
        a.ro_boards.scrape_market.return_value = ([], [])
        a.career.scrape.return_value = []
        yield a


class TestMarketSelection:
    def test_runs_all_markets_by_default(self, agent):
        result = agent.run()
        assert set(result.data["markets_used"]) == {"de", "ro_remote"}

    def test_can_restrict_to_one_market(self, agent):
        result = agent.run(markets=["ro_remote"])
        assert result.data["markets_used"] == ["ro_remote"]

    def test_unknown_market_is_ignored(self, agent):
        result = agent.run(markets=["nonexistent"])
        assert result.data["markets_used"] == []

    def test_each_market_gets_its_own_market_object(self, agent):
        agent.run()
        countries = {c.args[0].country for c in agent.jobspy.scrape_market.call_args_list}
        assert countries == {"GERMANY", "ROMANIA"}


class TestSourceRouting:
    def test_remote_boards_only_called_for_market_that_lists_it(self, agent):
        agent.run()
        markets = [c.args[0].key for c in agent.remote_boards.scrape_market.call_args_list]
        assert markets == ["ro_remote"]

    def test_career_pages_only_for_de(self, agent):
        agent.run()
        assert agent.career.scrape.call_count == 1

    def test_sources_override_restricts_execution(self, agent):
        agent.run(sources=["jobspy"])
        agent.remote_boards.scrape_market.assert_not_called()
        agent.career.scrape.assert_not_called()


class TestAggregation:
    def test_collects_jobs_from_all_sources(self, agent):
        agent.jobspy.scrape_market.return_value = ([_job("https://a.com/1")], [])
        agent.remote_boards.scrape_market.return_value = ([_job("https://b.com/2")], [])
        result = agent.run()
        assert len(result.data["jobs"]) >= 2

    def test_writes_results_file(self, agent, tmp_path):
        agent.jobspy.scrape_market.return_value = ([_job("https://a.com/1")], [])
        agent.run()
        assert (tmp_path / "data" / "jobs_scraped.json").exists()


class TestHonestStatus:
    def test_all_ok_is_success(self, agent):
        agent.jobspy.scrape_market.return_value = ([_job("https://a.com/1")], [])
        assert agent.run().status == AgentStatus.SUCCESS

    def test_partial_when_a_source_reports_errors(self, agent):
        """Erorile întoarse de scraper, nu excepțiile, contează pentru status."""
        agent.jobspy.scrape_market.return_value = ([_job("https://a.com/1")], ["[de/linkedin] 429"])
        result = agent.run()
        assert result.status == AgentStatus.PARTIAL
        assert any("429" in e for e in result.errors)

    def test_partial_when_a_source_raises(self, agent):
        agent.jobspy.scrape_market.side_effect = RuntimeError("boom")
        agent.remote_boards.scrape_market.return_value = ([_job("https://b.com/2")], [])
        result = agent.run()
        assert result.status == AgentStatus.PARTIAL
        assert result.data["jobs"]

    def test_failed_when_nothing_returned_and_errors_exist(self, agent):
        agent.jobspy.scrape_market.side_effect = RuntimeError("boom")
        agent.remote_boards.scrape_market.side_effect = RuntimeError("boom")
        agent.ro_boards.scrape_market.side_effect = RuntimeError("boom")
        agent.career.scrape.side_effect = RuntimeError("boom")
        assert agent.run().status == AgentStatus.FAILED

    def test_zero_jobs_without_errors_is_success(self, agent):
        """Zero joburi noi e un rezultat valid, nu o eroare."""
        assert agent.run().status == AgentStatus.SUCCESS

    def test_best_effort_source_failure_does_not_prevent_success(self, agent):
        """ro_boards e best-effort pentru ro_remote: eșecul lui nu strică statusul."""
        agent.jobspy.scrape_market.return_value = ([_job("https://a.com/1")], [])
        agent.ro_boards.scrape_market.side_effect = RuntimeError("403 Forbidden")
        result = agent.run()
        assert result.status == AgentStatus.SUCCESS
        assert any("403" in e for e in result.errors)   # tot se raportează
```

- [ ] **Step 2: Rulează testele ca să confirmi că eșuează**

```bash
source .venv/bin/activate && python -m pytest tests/test_scraper_agent.py -q
```

Așteptat: eșec la `patch("agents.scraper_agent.RemoteBoardsScraper")` (`AttributeError`), pentru că agentul nu importă încă modulul.

- [ ] **Step 3: Rescrie `agents/scraper_agent.py`**

```python
"""
Scraper Agent — orchestrează scraping-ul pe piețe și surse.

Responsabilități:
  1. Citește piețele din config și rulează sursele declarate de fiecare
  2. Izolează erorile: o sursă căzută nu doboară runul
  3. Raportează un status onest (SUCCESS / PARTIAL / FAILED)
  4. Salvează rezultatele rulării în data/jobs_scraped.json
"""

from agents.base_agent import AgentResult, AgentStatus, BaseAgent
from scrapers.base import Market, save_jobs
from scrapers.career_page_scraper import CareerPageScraper
from scrapers.jobspy_scraper import JobSpyScraper
from scrapers.remote_boards import RemoteBoardsScraper
from scrapers.ro_boards import RoBoardsScraper


class ScraperAgent(BaseAgent):
    """Agent pentru scraping joburi pe mai multe piețe."""

    def __init__(self):
        super().__init__("ScraperAgent")
        self.jobspy = JobSpyScraper(self.config, self.project_root)
        self.remote_boards = RemoteBoardsScraper(self.config, self.project_root)
        self.ro_boards = RoBoardsScraper(self.config, self.project_root)
        self.career = CareerPageScraper(self.config, self.project_root)

    def run(
        self,
        markets: list[str] | None = None,
        sources: list[str] | None = None,
        **kwargs,
    ) -> AgentResult:
        """Rulează scraping pe piețele cerute.

        Args:
            markets: Chei de piață (implicit: toate din config)
            sources: Restrânge sursele (implicit: cele declarate de fiecare piață)
        """
        self._start()
        result = AgentResult(agent_name=self.name, status=AgentStatus.RUNNING)

        all_markets = Market.from_config(self.config)
        if markets is not None:
            all_markets = [m for m in all_markets if m.key in markets]

        all_jobs: list[dict] = []
        # Erori care contează pentru status (exclude sursele best-effort).
        blocking_errors: list[str] = []

        for market in all_markets:
            active = [s for s in market.sources if sources is None or s in sources]
            self.logger.info(f"Piața „{market.key}" — surse: {', '.join(active) or 'niciuna'}")

            for source in active:
                jobs, errors = self._run_source(source, market)
                all_jobs.extend(jobs)
                result.errors.extend(errors)
                if source not in market.best_effort_sources:
                    blocking_errors.extend(errors)
                self.logger.info(f"  {source}: {len(jobs)} joburi, {len(errors)} erori")

        path = save_jobs(all_jobs, self.project_root / "data" / "jobs_scraped.json")
        self.logger.info(f"Salvat: {path}")

        result.items_processed = len(all_jobs)
        result.items_succeeded = len(all_jobs)
        result.data = {
            "jobs": all_jobs,
            "markets_used": [m.key for m in all_markets],
            "sources_used": sorted({s for m in all_markets for s in m.sources
                                    if sources is None or s in sources}),
            "total_found": len(all_jobs),
        }

        # Statusul spune adevărul: varianta veche raporta SUCCESS chiar și
        # când majoritatea apelurilor eșuaseră.
        if not all_jobs and result.errors:
            result.status = AgentStatus.FAILED
            result.message = f"Scraping eșuat: {len(result.errors)} erori"
        elif blocking_errors:
            result.status = AgentStatus.PARTIAL
            result.message = f"{len(all_jobs)} joburi ({len(blocking_errors)} surse cu probleme)"
        else:
            result.status = AgentStatus.SUCCESS
            result.message = f"{len(all_jobs)} joburi din {len(all_markets)} piețe"

        return self._finish(result)

    def _run_source(self, source: str, market: Market) -> tuple[list[dict], list[str]]:
        """Rulează o sursă pentru o piață, prinzând orice excepție.

        Contractul e uniform: `(joburi, erori)`. O sursă care crapă întoarce
        lista goală plus eroarea, nu propagă excepția.
        """
        try:
            if source == "jobspy":
                return self.jobspy.scrape_market(market)
            if source == "remote_boards":
                return self.remote_boards.scrape_market(market)
            if source == "ro_boards":
                return self.ro_boards.scrape_market(market)
            if source == "career_pages":
                # CareerPageScraper e anterior conceptului de piață și are
                # altă semnătură; îl adaptăm aici, nu îl rescriem.
                return self.career.scrape(), []
            return [], [f"[{market.key}] sursă necunoscută: {source}"]
        except ImportError as e:
            return [], [f"[{market.key}/{source}] dependență lipsă: {e}"]
        except Exception as e:
            return [], [f"[{market.key}/{source}] {e}"]
```

- [ ] **Step 4: Rulează testele ca să confirmi că trec**

```bash
source .venv/bin/activate && python -m pytest tests/test_scraper_agent.py -q
```

Așteptat: toate testele PASS.

- [ ] **Step 5: Rulează întreaga suită ca să prinzi regresiile**

```bash
source .venv/bin/activate && python -m pytest -q
```

Așteptat: totul trece. Dacă `tests/test_integration.py` pică pentru că folosea vechea semnătură `run(keywords=..., locations=...)`, actualizează apelul la `run(markets=[...])` — comportamentul testat rămâne același.

- [ ] **Step 6: Commit**

```bash
git add agents/scraper_agent.py tests/test_scraper_agent.py tests/test_integration.py
git commit -m "refactor(scraper): orchestrare pe piețe și status onest la erori (JOB-41)"
```

---

### Task 8: Poarta prefiltru în pipeline

Legarea pieselor: dedupe cu memorie și prefiltru între `scrape` și `analyze`.

**Files:**
- Modify: `main.py` (secțiunea `--step scrape`, ~liniile 167-185)
- Test: `tests/test_prefilter_gate.py`

**Interfaces:**
- Consumes: `filters.relevance.prescore`, `filters.relevance.passes_prefilter`, `store.seen.SeenIndex` și constantele `RESPINS_PREFILTRU`/`ANALIZAT`
- Produces: `main.apply_prefilter_gate(jobs: list[dict], config: dict, seen: SeenIndex) -> tuple[list[dict], dict]` — întoarce `(joburi_reținute, statistici)` unde statisticile au cheile `total`, `deja_vazute`, `respinse`, `retinute`.

- [ ] **Step 1: Scrie testele care eșuează**

Creează `tests/test_prefilter_gate.py`:

```python
"""
Teste pentru poarta dintre scrape și analyze: dedupe + prefiltru.
"""

from store.seen import APLICAT, RESPINS_PREFILTRU, SeenIndex

from main import apply_prefilter_gate


CFG = {
    "skills": {"primary": ["Python", "Django"], "secondary": ["Docker"]},
    "scraping": {
        "filters": {"exclude_keywords": ["Director"], "min_prescore": 40},
    },
}


def _job(url, title, description=""):
    return {"url": url, "title": title, "company": "Co", "description": description}


class TestGate:
    def test_relevant_job_is_kept(self, tmp_path):
        seen = SeenIndex.load(tmp_path / "seen.json")
        kept, stats = apply_prefilter_gate(
            [_job("https://a.com/1", "Python Django Developer")], CFG, seen
        )
        assert len(kept) == 1
        assert stats["retinute"] == 1

    def test_irrelevant_job_is_dropped(self, tmp_path):
        seen = SeenIndex.load(tmp_path / "seen.json")
        kept, stats = apply_prefilter_gate([_job("https://a.com/1", "Truck Driver")], CFG, seen)
        assert kept == []
        assert stats["respinse"] == 1

    def test_excluded_title_is_dropped(self, tmp_path):
        seen = SeenIndex.load(tmp_path / "seen.json")
        kept, _ = apply_prefilter_gate(
            [_job("https://a.com/1", "Director of Python Engineering")], CFG, seen
        )
        assert kept == []

    def test_already_applied_job_is_dropped(self, tmp_path):
        seen = SeenIndex.load(tmp_path / "seen.json")
        seen.mark(_job("https://a.com/1", "Python Django Developer"), APLICAT)

        kept, stats = apply_prefilter_gate(
            [_job("https://a.com/1", "Python Django Developer")], CFG, seen
        )
        assert kept == []
        assert stats["deja_vazute"] == 1

    def test_rejected_job_is_remembered(self, tmp_path):
        """Un job respins nu trebuie re-evaluat la rularea următoare."""
        seen = SeenIndex.load(tmp_path / "seen.json")
        job = _job("https://a.com/1", "Truck Driver")
        apply_prefilter_gate([job], CFG, seen)
        assert seen.is_seen(job)
        assert seen.entries["https://a.com/1"]["decision"] == RESPINS_PREFILTRU

    def test_kept_job_carries_prescore_and_reasons(self, tmp_path):
        seen = SeenIndex.load(tmp_path / "seen.json")
        kept, _ = apply_prefilter_gate(
            [_job("https://a.com/1", "Python Django Developer")], CFG, seen
        )
        assert kept[0]["prescore"] >= 40
        assert "Python" in kept[0]["keywords_match"]

    def test_duplicate_in_same_batch_counted_once(self, tmp_path):
        seen = SeenIndex.load(tmp_path / "seen.json")
        job = _job("https://a.com/1", "Python Django Developer")
        kept, stats = apply_prefilter_gate([job, dict(job)], CFG, seen)
        assert len(kept) == 1
        assert stats["total"] == 2

    def test_empty_input_is_safe(self, tmp_path):
        seen = SeenIndex.load(tmp_path / "seen.json")
        kept, stats = apply_prefilter_gate([], CFG, seen)
        assert kept == []
        assert stats["total"] == 0
```

- [ ] **Step 2: Rulează testele ca să confirmi că eșuează**

```bash
source .venv/bin/activate && python -m pytest tests/test_prefilter_gate.py -q
```

Așteptat: `ImportError: cannot import name 'apply_prefilter_gate' from 'main'`.

- [ ] **Step 3: Adaugă funcția în `main.py`**

Adaugă importurile lângă celelalte importuri de agenți (după linia `from agents.analyzer_agent import AnalyzerAgent`):

```python
from filters.relevance import passes_prefilter, prescore
from store.seen import ANALIZAT, RESPINS_PREFILTRU, SeenIndex
```

Adaugă funcția înainte de `run_pipeline`:

```python
def apply_prefilter_gate(
    jobs: list[dict], config: dict, seen: SeenIndex
) -> tuple[list[dict], dict]:
    """Poarta ieftină dintre scrape și analyze.

    Elimină joburile deja văzute și pe cele sub pragul local, ca AI-ul să
    ruleze doar pe ce are șansă. Joburile respinse se memorează, altfel ar
    fi re-evaluate la fiecare rulare.
    """
    stats = {"total": len(jobs), "deja_vazute": 0, "respinse": 0, "retinute": 0}

    fresh = seen.filter_new(jobs)
    stats["deja_vazute"] = len(jobs) - len(fresh)

    kept = []
    for job in fresh:
        score, reasons = prescore(job, config)
        if not passes_prefilter(job, config):
            seen.mark(job, RESPINS_PREFILTRU)
            stats["respinse"] += 1
            continue
        job["prescore"] = score
        # Motivele ajung în col „Keywords Match" — auditabil de ce a trecut.
        job["keywords_match"] = ", ".join(reasons)
        seen.mark(job, ANALIZAT)
        kept.append(job)

    stats["retinute"] = len(kept)
    return kept, stats
```

- [ ] **Step 4: Rulează testele ca să confirmi că trec**

```bash
source .venv/bin/activate && python -m pytest tests/test_prefilter_gate.py -q
```

Așteptat: toate testele PASS.

- [ ] **Step 5: Leagă poarta în `run_pipeline`**

În blocul `if args.step in ("scrape", "full"):`, imediat după `pipeline_jobs = result.data.get("jobs", [])` și **înainte** de apelul către `TrackerAgent`, inserează:

```python
            # Poarta ieftină: dedupe cu memorie + prefiltru local, ca analiza
            # AI să ruleze doar pe joburile cu șansă reală.
            seen = SeenIndex.load(PROJECT_ROOT / "data" / "seen_jobs.json")
            pipeline_jobs, gate_stats = apply_prefilter_gate(pipeline_jobs, config, seen)
            seen.save()
            print(
                f"  Prefiltru: {gate_stats['total']} găsite → "
                f"{gate_stats['deja_vazute']} deja văzute, "
                f"{gate_stats['respinse']} sub prag → "
                f"{gate_stats['retinute']} spre analiză"
            )
```

Verifică numele exacte disponibile în scope pentru `PROJECT_ROOT` și `config`:

```bash
source .venv/bin/activate && grep -n "PROJECT_ROOT\|^from agents.base_agent\|config = " main.py | head
```

Dacă `PROJECT_ROOT` nu e importat în `main.py`, adaugă-l la importul existent din `agents.base_agent`. Dacă variabila de config are alt nume în `run_pipeline`, folosește numele real.

- [ ] **Step 6: Verifică pipeline-ul fără rețea și fără AI**

```bash
source .venv/bin/activate && python -m pytest -q
```

Așteptat: întreaga suită trece.

- [ ] **Step 7: Commit**

```bash
git add main.py tests/test_prefilter_gate.py
git commit -m "feat(pipeline): poartă de dedupe + prefiltru înainte de analiza AI (JOB-42)"
```

---

### Task 9: `JobApp Urls` — alimentarea indexului din Sheet

Ca joburile deja aplicate să nu reapară, indexul are nevoie de istoricul din Sheet. `JobApp Pending` întoarce doar rândurile cu `SENDEN`, deci nu ajută — și **nu se modifică**, fiind trigger-ul de producție.

**Files:**
- Create: `n8n_workflows/jobapp_urls.json`
- Modify: `config/settings.yaml` (blocul `n8n.paths`)
- Modify: `sender/n8n_client.py`
- Modify: `sender/n8n_mapping.py` (`FIELD_MAP` — doar dacă verificarea de la Step 8 confirmă)
- Modify: `main.py` (secțiunea `--step scrape`)
- Test: `tests/test_n8n_client.py` (adaugă o clasă nouă, nu rescrie fișierul)
- Test: `tests/test_n8n_mapping.py` (adaugă teste, nu rescrie fișierul)

**Interfaces:**
- Consumes: `store.seen.SeenIndex.seed_urls`
- Produces: `N8nClient.get_known_urls() -> list[str]` — întoarce URL-urile din Sheet ale rândurilor deja procesate (`Status` = `done` sau `Email Status` ∈ {`sent`, `GESENDET`}).

- [ ] **Step 1: Scrie testele care eșuează**

Adaugă la finalul lui `tests/test_n8n_client.py`:

```python
class TestGetKnownUrls:
    """Alimentarea indexului de dedupe din Sheet."""

    def _client(self, enabled=True):
        from sender.n8n_client import N8nClient
        return N8nClient({
            "n8n": {
                "enabled": enabled,
                "base_url": "https://n8n.test",
                "paths": {"urls": "/webhook/jobapp-urls"},
                "timeout_sec": 5,
            }
        })

    def test_disabled_client_returns_empty(self):
        assert self._client(enabled=False).get_known_urls() == []

    def test_returns_urls_of_processed_rows(self):
        from unittest.mock import patch
        rows = [
            {"Apply URL": "https://a.com/1", "Status": "done", "Email Status": ""},
            {"Apply URL": "https://a.com/2", "Status": "", "Email Status": "GESENDET"},
            {"Apply URL": "https://a.com/3", "Status": "", "Email Status": "sent"},
        ]
        with patch.object(type(self._client()), "_post", return_value={"rows": rows}):
            urls = self._client().get_known_urls()
        assert urls == ["https://a.com/1", "https://a.com/2", "https://a.com/3"]

    def test_skips_unprocessed_rows(self):
        """Un rând doar găsit, neaplicat, nu blochează re-descoperirea."""
        from unittest.mock import patch
        rows = [{"Apply URL": "https://a.com/9", "Status": "", "Email Status": ""}]
        with patch.object(type(self._client()), "_post", return_value={"rows": rows}):
            assert self._client().get_known_urls() == []

    def test_skips_rows_without_url(self):
        from unittest.mock import patch
        rows = [{"Apply URL": "", "Status": "done", "Email Status": ""}]
        with patch.object(type(self._client()), "_post", return_value={"rows": rows}):
            assert self._client().get_known_urls() == []

    def test_empty_response_is_safe(self):
        from unittest.mock import patch
        with patch.object(type(self._client()), "_post", return_value={}):
            assert self._client().get_known_urls() == []
```

- [ ] **Step 2: Rulează testele ca să confirmi că eșuează**

```bash
source .venv/bin/activate && python -m pytest tests/test_n8n_client.py -q -k KnownUrls
```

Așteptat: `AttributeError: 'N8nClient' object has no attribute 'get_known_urls'`.

- [ ] **Step 3: Implementează metoda în `sender/n8n_client.py`**

Adaugă după `get_pending`:

```python
    def get_known_urls(self) -> list[str]:
        """URL-urile rândurilor deja procesate din Sheet.

        Alimentează indexul de dedupe, ca un job la care s-a aplicat deja să
        nu reapară ca nou. `JobApp Pending` nu ajută aici: întoarce doar
        rândurile cu `SENDEN`.

        Există două forme de „trimis", pentru că le scriu două sisteme:
        `sent` (Apps Script → FastAPI) și `GESENDET` (`--step poll`).
        """
        if not self.enabled:
            return []
        data = self._post("urls", {"action": "get_urls"})
        urls = []
        for row in data.get("rows", []) or []:
            url = str(row.get("Apply URL", "") or "").strip()
            if not url:
                continue
            status = str(row.get("Status", "") or "").strip().lower()
            email_status = str(row.get("Email Status", "") or "").strip().lower()
            if status == "done" or email_status in ("sent", "gesendet"):
                urls.append(url)
        return urls
```

- [ ] **Step 4: Rulează testele ca să confirmi că trec**

```bash
source .venv/bin/activate && python -m pytest tests/test_n8n_client.py -q
```

Așteptat: toate testele PASS.

- [ ] **Step 5: Adaugă calea în `config/settings.yaml`**

În blocul `n8n.paths`, sub linia `trigger:`, adaugă:

```yaml
    urls: "/webhook/jobapp-urls"
```

- [ ] **Step 6: Construiește workflow-ul n8n plecând de la cel existent**

**Nu scrie JSON-ul de la zero.** Pornește de la `JobApp Pending`, care are deja credentialul corect (`googleApi`, service account) și autentificarea Header Auth:

```bash
source .venv/bin/activate && python3 -c "
import json, pathlib
wf = json.loads(pathlib.Path('n8n_workflows/jobapp_pending.json').read_text())
print('nume:', wf.get('name'))
for n in wf['nodes']:
    print(' -', n['name'], '|', n['type'], '|', json.dumps(n.get('parameters', {}))[:160])
"
```

Copiază fișierul și modifică-l:

```bash
cp n8n_workflows/jobapp_pending.json n8n_workflows/jobapp_urls.json
```

În `n8n_workflows/jobapp_urls.json`:
1. `name` → `"JobApp Urls"`; șterge cheile `id`, `versionId` și `webhookId` (n8n le regenerează).
2. Nodul Webhook: `path` → `"jobapp-urls"`. Păstrează credentialul Header Auth neatins.
3. Nodul Google Sheets: păstrează doc-ul, tab-ul `Bewerbungen` și credentialul `googleApi` exact cum sunt.
4. **Înlocuiește** nodul de filtrare (cel care caută `Sende Befehl = SENDEN`) cu un nod Code care proiectează doar 3 coloane:

```javascript
// Întoarce DOAR cele 3 coloane de care are nevoie indexul de dedupe.
// Tab-ul „Bewerbungen" are ~1140 coloane fantomă (col_29…col_1167);
// fără proiecție, fiecare apel ar transfera 1086 × 1168 celule.
const rows = items.map(item => ({
  "Apply URL": item.json["Apply URL"] || "",
  "Status": item.json["Status"] || "",
  "Email Status": item.json["Email Status"] || "",
})).filter(r => r["Apply URL"]);

return [{ json: { rows } }];
```

5. Nodul Respond to Webhook: întoarce corpul nodului Code (`{ rows: [...] }`).

- [ ] **Step 7: Publică workflow-ul și testează-l pe date reale**

```bash
set -a; source .env; set +a
source .venv/bin/activate && python3 scripts/deploy_n8n_workflows.py --push n8n_workflows/jobapp_urls.json
```

Activează-l din interfața n8n, apoi verifică:

```bash
set -a; source .env; set +a
source .venv/bin/activate && python3 -c "
from agents.base_agent import load_config
from sender.n8n_client import N8nClient
urls = N8nClient(load_config()).get_known_urls()
print('URL-uri deja procesate:', len(urls))
print('primele 3:', urls[:3])
"
```

Așteptat: un număr > 0 și URL-uri plauzibile. Dacă întoarce `n8n 5xx`, verifică în n8n că workflow-ul e activ și că nodul Google Sheets folosește credentialul `googleApi` (service account) — credentialele OAuth din instanță au expirat în trecut și dau 500.

- [ ] **Step 8: Verifică dacă există coloana „Keywords Match" în Sheet**

Task 8 pune motivele prefiltrului în `job["keywords_match"]`, dar nimic nu le duce încă în Sheet. Înainte de a adăuga cheia în `FIELD_MAP`, **trebuie confirmat că acea coloană există**: o cheie care nu corespunde niciunui header creează o coloană fantomă la `appendOrUpdate` + `autoMapInputData`.

Vechiul spec (`2026-06-22-...`) listează `Keywords Match`, dar e documentație depășită — nu e dovadă. Citește headerele reale, modificând temporar nodul Code din `JobApp Urls` să întoarcă și cheile:

```javascript
// TEMPORAR — doar pentru a inspecta headerele reale
return [{ json: { headers: Object.keys(items[0].json).filter(k => !k.startsWith('col_')) } }];
```

Salvează, apoi:

```bash
set -a; source .env; set +a
source .venv/bin/activate && python3 -c "
from agents.base_agent import load_config
from sender.n8n_client import N8nClient
c = N8nClient(load_config())
print(c._post('urls', {'action': 'get_urls'}))
"
```

Notează lista exactă. **Revino apoi la nodul Code corect** (proiecția pe 3 coloane de la Step 6) și re-publică.

- Dacă `Keywords Match` **apare** în listă → continuă cu Step 9.
- Dacă **nu apare** → sari peste Step 9. `job["keywords_match"]` rămâne un câmp intern, util la depanare, iar în raportul final menționezi că motivele prefiltrului nu se pot afișa în Sheet fără o coloană nouă adăugată manual.

- [ ] **Step 9: Adaugă `keywords_match` în `FIELD_MAP` (doar dacă Step 8 a confirmat)**

Adaugă testele la finalul lui `tests/test_n8n_mapping.py`:

```python
class TestKeywordsMatch:
    """Motivele prefiltrului ajung în Sheet, ca să fie auditabil de ce a trecut un job."""

    def test_keywords_match_is_written(self):
        from sender.n8n_mapping import job_to_sheet_row
        row = job_to_sheet_row({
            "url": "https://a.com/1",
            "keywords_match": "Python, Django",
        })
        assert row["Keywords Match"] == "Python, Django"

    def test_empty_keywords_match_is_omitted(self):
        """O cheie cu \"\" GOLEȘTE celula la upsert — deci nu se emite deloc."""
        from sender.n8n_mapping import job_to_sheet_row
        row = job_to_sheet_row({"url": "https://a.com/1", "keywords_match": ""})
        assert "Keywords Match" not in row

    def test_missing_keywords_match_is_omitted(self):
        from sender.n8n_mapping import job_to_sheet_row
        row = job_to_sheet_row({"url": "https://a.com/1"})
        assert "Keywords Match" not in row
```

Rulează-le ca să confirmi că eșuează:

```bash
source .venv/bin/activate && python -m pytest tests/test_n8n_mapping.py -q -k KeywordsMatch
```

Așteptat: `KeyError: 'Keywords Match'`.

În `sender/n8n_mapping.py`, adaugă în `FIELD_MAP`, după linia `"notizen": "Notizen",`:

```python
    "keywords_match": "Keywords Match",
```

Bucla existentă din `job_to_sheet_row` sare deja peste valorile goale, deci celelalte două teste trec fără cod suplimentar. Confirmă:

```bash
source .venv/bin/activate && python -m pytest tests/test_n8n_mapping.py -q
```

Așteptat: toate testele PASS.

- [ ] **Step 10: Alimentează indexul la începutul pasului `scrape`**

În `main.py`, în blocul `if args.step in ("scrape", "full"):`, modifică porțiunea adăugată la Task 8 astfel încât seed-ul să se facă înainte de poartă:

```python
            seen = SeenIndex.load(PROJECT_ROOT / "data" / "seen_jobs.json")
            # Seed din Sheet: joburile deja aplicate nu trebuie redescoperite.
            # Sheet-ul e sursa de adevăr; indexul local e doar o oglindă.
            try:
                added = seen.seed_urls(N8nClient(config).get_known_urls())
                if added:
                    print(f"  Index: +{added} URL-uri din Sheet")
            except Exception as e:
                print(f"  ⚠ Seed din Sheet eșuat (continuăm cu indexul local): {e}")

            pipeline_jobs, gate_stats = apply_prefilter_gate(pipeline_jobs, config, seen)
            seen.save()
```

Asigură-te că `N8nClient` e importat în `main.py`:

```bash
source .venv/bin/activate && grep -n "N8nClient" main.py | head
```

Dacă lipsește, adaugă `from sender.n8n_client import N8nClient` lângă celelalte importuri.

- [ ] **Step 11: Rulează suita completă**

```bash
source .venv/bin/activate && python -m pytest -q
```

Așteptat: totul trece.

- [ ] **Step 12: Commit**

```bash
git add n8n_workflows/jobapp_urls.json config/settings.yaml sender/n8n_client.py sender/n8n_mapping.py main.py tests/test_n8n_client.py tests/test_n8n_mapping.py
git commit -m "feat(n8n): workflow JobApp Urls pentru seed-ul indexului de dedupe (JOB-43)"
```

**Înainte de commit**, rulează recenzorul specializat pe diff — orice modificare care atinge `sender/` sau `n8n_workflows/` intră sub incidența lui (vezi `.claude/agents/n8n-sheet-reviewer.md`).

---

### Task 10: Validare pe date reale și documentație

**Files:**
- Modify: `CLAUDE.md`
- Test: rulare manuală (nu intră în suita pytest)

**Interfaces:**
- Consumes: tot ce s-a construit în T1–T9.
- Produces: nimic nou de cod.

- [ ] **Step 1: Rulează scraping-ul real pe ambele piețe și măsoară**

```bash
set -a; source .env; set +a
source .venv/bin/activate && time python3 main.py --step scrape
```

Notează din output: numărul de joburi pe piață, erorile per sursă, statistica prefiltrului și durata totală.

- [ ] **Step 2: Verifică criteriile de succes din spec**

```bash
source .venv/bin/activate && python3 -c "
import json, collections, pathlib
jobs = json.loads(pathlib.Path('data/jobs_scraped.json').read_text())
print('total:', len(jobs))
print('pe piață: ', collections.Counter(j['market'] for j in jobs).most_common())
print('pe platformă:', collections.Counter(j['platform'] for j in jobs).most_common())
ro = [j for j in jobs if j['market'] == 'ro_remote']
print('RO remote:', len(ro), '| toate cu Sprache=en:', all(j['language'] == 'en' for j in ro))
seen = json.loads(pathlib.Path('data/seen_jobs.json').read_text())
print('index dedupe:', len(seen), 'chei')
print('respinse local:', sum(1 for v in seen.values() if v['decision'] == 'respins_prefiltru'))
"
```

Verifică față de spec §10:
1. durata sub ~3 minute;
2. cel puțin două platforme distincte, nu doar `indeed`;
3. prefiltrul respinge ≥ 50% (`respinse local` vs total);
4. piața `ro_remote` produce joburi cu `language = en`.

Dacă un criteriu nu e atins, notează cifra reală în raport. **Nu ajusta pragurile ca să pară îndeplinite** — o sursă moartă e o informație utilă, nu un test de trecut.

- [ ] **Step 3: Confirmă că a doua rulare nu redescoperă aceleași joburi**

```bash
set -a; source .env; set +a
source .venv/bin/activate && python3 main.py --step scrape 2>&1 | grep -i "prefiltru"
```

Așteptat: `deja văzute` aproape egal cu `găsite`, iar `spre analiză` aproape de 0.

- [ ] **Step 4: Actualizează `CLAUDE.md`**

În secțiunea „Arhitectură (big picture)", după paragraful „**Sheet = sursă de adevăr.**", inserează:

```markdown
**Piețe, nu liste plate.** `scraping.markets` din `settings.yaml` definește câte o piață
(`de`, `ro_remote`) cu țară, limbă, `keyword_groups` și surse proprii. Codul primește un
`scrapers.base.Market` și nu știe nimic despre țări — o piață nouă se adaugă în YAML, fără
cod nou. `market.language` ajunge în col „Sprache" și comandă limba scrisorii, de asta un
job din RO nu primește Anschreiben german. Bucla veche `keywords × locations` (49 de apeluri,
majoritatea pe rate-limit) a fost înlocuită cu `keyword_groups × site-uri` (~12), în paralel.

**Poarta dintre scrape și analyze.** `main.apply_prefilter_gate()` rulează `store/seen.py`
(dedupe cu memorie, alimentat din Sheet prin workflow-ul read-only `JobApp Urls`) și
`filters/relevance.py` (scor determinist 0-100 din `scraping.filters`). Doar joburile peste
`min_prescore` ajung la AI. `filters` din `settings.yaml` era cod mort înainte de asta.

**Surse best-effort.** `market.best_effort_sources` listează sursele al căror eșec NU
marchează runul ca `PARTIAL` — LinkedIn (rate-limit agresiv fără proxy) și `ro_boards`
(eJobs/BestJobs, scraping HTML fără API, se strică la redesign). Restul surselor, dacă
eșuează, coboară statusul: `ScraperAgent` nu mai raportează `SUCCESS` când majoritatea
apelurilor au căzut.
```

În secțiunea „n8n — detalii critice", la lista workflow-urilor active, adaugă `JobApp Urls`:

```markdown
`JobApp Urls` (read-only) întoarce doar `Apply URL`, `Status`, `Email Status` pentru tot
tab-ul — alimentează indexul de dedupe. Proiecția pe 3 coloane e intenționată: tab-ul are
~1140 coloane fantomă (`col_29`…`col_1167`), deci o citire completă ar transfera
1086 × 1168 celule.
```

- [ ] **Step 5: Rulează suita completă înainte de commit**

```bash
source .venv/bin/activate && python -m pytest -q
```

Așteptat: totul trece.

- [ ] **Step 6: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: documentează piețele, poarta de prefiltru și JobApp Urls (JOB-44)"
```

- [ ] **Step 7: Raportează rezultatele reale**

Scrie un rezumat scurt cu cifrele măsurate la Step 2 și 3: joburi per piață, per platformă, procentul respins local, durata, și ce surse au eșuat. Menționează explicit dacă LinkedIn sau eJobs s-au dovedit moarte — spec-ul le-a marcat ca risc asumat, iar rezultatul real decide dacă rămân în config.

---

## Note pentru implementator

- **Nu ghici selectori HTML** (Task 6). Descarcă, uită-te, apoi scrie.
- **Nu slăbi un test ca să treacă.** Dacă `test_extracts_jobs_from_real_fixture` întoarce 0, selectorii sunt greșiți, nu testul.
- **Nu atinge `JobApp Pending`.** E trigger-ul de producție al poll-ului.
- **Nu scrie în col M „Status"** din Python. Aparține mașinii de stări a Apps Script.
- **Nu trimite emailuri** ca parte a acestui plan. Pipeline-ul merge până la „gata de trimis"; decizia rămâne umană (`send` în col M).
