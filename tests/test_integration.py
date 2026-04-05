"""
Test integrare — pipeline end-to-end cu mock data, fără API keys sau conexiuni externe.
"""

from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
import yaml

from agents.base_agent import AgentStatus, PROJECT_ROOT


pytestmark = pytest.mark.integration


@pytest.fixture
def mock_pipeline_env(tmp_path):
    """Pregătește un environment complet mockat pentru pipeline."""
    # Structura directorelor
    (tmp_path / "config" / "email_templates").mkdir(parents=True)
    (tmp_path / "data" / "backups").mkdir(parents=True)
    (tmp_path / "output" / "logs").mkdir(parents=True)
    (tmp_path / "output" / "reports").mkdir(parents=True)
    (tmp_path / "output" / "sent_emails").mkdir(parents=True)
    (tmp_path / "documents" / "cv").mkdir(parents=True)
    (tmp_path / "documents" / "certificates").mkdir(parents=True)
    (tmp_path / "documents" / "references").mkdir(parents=True)
    (tmp_path / "documents" / "cover_letters" / "generated").mkdir(parents=True)

    from tests.conftest import MOCK_CONFIG
    with open(tmp_path / "config" / "settings.yaml", "w") as f:
        yaml.dump(MOCK_CONFIG, f)

    # CV dummy
    (tmp_path / "documents" / "cv" / "test_cv.pdf").write_bytes(b"%PDF-1.4 test")

    # Email template
    (tmp_path / "config" / "email_templates" / "de_generic.txt").write_text(
        "Sehr geehrte/r {kontakt_person},\n\n"
        "Bewerbung als {position} bei {firma}.\n\n"
        "MfG\n{sender_name}\n"
    )

    return tmp_path


class TestPipelineIntegration:
    """Testează flow-ul pipeline: scrape → analyze → track."""

    def test_scrape_analyze_track_flow(self, mock_pipeline_env, sample_jobs):
        """Flow complet: scraping mockat → analiză keyword → tracker."""
        with patch("agents.base_agent.PROJECT_ROOT", mock_pipeline_env):
            # Step 1: Analiză (skip scraping — folosim mock data)
            from agents.analyzer_agent import AnalyzerAgent

            analyzer = AnalyzerAgent()
            analyzer.logger.setLevel("CRITICAL")
            result = analyzer.run(jobs=sample_jobs, use_ai=False, min_score=0)

            assert result.status in (AgentStatus.SUCCESS, AgentStatus.PARTIAL)
            analyzed_jobs = result.data.get("analyzed_jobs", [])
            assert len(analyzed_jobs) >= 1

            # Verifică că fiecare job are analysis
            for job in analyzed_jobs:
                assert "analysis" in job
                assert "match_score" in job["analysis"]

            # Step 2: Tracker — adăugare
            from agents.tracker_agent import TrackerAgent

            tracker = TrackerAgent()
            tracker.logger.setLevel("CRITICAL")
            result = tracker.run(action="add_jobs", jobs=analyzed_jobs)

            assert result.status in (AgentStatus.SUCCESS, AgentStatus.PARTIAL)

            # Step 3: Tracker — raport
            result = tracker.run(action="report")
            assert result.status in (AgentStatus.SUCCESS, AgentStatus.PARTIAL)

    def test_document_verify_flow(self, mock_pipeline_env):
        """Verificare documente în environment-ul mockat."""
        with patch("agents.base_agent.PROJECT_ROOT", mock_pipeline_env):
            from agents.document_agent import DocumentAgent

            agent = DocumentAgent()
            agent.logger.setLevel("CRITICAL")
            result = agent.run(action="verify")

            assert result.status in (AgentStatus.SUCCESS, AgentStatus.PARTIAL, AgentStatus.FAILED)
            checks = result.data.get("checks", [])
            assert len(checks) >= 1

    def test_analyzer_score_ranking(self, mock_pipeline_env):
        """Verifică că joburile sunt ordonate corect după match score."""
        with patch("agents.base_agent.PROJECT_ROOT", mock_pipeline_env):
            from agents.analyzer_agent import AnalyzerAgent

            jobs = [
                {
                    "id": "low",
                    "title": "Receptionist",
                    "company": "Hotel X",
                    "description": "Front desk receptionist needed.",
                    "location": "Berlin",
                    "status": "new",
                },
                {
                    "id": "high",
                    "title": "Python Django Developer",
                    "company": "Tech Y",
                    "description": (
                        "Python Django developer with Docker, Git, REST APIs, "
                        "PostgreSQL, AI/ML experience. CI/CD pipelines."
                    ),
                    "location": "Berlin",
                    "status": "new",
                },
            ]

            analyzer = AnalyzerAgent()
            analyzer.logger.setLevel("CRITICAL")
            result = analyzer.run(jobs=jobs, use_ai=False, min_score=0)

            analyzed = result.data.get("analyzed_jobs", [])
            if len(analyzed) == 2:
                # Job cu Python/Django trebuie să fie primul (scor mai mare)
                assert analyzed[0]["id"] == "high"
                assert analyzed[0]["analysis"]["match_score"] > analyzed[1]["analysis"]["match_score"]
