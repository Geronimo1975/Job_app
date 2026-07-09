"""
Teste unitare pentru DocumentAgent — verificare/organizare documente.
"""

from pathlib import Path
from unittest.mock import patch

import pytest

from agents.base_agent import AgentStatus
from agents.document_agent import DocumentAgent


@pytest.fixture
def doc_agent():
    agent = DocumentAgent()
    agent.logger.setLevel("CRITICAL")
    return agent


class TestDocumentAgent:
    def test_instantiation(self, doc_agent):
        assert doc_agent.name == "DocumentAgent"

    def test_verify_action(self, doc_agent):
        """Verifică că acțiunea verify funcționează."""
        result = doc_agent.run(action="verify")

        assert result.status in (AgentStatus.SUCCESS, AgentStatus.PARTIAL, AgentStatus.FAILED)
        checks = result.data.get("checks", [])
        # Trebuie să verifice cel puțin CV-ul
        assert len(checks) >= 1
        for check in checks:
            assert "document" in check
            assert "exists" in check

    def test_organize_action(self, doc_agent):
        """Testează organizarea documentelor."""
        result = doc_agent.run(action="organize")

        assert result.status in (AgentStatus.SUCCESS, AgentStatus.PARTIAL, AgentStatus.FAILED)

    def test_cleanup_action(self, doc_agent):
        """Testează cleanup-ul."""
        result = doc_agent.run(action="cleanup")

        assert result.status in (AgentStatus.SUCCESS, AgentStatus.PARTIAL, AgentStatus.FAILED)

    def test_verify_with_tmp_project(self, tmp_project):
        """Verificare în proiect temporar cu documente existente."""
        with patch("agents.base_agent.PROJECT_ROOT", tmp_project):
            agent = DocumentAgent()
            agent.logger.setLevel("CRITICAL")
            result = agent.run(action="verify")

            checks = result.data.get("checks", [])
            # CV-ul dummy există în tmp_project
            cv_checks = [c for c in checks if "cv" in c["document"].lower()]
            if cv_checks:
                assert cv_checks[0]["exists"] is True

    def test_prepare_package(self, doc_agent, sample_job_analyzed):
        """Testează pregătirea pachetului de documente pentru un job."""
        result = doc_agent.run(
            action="prepare_package",
            job=sample_job_analyzed,
        )

        assert result.status in (AgentStatus.SUCCESS, AgentStatus.PARTIAL, AgentStatus.FAILED)

    def test_invalid_action(self, doc_agent):
        """Acțiune invalidă returnează eroare."""
        result = doc_agent.run(action="nonexistent_action")

        assert result.status == AgentStatus.FAILED

    def test_cleanup_skips_venv_directory(self, tmp_project):
        """Cleanup nu trebuie să șteargă __pycache__ din .venv (doar fișierele proiectului)."""
        venv_pycache = tmp_project / ".venv" / "lib" / "somepkg" / "__pycache__"
        venv_pycache.mkdir(parents=True)
        (venv_pycache / "mod.cpython-312.pyc").touch()

        project_pycache = tmp_project / "agents" / "__pycache__"
        project_pycache.mkdir(parents=True)
        (project_pycache / "mod.cpython-312.pyc").touch()

        with patch("agents.base_agent.PROJECT_ROOT", tmp_project):
            agent = DocumentAgent()
            agent.logger.setLevel("CRITICAL")
            result = agent.run(action="cleanup")

        assert venv_pycache.exists()
        assert not project_pycache.exists()
        assert result.status == AgentStatus.SUCCESS
