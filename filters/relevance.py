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

# Bonus pentru un keyword de căutare al pieței găsit în titlu: jobul e exact
# ce am căutat, nu doar unul care menționează un skill în treacăt.
_W_MARKET_KEYWORD = 15
# Bonusul total de keyword e plafonat: două keyword-uri potrivite ajung ca
# semnal; mai multe nu fac jobul mai relevant.
_MAX_KEYWORD_BONUS = 30


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


def _market_keywords(job: dict, config: dict) -> list[str]:
    """Keyword-urile de căutare relevante pentru piața jobului.

    Joburile fără câmp `market` (ex. career pages, scraper pre-piețe) primesc
    reuniunea keyword-urilor din toate piețele.
    """
    markets_cfg = (config.get("scraping", {}) or {}).get("markets", {}) or {}
    market_key = job.get("market")
    if market_key in markets_cfg:
        groups = markets_cfg[market_key].get("keyword_groups", []) or []
    else:
        groups = [g for m in markets_cfg.values()
                  for g in (m.get("keyword_groups", []) or [])]
    return [kw for group in groups for kw in group]


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
    # Potrivire pe cuvânt întreg, la fel ca la skill-uri: cu `in` simplu,
    # keyword-ul „VP" respingea „MVP Launch Engineer".
    for excluded in filters.get("exclude_keywords", []) or []:
        if _contains(title_l, excluded.lower()):
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

    # ── Bonus keyword de piață (spec §5.3) ─────────────────────
    # Un titlu care conține chiar interogarea căutată („AI Engineer") e
    # semnalul cel mai puternic — fără bonus, astfel de joburi cădeau sub
    # prag dacă titlul nu repeta și un skill primar (ex. „Python").
    keyword_bonus = 0
    for keyword in _market_keywords(job, config):
        if _contains(title_l, keyword.lower()):
            keyword_bonus += _W_MARKET_KEYWORD
            reasons.append(f"cautare: {keyword}")
    score += min(keyword_bonus, _MAX_KEYWORD_BONUS)

    return min(score, 100), reasons


def passes_prefilter(job: dict, config: dict) -> bool:
    """True dacă jobul merită cheltuiala de analiză AI."""
    score, _ = prescore(job, config)
    if score == HARD_REJECT:
        return False
    filters = (config.get("scraping", {}) or {}).get("filters", {}) or {}
    return score >= int(filters.get("min_prescore", 0))
