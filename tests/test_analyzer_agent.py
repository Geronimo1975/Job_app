"""
Teste unitare pentru AnalyzerAgent — keyword matching fără AI.
"""

from unittest.mock import patch, MagicMock

import pytest

from agents.base_agent import AgentStatus
from agents.analyzer_agent import AnalyzerAgent


@pytest.fixture
def analyzer():
    agent = AnalyzerAgent()
    agent.logger.setLevel("CRITICAL")
    return agent


class TestAnalyzerAgent:
    def test_instantiation(self, analyzer):
        assert analyzer.name == "AnalyzerAgent"
        assert analyzer.status == AgentStatus.IDLE

    def test_detect_language_german(self, analyzer):
        txt = "wir suchen einen entwickler für unser team mit erfahrung und kenntnissen"
        assert analyzer._detect_language(txt) == "de"

    def test_detect_language_english(self, analyzer):
        txt = "we are looking for an engineer with experience in your team and strong skills"
        assert analyzer._detect_language(txt) == "en"

    def test_detect_language_substring_not_misclassified(self, analyzer):
        # 'around'/'background' conțin 'und' ca substring — euristica veche le
        # clasifica greșit drept germană. Word-boundary trebuie să dea 'en'.
        txt = "you work in the background around our team with experience and skills"
        assert analyzer._detect_language(txt) == "en"

    def test_keyword_analysis_high_match(self, analyzer, sample_job):
        """Job cu multe skill-uri matching => scor mare."""
        result = analyzer.run(jobs=[sample_job], use_ai=False, min_score=0)

        assert result.status in (AgentStatus.SUCCESS, AgentStatus.PARTIAL)
        analyzed = result.data.get("analyzed_jobs", [])
        assert len(analyzed) >= 1

        job = analyzed[0]
        analysis = job.get("analysis", {})
        assert "match_score" in analysis
        assert analysis["match_score"] > 0
        # Python, Django, Docker, Git ar trebui sa fie in matching_skills
        matching = [s.lower() for s in analysis.get("matching_skills", [])]
        assert "python" in matching

    def test_keyword_analysis_low_match(self, analyzer):
        """Job fără skill-uri relevante => scor mic."""
        job = {
            "id": "no_match",
            "title": "Florist (m/w/d)",
            "company": "Blumen GmbH",
            "description": "Wir suchen einen erfahrenen Floristen für unser Geschäft.",
            "location": "Berlin",
            "status": "new",
        }
        result = analyzer.run(jobs=[job], use_ai=False, min_score=0)

        analyzed = result.data.get("analyzed_jobs", [])
        if analyzed:
            assert analyzed[0]["analysis"]["match_score"] < 30

    def test_min_score_filter(self, analyzer, sample_job):
        """Testează filtrarea după scor minim."""
        result = analyzer.run(jobs=[sample_job], use_ai=False, min_score=100)

        qualified = result.data.get("qualified", 0)
        # Cu min_score=100, e puțin probabil să treacă
        # Dar testăm că filtrarea funcționează
        analyzed = result.data.get("analyzed_jobs", [])
        for job in analyzed:
            assert job["analysis"]["match_score"] >= 100

    def test_empty_jobs_list(self, analyzer):
        """Run cu lista goală de joburi."""
        result = analyzer.run(jobs=[], use_ai=False, min_score=0)

        assert result.status in (AgentStatus.SUCCESS, AgentStatus.FAILED)
        assert result.items_processed == 0

    def test_multiple_jobs(self, analyzer, sample_jobs):
        """Analiza mai multor joburi simultan."""
        result = analyzer.run(jobs=sample_jobs, use_ai=False, min_score=0)

        assert result.items_processed == 2
        analyzed = result.data.get("analyzed_jobs", [])
        # Ambele joburi au skill-uri relevante
        assert len(analyzed) >= 1

    def test_no_ai_flag(self, analyzer, sample_job):
        """Confirmă că use_ai=False nu apelează API-ul."""
        with patch("agents.analyzer_agent.anthropic", None):
            result = analyzer.run(jobs=[sample_job], use_ai=False, min_score=0)
            assert result.status in (AgentStatus.SUCCESS, AgentStatus.PARTIAL)

    def test_result_sorted_by_score(self, analyzer, sample_jobs):
        """Verifică sortarea descrescătoare după match_score."""
        result = analyzer.run(jobs=sample_jobs, use_ai=False, min_score=0)

        analyzed = result.data.get("analyzed_jobs", [])
        if len(analyzed) >= 2:
            scores = [j["analysis"]["match_score"] for j in analyzed]
            assert scores == sorted(scores, reverse=True)
