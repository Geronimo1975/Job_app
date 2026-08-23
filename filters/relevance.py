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

# Bonus pentru semnale de flexibilitate (part-time/freelance/remote…): profilul
# caută explicit angajamente sub 40h, deci flexibilitatea E relevanță, nu
# doar un beneficiu. Plafonat: două semnale ajung.
_W_FLEX = 10
_MAX_FLEX_BONUS = 20

# Penalizare pentru rol greșit în titlu (sales, consultant SAP…): mare cât să
# scufunde jobul sub min_prescore, dar NU hard-reject — un titlu cu multe
# semnale bune poate compensa (spre deosebire de exclude_keywords).
_W_NEGATIVE = -60

# Separatorii după care titlul trece de la ROL la calificativ („– Conversational
# AI & Sales Solutions", ", Sales Enablement", "(m/w/d)"). Penalizarea de rol se
# uită doar în capul titlului: „Junior AI Engineer – … & Sales Solutions" e un
# post de AI Engineer, nu de vânzări, dar cădea la 0 (găsit live 2026-08-06).
# Cratima intră în listă DOAR încadrată de spații: „Pre-Sales"/„Full-Stack" sunt
# cuvinte compuse, nu titlu urmat de calificativ.
_TITLE_QUALIFIER_SPLIT = re.compile(r"[–—|(,:]|\s-\s")


def _role_head(title: str) -> str:
    """Partea din titlu care spune ce E jobul, fără calificative."""
    return _TITLE_QUALIFIER_SPLIT.split(title, maxsplit=1)[0]


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


def prescore(job: dict, config: dict,
             search_keywords: list[str] | None = None) -> tuple[int, list[str]]:
    """Calculează un scor 0-100 (sau HARD_REJECT) plus motivele.

    Motivele ajung în coloana „Keywords Match" din Sheet, ca să fie
    auditabil de ce a trecut un job.

    `search_keywords` e pentru căutările AD-HOC (dashboard), care nu au o
    piață în `scraping.markets`: fără el, `_market_keywords` cade pe reuniunea
    keyword-urilor TUTUROR piețelor, deci o căutare de „Game Designer" era
    punctată — și ordonată, tabelul sortează implicit pe `-prescore` — cu
    bonusurile „Python Developer"/„AI Engineer" și nu primea niciodată un
    motiv `cautare: …` pentru ce s-a căutat efectiv. Calea CLI nu-l dă, deci
    scoringul pe piețe rămâne bit-identic.
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

    # ── Penalizare rol nepotrivit: doar pe CAPUL titlului, cuvânt întreg ──
    # (pe descriere ar penaliza „work with the sales team"; pe calificativ ar
    # penaliza „AI Engineer – … & Sales Solutions", care e tocmai un post bun)
    role_head = _role_head(title_l)
    for negative in filters.get("negative_keywords", []) or []:
        if _contains(role_head, negative.lower()):
            score += _W_NEGATIVE
            reasons.append(f"rol nepotrivit: {negative}")

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
    keywords = (search_keywords if search_keywords is not None
                else _market_keywords(job, config))
    for keyword in keywords:
        if _contains(title_l, keyword.lower()):
            keyword_bonus += _W_MARKET_KEYWORD
            reasons.append(f"cautare: {keyword}")
    score += min(keyword_bonus, _MAX_KEYWORD_BONUS)

    # ── Bonus flexibilitate: titlu SAU descriere ────────────────
    # (aici descrierea contează — „remote, part-time possible" apare rar în titlu)
    flex_bonus = 0
    for signal in filters.get("flex_signals", []) or []:
        if _contains(text_l, signal.lower()):
            flex_bonus += _W_FLEX
            reasons.append(f"flexibil: {signal}")
    score += min(flex_bonus, _MAX_FLEX_BONUS)

    return max(0, min(score, 100)), reasons


def requires_email(job: dict, config: dict) -> bool:
    """True dacă jobul are nevoie de adresă de contact ca să fie reținut.

    Regula e per PIAȚĂ, nu globală, fiindcă emailul nu există peste tot:
    anunțurile DE/NL scriu „Bewerbung an …", pe când cele din România (și din
    piețele remote/EN) trimit la un portal — măsurat 2026-08-06, eJobs dă un
    email la 106 anunțuri. O regulă globală ar goli exact piețele fără email.
    `scraping.markets.<piață>.require_email` bate `scraping.filters.
    require_email`; joburile fără piață cunoscută urmează regula globală.
    """
    scraping = config.get("scraping", {}) or {}
    market_cfg = (scraping.get("markets", {}) or {}).get(job.get("market")) or {}
    if "require_email" in market_cfg:
        return bool(market_cfg["require_email"])
    return bool((scraping.get("filters", {}) or {}).get("require_email", False))


# Cuvinte întregi: `\bremote\b` (deci NU „remotely"), ca în `_is_remote_row`
# din jobspy_scraper. Formulările românești sunt necesare fiindcă eJobs scrie
# anunțurile în română.
_REMOTE_RE = re.compile(
    r"\b(remote|telemunc[ăa]|la distan[țt][ăa]|work from home)\b", re.I
)
# Hibridul NU e remote. Veto-ul se uită DOAR la titlu+locație: acolo „hibrid"
# e o clasificare a postului, pe când în descriere e proză („colegii din alte
# echipe lucrează hibrid") și ar respinge anunțuri remote adevărate.
_HYBRID_RE = re.compile(r"\b(hibrid|hybrid)\b", re.I)


def is_remote_job(job: dict) -> bool:
    """True dacă jobul canonic descrie o poziție remote.

    Diferă intenționat de `_is_remote_row` (jobspy_scraper), care lucrează pe
    rândul BRUT și se oprește la titlu+locație: aici jobul e deja normalizat și
    ARE descriere — necesar fiindcă eJobs pune „Romania" în locație la toate
    anunțurile, deci corpul e singurul semnal rămas.
    """
    flag = (job.get("raw") or {}).get("is_remote")
    if flag is True or str(flag).strip().lower() == "true":
        return True
    label = f"{job.get('title') or ''} {job.get('location') or ''}"
    if _HYBRID_RE.search(label):
        return False
    return _REMOTE_RE.search(f"{label} {job.get('description') or ''}") is not None


def requires_remote(job: dict, config: dict) -> bool:
    """True dacă jobul trebuie să fie remote ca să fie reținut.

    Opt-in și SEPARAT de `market.remote`, care spune ce CEREM sursei (ajunge
    `is_remote` la jobspy) — nu ce PĂSTRĂM. Confundându-le, piața
    `remote_global` s-ar goli: LinkedIn filtrează deja upstream și nu
    completează câmpul, deci a doua filtrare ar tăia exact rândurile bune
    (vezi JOB-48). Ca la `require_email`, regula e per piață cu fallback
    global, fiindcă „doar remote" are sens pe unele piețe și golește altele.
    """
    scraping = config.get("scraping", {}) or {}
    market_cfg = (scraping.get("markets", {}) or {}).get(job.get("market")) or {}
    if "require_remote" in market_cfg:
        return bool(market_cfg["require_remote"])
    return bool((scraping.get("filters", {}) or {}).get("require_remote", False))


# ── Limba anunțului ──────────────────────────────────────────
# Verdictele posibile ale detecției. „Necunoscut" NU e „străin": 77% dintre
# anunțuri n-au descriere (LinkedIn, măsurat 2026-08-23), iar a le trata ca
# străine ar goli exact piețele bune — capcana din JOB-48.
LANG_UNKNOWN = ""
LANG_OTHER = "alta"

# Sub atâtea cuvinte nu există semnal, oricâți marcatori s-ar potrivi
# întâmplător. Un titlu singur are ~5 cuvinte, deci cade aici.
_LANG_MIN_WORDS = 20
# Densitatea minimă de cuvinte-funcție ale unei limbi cunoscute. Sub prag,
# textul e destul de lung ca să existe semnal, dar niciuna dintre limbile
# noastre nu-l explică → e altă limbă.
_LANG_MIN_DENSITY = 0.045

# Marcatori = cuvinte-funcție frecvente, alese DISTINCTIV. Româna împarte
# cuvinte scurte cu spaniola/italiana (`de`, `la`, `un`, `este`), iar germana
# cu nordicele (`for`, `en`) — de aceea listele evită tocmai suprapunerile și
# păstrează ce e specific: `pentru`/`nostru` (nu `de`), `und`/`für` (nu `en`).
_LANG_MARKERS: dict[str, frozenset[str]] = {
    "en": frozenset({
        "the", "and", "with", "for", "you", "are", "our", "will", "have", "this",
        "that", "from", "your", "we", "is", "to", "of", "as", "be", "work",
        "experience", "team", "skills", "role", "about", "who", "what",
    }),
    "de": frozenset({
        "und", "der", "die", "das", "mit", "für", "wir", "sie", "ist", "sind",
        "ein", "eine", "den", "dem", "von", "zu", "im", "auf", "nicht", "oder",
        "bei", "auch", "uns", "unser", "unsere", "dich", "du", "sowie", "durch",
    }),
    "ro": frozenset({
        "și", "si", "sau", "pentru", "care", "sunt", "este", "să", "sa", "cu",
        "din", "prin", "către", "catre", "nostru", "noastre", "noastră", "noastra",
        "vei", "vom", "ești", "esti", "echipa", "echipă", "dezvoltare", "cerințe",
        "cerinte", "experiență", "experienta", "responsabilități", "responsabilitati",
        "căutăm", "cautam", "alături", "alaturi",
    }),
}
# Limbile pe care le RECUNOAȘTEM ca fiind străine. Respingerea se face doar pe
# dovadă POZITIVĂ („asta e suedeză"), nu pe absența marcatorilor noștri: o
# descriere care e listă de keyword-uri („Software Engineer, PHP Developer, …")
# n-are cuvinte-funcție în nicio limbă, iar regula „nu recunosc → străin" o
# respingea (caz real: „Inginer Software – Piatra Neamt", anunț ROMÂNESC).
# Acoperă limbile piețelor scrapate; una neacoperită iese „necunoscut", deci
# trece — consecvent cu fail-open.
_FOREIGN_MARKERS: dict[str, frozenset[str]] = {
    "sv": frozenset({"och", "att", "som", "med", "för", "är", "vi", "du", "av",
                     "på", "det", "en", "ett", "till", "har", "kan", "söker"}),
    "nb": frozenset({"og", "som", "med", "til", "vi", "du", "av", "på", "det",
                     "en", "et", "har", "kan", "søker", "være", "eller", "for"}),
    "da": frozenset({"og", "som", "med", "til", "vi", "du", "af", "på", "det",
                     "en", "et", "har", "kan", "søger", "være", "eller"}),
    "fi": frozenset({"ja", "on", "sekä", "että", "ovat", "sinä", "meidän",
                     "työ", "kanssa", "tai", "voit", "haemme", "osaamista"}),
    "es": frozenset({"que", "los", "las", "para", "con", "una", "por", "del",
                     "nuestro", "nuestra", "buscamos", "trabajo", "equipo",
                     "experiencia", "desarrollo", "como", "más"}),
    "pt": frozenset({"que", "para", "com", "uma", "por", "dos", "das", "nosso",
                     "nossa", "procuramos", "trabalho", "equipa", "equipe",
                     "experiência", "desenvolvimento", "como", "mais", "você"}),
    "pl": frozenset({"oraz", "dla", "jest", "się", "nie", "lub", "przez", "praca",
                     "nasze", "naszym", "doświadczenie", "zespół", "będziesz",
                     "który", "która", "które", "wraz"}),
    "cs": frozenset({"pro", "jsme", "jsou", "nebo", "naše", "našeho", "práce",
                     "tým", "zkušenosti", "budeš", "který", "která", "které",
                     "také", "své"}),
    "nl": frozenset({"een", "het", "van", "voor", "met", "wij", "zijn", "onze",
                     "jij", "werk", "ervaring", "team", "bij", "als", "naar"}),
    "fr": frozenset({"les", "des", "une", "pour", "avec", "vous", "nous", "est",
                     "sont", "notre", "votre", "dans", "sur", "qui", "que"}),
    "it": frozenset({"che", "per", "con", "una", "del", "della", "nostro",
                     "nostra", "sono", "gli", "nel", "alla", "esperienza"}),
}
_WORD_RE = re.compile(r"[^\W\d_]+", re.UNICODE)


def _best_match(words: list[str], markers: dict[str, frozenset[str]]) -> tuple[str, float]:
    """Limba cu cei mai mulți marcatori și densitatea ei."""
    scores = {lang: sum(1 for w in words if w in marks)
              for lang, marks in markers.items()}
    best = max(scores, key=lambda lang: scores[lang])
    return best, scores[best] / len(words)


def detect_job_language(job: dict) -> str:
    """Limba în care e SCRIS anunțul: `en`/`de`/`ro`, `alta`, sau necunoscut.

    Diferă de `agents.base_agent.job_language()`, care spune în ce limbă se
    face CANDIDATURA (analiză AI → piață → implicit) și nu se uită niciodată la
    textul anunțului. Diferă și de `AnalyzerAgent._detect_language`, care e
    binar DE/EN și rulează în pasul `analyze` — adică după ce s-a plătit deja
    apelul AI. Poarta are nevoie de verdictul al treilea, „clar altă limbă",
    ca să poată respinge un anunț suedez înainte de orice cheltuială.
    """
    text = f"{job.get('title') or ''} {job.get('description') or ''}".lower()
    words = _WORD_RE.findall(text)
    if len(words) < _LANG_MIN_WORDS:
        return LANG_UNKNOWN

    known, known_density = _best_match(words, _LANG_MARKERS)
    foreign, foreign_density = _best_match(words, _FOREIGN_MARKERS)

    # Cea mai bine reprezentată limbă câștigă. Comparația e necesară fiindcă
    # listele se ating (germana și nordicele împart „for"/„en"), deci un anunț
    # german lovește și marcatori `da`/`nb` — dar mult mai slab decât cei `de`.
    if known_density >= _LANG_MIN_DENSITY and known_density >= foreign_density:
        return known
    if foreign_density >= _LANG_MIN_DENSITY:
        return LANG_OTHER
    # Destul text, dar nicio limbă nu-l explică: nu e proză (listă de
    # keyword-uri). „Nu știu" — deci trece.
    return LANG_UNKNOWN


def language_allowed(job: dict, config: dict) -> bool:
    """True dacă limba anunțului e una dintre cele acceptate.

    Regulă per piață cu fallback global, ca `requires_email`/`requires_remote`.
    Listă goală sau absentă = fără regulă (opt-in: o config existentă nu începe
    brusc să taie rânduri). Verdictul „necunoscut" trece ÎNTOTDEAUNA — vezi
    `LANG_UNKNOWN`.
    """
    scraping = config.get("scraping", {}) or {}
    market_cfg = (scraping.get("markets", {}) or {}).get(job.get("market")) or {}
    allowed = market_cfg.get("allowed_languages")
    if allowed is None:
        allowed = (scraping.get("filters", {}) or {}).get("allowed_languages")
    if not allowed:
        return True

    detected = detect_job_language(job)
    if detected == LANG_UNKNOWN:
        return True
    return detected in {str(x).strip().lower() for x in allowed}


def passes_prefilter(job: dict, config: dict) -> bool:
    """True dacă jobul merită cheltuiala de analiză AI."""
    score, _ = prescore(job, config)
    if score == HARD_REJECT:
        return False
    filters = (config.get("scraping", {}) or {}).get("filters", {}) or {}
    return score >= int(filters.get("min_prescore", 0))
