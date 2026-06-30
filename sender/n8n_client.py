"""
N8nClient — transport HTTP către webhook-urile n8n (ingest + pending).

Nu atinge Google direct: trimite/cere date de la n8n, care deține Sheet-ul.
Respectă flag-ul n8n.enabled (no-op când e dezactivat).
"""

import os

import httpx

from sender.n8n_mapping import job_to_sheet_row


class N8nClient:
    """Client subțire peste webhook-urile n8n."""

    def __init__(self, config: dict):
        cfg = config.get("n8n", {}) or {}
        self.enabled: bool = bool(cfg.get("enabled", False))
        self.base_url: str = str(cfg.get("base_url", "")).rstrip("/")
        self.paths: dict = cfg.get("paths", {}) or {}
        self.timeout: int = int(cfg.get("timeout_sec", 20))
        secret_env = cfg.get("secret_env", "N8N_WEBHOOK_SECRET")
        self.secret: str = os.environ.get(secret_env, "")

    def _post(self, path_key: str, payload: dict) -> dict:
        url = f"{self.base_url}{self.paths.get(path_key, '')}"
        headers = {"X-JobApp-Secret": self.secret, "Content-Type": "application/json"}
        last_err = None
        for _ in range(2):  # 1 încercare + 1 retry
            try:
                resp = httpx.post(url, json=payload, headers=headers, timeout=self.timeout)
                if resp.status_code >= 500:
                    last_err = RuntimeError(f"n8n 5xx: {resp.status_code}")
                    continue
                resp.raise_for_status()
                # n8n întoarce body gol când workflow-ul nu produce rânduri (ex: 0 joburi);
                # tratăm asta ca succes fără payload, nu ca eroare de parsare.
                if not resp.content:
                    return {}
                return resp.json()
            except (httpx.TransportError, httpx.HTTPStatusError) as e:
                last_err = e
                continue
        raise RuntimeError(f"n8n request eșuat ({path_key}): {last_err}")

    def ingest(self, jobs: list[dict]) -> dict:
        if not self.enabled:
            return {"ok": False, "skipped": True, "upserted": 0}
        if not jobs:
            # Nimic de trimis — evităm un request inutil (webhook-ul n8n nu
            # returnează body când nu există rânduri de procesat).
            return {"ok": True, "skipped": True, "upserted": 0}
        rows = [job_to_sheet_row(j) for j in jobs]
        return self._post("ingest", {"event": "jobs_upsert", "jobs": rows})

    def get_pending(self) -> list[dict]:
        if not self.enabled:
            return []
        data = self._post("pending", {"action": "get_pending"})
        return data.get("commands", [])
