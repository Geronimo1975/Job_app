from unittest.mock import patch, MagicMock

import pytest

from agents.base_agent import AgentStatus


@pytest.fixture
def agent(tmp_path, monkeypatch):
    # Redirecționează PROJECT_ROOT + config minimal
    import yaml
    (tmp_path / "config").mkdir()
    (tmp_path / "output" / "logs").mkdir(parents=True)
    cfg = {
        "n8n": {"enabled": True, "base_url": "https://n8n.example.com",
                "paths": {"ingest": "/i", "pending": "/p"},
                "secret_env": "N8N_WEBHOOK_SECRET", "timeout_sec": 5},
        "output": {"logs_dir": "output/logs", "log_level": "WARNING"},
    }
    (tmp_path / "config" / "settings.yaml").write_text(yaml.dump(cfg))
    monkeypatch.setattr("agents.base_agent.PROJECT_ROOT", tmp_path)
    from agents.n8n_sync_agent import N8nSyncAgent
    return N8nSyncAgent()


def test_push_success(agent):
    with patch.object(agent.client, "ingest", return_value={"ok": True, "upserted": 2}):
        res = agent.run(action="push", jobs=[{"url": "u1"}, {"url": "u2"}])
    assert res.status == AgentStatus.SUCCESS
    assert res.data["upserted"] == 2


def test_pending_success(agent):
    cmds = [{"action": "send", "URL": "u1"}]
    with patch.object(agent.client, "get_pending", return_value=cmds):
        res = agent.run(action="pending")
    assert res.status == AgentStatus.SUCCESS
    assert res.data["commands"] == cmds


def test_n8n_down_is_partial_not_failed(agent):
    with patch.object(agent.client, "ingest", side_effect=RuntimeError("boom")):
        res = agent.run(action="push", jobs=[{"url": "u1"}])
    assert res.status == AgentStatus.PARTIAL
    assert res.errors


def test_unknown_action_failed(agent):
    res = agent.run(action="nope")
    assert res.status == AgentStatus.FAILED
