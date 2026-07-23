#!/usr/bin/env python3
"""
Deploy workflow-urile JobApp în n8n via public API.

Citește N8N_API_KEY din .env. Idempotent: dacă un workflow cu același nume există,
îl actualizează în loc să-l dubleze.

Rulare:
    python3 scripts/deploy_n8n_workflows.py
"""
import json
from pathlib import Path

import httpx

ROOT = Path(__file__).parent.parent.resolve()
BASE = "https://app.n8n-studio.com/api/v1"
WORKFLOWS = ["jobapp_ingest.json", "jobapp_pending.json", "jobapp_upload.json", "jobapp_trigger.json", "jobapp_urls.json"]


def _api_key() -> str:
    for line in (ROOT / ".env").read_text().splitlines():
        if line.startswith("N8N_API_KEY="):
            return line.split("=", 1)[1].strip()
    raise SystemExit("N8N_API_KEY lipsă în .env")


def main():
    key = _api_key()
    h = {"X-N8N-API-KEY": key, "accept": "application/json", "content-type": "application/json"}

    existing = httpx.get(f"{BASE}/workflows?limit=200", headers=h, timeout=30).json().get("data", [])
    by_name = {w["name"]: w["id"] for w in existing}

    for fname in WORKFLOWS:
        wf = json.loads((ROOT / "n8n_workflows" / fname).read_text())
        name = wf["name"]
        if name in by_name:
            wid = by_name[name]
            r = httpx.put(f"{BASE}/workflows/{wid}", headers=h, json=wf, timeout=30)
            if r.status_code >= 300:
                raise SystemExit(f"  ❌ update {name} eșuat: HTTP {r.status_code} — {r.text[:200]}")
            print(f"  ♻ update {name}: HTTP {r.status_code}")
        else:
            r = httpx.post(f"{BASE}/workflows", headers=h, json=wf, timeout=30)
            if r.status_code >= 300:
                raise SystemExit(f"  ❌ create {name} eșuat: HTTP {r.status_code} — {r.text[:200]}")
            wid = r.json().get("id")
            if not wid:
                raise SystemExit(f"  ❌ create {name}: răspuns fără id — {r.text[:200]}")
            print(f"  ➕ create {name}: HTTP {r.status_code} (id={wid})")
        # activează
        ra = httpx.post(f"{BASE}/workflows/{wid}/activate", headers=h, timeout=30)
        print(f"     activate: HTTP {ra.status_code}")


if __name__ == "__main__":
    main()
