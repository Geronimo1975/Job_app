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

    def test_exclusion_matches_whole_words_only(self):
        """„VP" nu trebuie să respingă „MVP"."""
        score, _ = prescore(_job("MVP Launch Engineer (Python)"), CFG)
        assert score > 0

    def test_multiword_exclusion_still_rejects(self):
        """Reparația nu trebuie să strice keyword-urile din mai multe cuvinte."""
        score, reasons = prescore(_job("Head of Data Platform"), CFG)
        assert score == HARD_REJECT
        assert any("Head of" in r for r in reasons)


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
        """Șase skill-uri primare în titlu ar da 120 brut — se plafonează la 100."""
        cfg = {
            "skills": {
                "primary": ["Python", "Django", "Java", "Rust", "Kotlin", "Scala"],
                "secondary": [],
            },
            "scraping": {"filters": {"exclude_keywords": [], "min_prescore": 0}},
        }
        score, _ = prescore(
            _job("Python Django Java Rust Kotlin Scala Developer"), cfg
        )
        assert score == 100

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


CFG_MARKETS = {
    "skills": {"primary": ["Python", "Django", "AI/ML"], "secondary": ["Docker"]},
    "scraping": {
        "filters": {"exclude_keywords": ["Director"], "min_prescore": 40},
        "markets": {
            "de": {"keyword_groups": [["Python Developer", "Backend Engineer"],
                                       ["AI Engineer", "Machine Learning Engineer"]]},
            "ro_remote": {"keyword_groups": [["Django Developer"]]},
        },
    },
}


class TestMarketKeywordBonus:
    def test_search_keyword_in_title_passes_threshold(self):
        """„AI Engineer (m/w/d)" e chiar interogarea căutată — trebuie să treacă
        pragul chiar dacă titlul nu conține un skill primar ca „Python"."""
        job = _job("AI Engineer (m/w/d)", "Wir nutzen Python und Docker.", market="de")
        score, reasons = prescore(job, CFG_MARKETS)
        assert score >= 40
        assert any("AI Engineer" in r for r in reasons)

    def test_keyword_bonus_is_capped(self):
        """Trei keyword-uri potrivite nu dau mai mult bonus decât plafonul."""
        job = _job("Python Developer / Backend Engineer / AI Engineer", "", market="de")
        with_cap, _ = prescore(job, CFG_MARKETS)
        # scorul fără plafon ar fi cu 15 mai mare (3×15=45 > plafon 30)
        assert with_cap <= 100
        # verificare directă a plafonului: bonusul efectiv e exact 30, nu 45.
        # titlul conține „Python" (primar, +20) și „ai" ca token separat din
        # skill-ul primar „AI/ML" (via „AI Engineer", +20) → 20+20+30 = 70
        assert with_cap == 70

    def test_job_without_market_uses_all_markets(self):
        """Career pages (fără câmp market): reuniunea keyword-urilor."""
        job = _job("Django Developer", "")  # doar în ro_remote
        score, reasons = prescore(job, CFG_MARKETS)
        assert any("Django Developer" in r for r in reasons)

    def test_no_keyword_no_bonus(self):
        job = _job("Truck Driver", "", market="de")
        score, reasons = prescore(job, CFG_MARKETS)
        assert score == 0
        assert not any(r.startswith("cautare:") for r in reasons)

    def test_keyword_matches_as_phrase_with_boundaries(self):
        """„AI Engineer" se potrivește în „Senior AI Engineer", nu fragmentat."""
        score_senior, _ = prescore(_job("Senior AI Engineer", "", market="de"), CFG_MARKETS)
        score_split, reasons_split = prescore(_job("AI Systems / Test Engineer", "", market="de"), CFG_MARKETS)
        assert score_senior >= 15
        assert not any("AI Engineer" in r for r in reasons_split if r.startswith("cautare:"))


class TestAdHocSearchKeywords:
    """Căutările din dashboard n-au piață în `scraping.markets`."""

    def test_adhoc_keyword_absent_from_settings_earns_its_reason(self):
        """Fără `search_keywords`, „Game Designer" nu există în nicio piață,
        deci n-ar primi niciodată bonus, iar scorul (și ordinea implicită a
        tabelului, `-prescore`) s-ar baza pe keyword-urile altor căutări."""
        job = _job("Senior Game Designer", "Unity, level design.")
        score, reasons = prescore(job, CFG_MARKETS,
                                  search_keywords=["Game Designer"])
        assert "cautare: Game Designer" in reasons
        assert score >= 15

    def test_adhoc_keywords_replace_the_market_union(self):
        """Un titlu care ar fi luat bonus din reuniunea piețelor NU-l mai ia
        când căutarea a fost pe altceva."""
        job = _job("Django Developer", "")
        _, market_reasons = prescore(job, CFG_MARKETS)
        _, adhoc_reasons = prescore(job, CFG_MARKETS,
                                    search_keywords=["Game Designer"])
        assert any("Django Developer" in r for r in market_reasons)
        assert not any(r.startswith("cautare:") for r in adhoc_reasons)

    def test_cli_market_path_is_unchanged(self):
        """Fără argumentul nou, comportamentul e cel dinainte (calea CLI)."""
        job = _job("AI Engineer (m/w/d)", "Wir nutzen Python und Docker.", market="de")
        assert prescore(job, CFG_MARKETS) == prescore(job, CFG_MARKETS,
                                                      search_keywords=None)
        score, reasons = prescore(job, CFG_MARKETS)
        assert score >= 40
        assert "cautare: AI Engineer" in reasons
