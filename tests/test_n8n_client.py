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


def test_upload_doc_posts_base64_pdf(monkeypatch, tmp_path):
    import base64
    monkeypatch.setenv("N8N_WEBHOOK_SECRET", "s")
    client = N8nClient(BASE_CFG)
    f = tmp_path / "Anschreiben_X.pdf"
    f.write_bytes(b"%PDF-1.4 test-bytes")
    with patch("sender.n8n_client.httpx.post",
               return_value=_resp(200, {"ok": True, "link": "L", "fileId": "F"})) as p:
        out = client.upload_doc(f, row=5, apply_url="u1")
    assert out == {"ok": True, "link": "L", "fileId": "F"}
    body = p.call_args.kwargs["json"]
    assert body["action"] == "upload_doc"
    assert body["row"] == 5 and body["filename"] == "Anschreiben_X.pdf"
    assert base64.b64decode(body["content_base64"]) == b"%PDF-1.4 test-bytes"


def test_upload_doc_disabled_is_noop(monkeypatch, tmp_path):
    client = N8nClient({"n8n": dict(BASE_CFG["n8n"], enabled=False)})
    f = tmp_path / "x.pdf"
    f.write_bytes(b"x")
    with patch("sender.n8n_client.httpx.post") as p:
        assert client.upload_doc(f, row=1)["skipped"] is True
    p.assert_not_called()


class TestGetKnownUrls:
    """Alimentarea indexului de dedupe din Sheet."""

    def _client(self, enabled=True):
        from sender.n8n_client import N8nClient
        return N8nClient({
            "n8n": {
                "enabled": enabled,
                "base_url": "https://n8n.test",
                "paths": {"urls": "/webhook/jobapp-urls"},
                "timeout_sec": 5,
            }
        })

    def test_disabled_client_returns_empty(self):
        assert self._client(enabled=False).get_known_urls() == []

    def test_returns_urls_of_processed_rows(self):
        from unittest.mock import patch
        rows = [
            {"Apply URL": "https://a.com/1", "Status": "done", "Email Status": ""},
            {"Apply URL": "https://a.com/2", "Status": "", "Email Status": "GESENDET"},
            {"Apply URL": "https://a.com/3", "Status": "", "Email Status": "sent"},
        ]
        with patch.object(type(self._client()), "_post", return_value={"rows": rows}):
            urls = self._client().get_known_urls()
        assert urls == ["https://a.com/1", "https://a.com/2", "https://a.com/3"]

    def test_skips_unprocessed_rows(self):
        """Un rând doar găsit, neaplicat, nu blochează re-descoperirea."""
        from unittest.mock import patch
        rows = [{"Apply URL": "https://a.com/9", "Status": "", "Email Status": ""}]
        with patch.object(type(self._client()), "_post", return_value={"rows": rows}):
            assert self._client().get_known_urls() == []

    def test_skips_rows_without_url(self):
        from unittest.mock import patch
        rows = [{"Apply URL": "", "Status": "done", "Email Status": ""}]
        with patch.object(type(self._client()), "_post", return_value={"rows": rows}):
            assert self._client().get_known_urls() == []

    def test_empty_response_is_safe(self):
        from unittest.mock import patch
        with patch.object(type(self._client()), "_post", return_value={}):
            assert self._client().get_known_urls() == []
