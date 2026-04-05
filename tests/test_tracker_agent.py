"""
Teste unitare pentru TrackerAgent — mock Excel file, fără I/O real.
"""

from pathlib import Path
from unittest.mock import patch, MagicMock, PropertyMock

import pytest

from agents.base_agent import AgentStatus
from agents.tracker_agent import TrackerAgent


@pytest.fixture
def tracker_agent(tmp_path):
    """TrackerAgent cu path-uri redirecționate în tmp_path."""
    with patch("agents.base_agent.PROJECT_ROOT", tmp_path):
        # Creează structura necesară
        (tmp_path / "config").mkdir(exist_ok=True)
        (tmp_path / "data").mkdir(exist_ok=True)
        (tmp_path / "data" / "backups").mkdir(exist_ok=True)
        (tmp_path / "output" / "logs").mkdir(parents=True, exist_ok=True)
        (tmp_path / "output" / "reports").mkdir(parents=True, exist_ok=True)

        import yaml
        config = {
            "tracker": {
                "file": "data/Job_Application_Tracker.xlsx",
                "auto_backup": False,
                "backup_dir": "data/backups",
            },
            "output": {
                "logs_dir": "output/logs",
                "reports_dir": "output/reports",
                "log_level": "WARNING",
            },
            "applicant": {"name": "Test User"},
            "skills": {"primary": [], "secondary": [], "gaming_specific": []},
            "documents": {"cover_letters_dir": "documents/cover_letters/generated"},
        }
        with open(tmp_path / "config" / "settings.yaml", "w") as f:
            yaml.dump(config, f)

        agent = TrackerAgent()
        agent.logger.setLevel("CRITICAL")
        return agent


class TestTrackerAgent:
    def test_instantiation(self, tracker_agent):
        assert tracker_agent.name == "TrackerAgent"

    def test_add_jobs(self, tracker_agent, sample_jobs):
        """Testează adăugarea de joburi în tracker."""
        result = tracker_agent.run(action="add_jobs", jobs=sample_jobs)

        assert result.status in (AgentStatus.SUCCESS, AgentStatus.PARTIAL)
        assert result.items_processed == 2

    def test_add_single_job(self, tracker_agent, sample_job):
        """Adăugare un singur job."""
        result = tracker_agent.run(action="add_jobs", jobs=[sample_job])

        assert result.status in (AgentStatus.SUCCESS, AgentStatus.PARTIAL)
        assert result.items_processed == 1

    def test_add_empty_list(self, tracker_agent):
        """Adăugare listă goală."""
        result = tracker_agent.run(action="add_jobs", jobs=[])

        assert result.items_processed == 0

    def test_report_action(self, tracker_agent, sample_job):
        """Testează generarea raportului."""
        # Mai întâi adăugăm un job
        tracker_agent.run(action="add_jobs", jobs=[sample_job])

        result = tracker_agent.run(action="report")
        assert result.status in (AgentStatus.SUCCESS, AgentStatus.PARTIAL)

    def test_sync_without_data_files(self, tracker_agent):
        """Sync când nu există fișiere JSON de date."""
        result = tracker_agent.run(action="sync")

        # Trebuie să gestioneze lipsa fișierelor
        assert result.status in (AgentStatus.SUCCESS, AgentStatus.FAILED, AgentStatus.PARTIAL)

    def test_update_status(self, tracker_agent, sample_job_analyzed):
        """Testează actualizarea statusului."""
        # Adăugăm jobul mai întâi
        tracker_agent.run(action="add_jobs", jobs=[sample_job_analyzed])

        result = tracker_agent.run(
            action="update_status",
            jobs=[sample_job_analyzed],
        )
        assert result.status in (AgentStatus.SUCCESS, AgentStatus.PARTIAL)

    def test_add_jobs_sets_row(self, tracker_agent, sample_jobs):
        """Testează că _add_jobs setează _row pe fiecare job dict."""
        result = tracker_agent.run(action="add_jobs", jobs=sample_jobs)

        assert result.status == AgentStatus.SUCCESS
        # Verifică _row setat direct pe job dicts
        for job in sample_jobs:
            assert "_row" in job, f"_row lipsește pentru {job.get('company')}"
            assert isinstance(job["_row"], int)
            assert job["_row"] >= 2  # rândul 1 e header

        # Verifică propagarea prin result.data
        returned_jobs = result.data.get("jobs", [])
        assert len(returned_jobs) == len(sample_jobs)
        for job in returned_jobs:
            assert "_row" in job

    def test_row_propagation_add_then_update(self, tracker_agent, sample_jobs):
        """Testează propagarea _row: add_jobs → update_status."""
        # Adăugăm joburi — _row trebuie setat
        add_result = tracker_agent.run(action="add_jobs", jobs=sample_jobs)
        assert add_result.status == AgentStatus.SUCCESS

        # Adăugăm date de analiză pe joburi
        for job in sample_jobs:
            job["analysis"] = {"match_score": 80, "matching_skills": ["Python"]}
            job["pipeline_status"] = "Analysiert"

        # Update cu aceleași joburi — _row trebuie să funcționeze
        update_result = tracker_agent.run(action="update_status", jobs=sample_jobs)
        assert update_result.status == AgentStatus.SUCCESS
        assert update_result.items_succeeded == len(sample_jobs)
