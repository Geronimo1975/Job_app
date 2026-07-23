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
