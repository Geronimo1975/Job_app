"""
Teste unitare pentru base_agent.py — AgentResult, AgentStatus, load_config, BaseAgent.
"""

from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from agents.base_agent import (
    AgentResult,
    AgentStatus,
    BaseAgent,
    load_config,
    setup_logger,
    PROJECT_ROOT,
)


# ── AgentStatus ────────────────────────────────────────────────

class TestAgentStatus:
    def test_values(self):
        assert AgentStatus.IDLE.value == "idle"
        assert AgentStatus.RUNNING.value == "running"
        assert AgentStatus.SUCCESS.value == "success"
        assert AgentStatus.FAILED.value == "failed"
        assert AgentStatus.PARTIAL.value == "partial"

    def test_all_members(self):
        assert len(AgentStatus) == 5


# ── AgentResult ────────────────────────────────────────────────

class TestAgentResult:
    def test_creation_defaults(self):
        result = AgentResult(agent_name="test", status=AgentStatus.SUCCESS)
        assert result.agent_name == "test"
        assert result.status == AgentStatus.SUCCESS
        assert result.message == ""
        assert result.data == {}
        assert result.errors == []
        assert result.items_processed == 0
        assert result.items_succeeded == 0
        assert result.items_failed == 0
        assert result.finished_at is None

    def test_creation_with_data(self):
        result = AgentResult(
            agent_name="scraper",
            status=AgentStatus.PARTIAL,
            message="3 din 5 reușite",
            data={"jobs": [1, 2, 3]},
            errors=["timeout pe site X"],
            items_processed=5,
            items_succeeded=3,
            items_failed=2,
        )
        assert result.items_processed == 5
        assert result.items_succeeded == 3
        assert result.data["jobs"] == [1, 2, 3]

    def test_duration_sec_without_finished(self):
        result = AgentResult(agent_name="test", status=AgentStatus.RUNNING)
        assert result.duration_sec == 0.0

    def test_duration_sec_with_finished(self):
        start = datetime(2026, 1, 1, 10, 0, 0)
        end = datetime(2026, 1, 1, 10, 0, 5)
        result = AgentResult(
            agent_name="test",
            status=AgentStatus.SUCCESS,
            started_at=start,
            finished_at=end,
        )
        assert result.duration_sec == 5.0

    def test_summary_format(self):
        result = AgentResult(
            agent_name="TestAgent",
            status=AgentStatus.SUCCESS,
            message="Totul OK",
            items_processed=10,
            items_succeeded=10,
            started_at=datetime(2026, 1, 1, 10, 0, 0),
            finished_at=datetime(2026, 1, 1, 10, 0, 2),
        )
        summary = result.summary()
        assert "[TestAgent]" in summary
        assert "success" in summary
        assert "10/10 OK" in summary
        assert "2.0s" in summary
        assert "Totul OK" in summary


# ── load_config ────────────────────────────────────────────────

class TestLoadConfig:
    def test_loads_valid_config(self):
        config = load_config()
        assert isinstance(config, dict)
        assert "applicant" in config
        assert "skills" in config
        assert "email" in config

    def test_missing_config_raises(self, tmp_path):
        with patch("agents.base_agent.PROJECT_ROOT", tmp_path):
            with pytest.raises(FileNotFoundError, match="Config lipsă"):
                load_config()


# ── setup_logger ───────────────────────────────────────────────

class TestSetupLogger:
    def test_creates_logger(self, tmp_path):
        logger = setup_logger("test_logger_unit", log_dir=tmp_path)
        assert logger.name == "test_logger_unit"
        assert len(logger.handlers) >= 2  # console + file

    def test_reuses_existing_logger(self, tmp_path):
        logger1 = setup_logger("test_reuse", log_dir=tmp_path)
        logger2 = setup_logger("test_reuse", log_dir=tmp_path)
        assert logger1 is logger2

    def test_creates_log_file(self, tmp_path):
        setup_logger("test_file_creation", log_dir=tmp_path)
        log_files = list(tmp_path.glob("test_file_creation_*.log"))
        assert len(log_files) == 1


# ── BaseAgent ──────────────────────────────────────────────────

class TestBaseAgent:
    def test_cannot_instantiate_abstract(self):
        with pytest.raises(TypeError):
            BaseAgent("test")

    def test_concrete_subclass(self):
        class DummyAgent(BaseAgent):
            def run(self, **kwargs):
                return AgentResult(
                    agent_name=self.name,
                    status=AgentStatus.SUCCESS,
                    message="done",
                )

        agent = DummyAgent("dummy")
        assert agent.name == "dummy"
        assert agent.status == AgentStatus.IDLE
        assert isinstance(agent.config, dict)
        assert agent.project_root == PROJECT_ROOT

    def test_run_returns_result(self):
        class DummyAgent(BaseAgent):
            def run(self, **kwargs):
                return AgentResult(
                    agent_name=self.name,
                    status=AgentStatus.SUCCESS,
                    message="OK",
                )

        agent = DummyAgent()
        result = agent.run()
        assert result.status == AgentStatus.SUCCESS

    def test_start_sets_running(self):
        class DummyAgent(BaseAgent):
            def run(self, **kwargs):
                start = self._start()
                assert self.status == AgentStatus.RUNNING
                return AgentResult(
                    agent_name=self.name,
                    status=AgentStatus.SUCCESS,
                    started_at=start,
                )

        agent = DummyAgent()
        agent.run()

    def test_finish_sets_status(self):
        class DummyAgent(BaseAgent):
            def run(self, **kwargs):
                start = self._start()
                result = AgentResult(
                    agent_name=self.name,
                    status=AgentStatus.SUCCESS,
                    started_at=start,
                )
                return self._finish(result)

        agent = DummyAgent()
        result = agent.run()
        assert agent.status == AgentStatus.SUCCESS
        assert result.finished_at is not None

    def test_get_path(self):
        class DummyAgent(BaseAgent):
            def run(self, **kwargs):
                pass

        agent = DummyAgent()
        # Testeaza cu o cheie de config care exista
        path = agent.get_path("output.logs_dir")
        assert isinstance(path, Path)
        assert "output" in str(path)

    def test_get_path_invalid_key(self):
        class DummyAgent(BaseAgent):
            def run(self, **kwargs):
                pass

        agent = DummyAgent()
        with pytest.raises(ValueError, match="nu este un path valid"):
            agent.get_path("nonexistent.key")
