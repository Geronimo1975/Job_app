from unittest.mock import patch, MagicMock

import httpx
import pytest

from sender.n8n_client import N8nClient

BASE_CFG = {
    "n8n": {
        "enabled": True,
        "base_url": "https://n8n.example.com",
        "paths": {"ingest": "/webhook/jobapp-ingest", "pending": "/webhook/jobapp-pending"},
        "secret_env": "N8N_WEBHOOK_SECRET",
        "timeout_sec": 5,
    }
}


def _resp(status=200, payload=None, content=b"{}"):
    m = MagicMock(spec=httpx.Response)
    m.status_code = status
    m.content = content
    m.json.return_value = payload or {}
    m.raise_for_status.return_value = None
    return m


def test_ingest_posts_mapped_rows_with_secret(monkeypatch):
    monkeypatch.setenv("N8N_WEBHOOK_SECRET", "s3cr3t")
    client = N8nClient(BASE_CFG)
    with patch("sender.n8n_client.httpx.post", return_value=_resp(200, {"ok": True, "upserted": 1})) as p:
        out = client.ingest([{"url": "u1", "company": "X"}])
    assert out == {"ok": True, "upserted": 1}
    args, kwargs = p.call_args
    assert kwargs["headers"]["X-JobApp-Secret"] == "s3cr3t"
    assert kwargs["json"]["event"] == "jobs_upsert"
    assert kwargs["json"]["jobs"][0]["Apply URL"] == "u1"
    assert kwargs["json"]["jobs"][0]["Firma"] == "X"


def test_get_pending_returns_commands(monkeypatch):
    monkeypatch.setenv("N8N_WEBHOOK_SECRET", "s3cr3t")
    client = N8nClient(BASE_CFG)
    payload = {"ok": True, "commands": [{"action": "send", "URL": "u1"}]}
    with patch("sender.n8n_client.httpx.post", return_value=_resp(200, payload)):
        cmds = client.get_pending()
    assert cmds == [{"action": "send", "URL": "u1"}]


def test_disabled_client_is_noop(monkeypatch):
    cfg = {"n8n": dict(BASE_CFG["n8n"], enabled=False)}
    client = N8nClient(cfg)
    with patch("sender.n8n_client.httpx.post") as p:
        assert client.ingest([{"url": "u"}]) == {"ok": False, "skipped": True, "upserted": 0}
        assert client.get_pending() == []
    p.assert_not_called()


def test_ingest_empty_jobs_is_noop_without_request(monkeypatch):
    monkeypatch.setenv("N8N_WEBHOOK_SECRET", "s")
    client = N8nClient(BASE_CFG)
    with patch("sender.n8n_client.httpx.post") as p:
        assert client.ingest([]) == {"ok": True, "skipped": True, "upserted": 0}
    p.assert_not_called()


def test_empty_response_body_returns_empty_dict(monkeypatch):
    monkeypatch.setenv("N8N_WEBHOOK_SECRET", "s")
    client = N8nClient(BASE_CFG)
    # 200 cu body gol (n8n nu produce rânduri) → nu crăpa la parse
    with patch("sender.n8n_client.httpx.post", return_value=_resp(200, content=b"")):
        assert client.ingest([{"url": "u"}]) == {}
        assert client.get_pending() == []


def test_retries_once_on_5xx_then_raises(monkeypatch):
    monkeypatch.setenv("N8N_WEBHOOK_SECRET", "s")
    client = N8nClient(BASE_CFG)
    with patch("sender.n8n_client.httpx.post", return_value=_resp(503, {})) as p:
        with pytest.raises(RuntimeError):
            client.ingest([{"url": "u"}])
    assert p.call_count == 2
