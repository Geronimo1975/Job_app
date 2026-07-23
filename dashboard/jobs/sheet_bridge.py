"""Puntea dashboard → Sheet prin n8n: upsert de date + marcarea GESENDET.

Importă din sender/ read-only. Nu emite NICIODATĂ col M „Status" (mașina de
stări Apps Script) și nici col P „Sende Befehl" (fluxul SENDEN a fost înlocuit
de trimiterea directă din dashboard — vezi generation_bridge).
"""
import os
from pathlib import Path

import yaml
from django.utils import timezone

from sender.n8n_client import N8nClient

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SETTINGS = _REPO_ROOT / "config" / "settings.yaml"


def load_pipeline_config() -> dict:
    """Config-ul pipeline-ului (bloc n8n etc.), fără import din agents/."""
    with open(_SETTINGS, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def job_to_internal(job) -> dict:
    """Modelul Django → dict intern (chei engleze din FIELD_MAP).

    Emite doar câmpurile cu valoare (upsert-ul n8n golește celula la "").
    NU emite: status (col M), description/raw_json (fără coloană mapată).
    """
    data = {
        "company": job.company,
        "title": job.title,
        "location": job.location,
        "url": job.url,
        "source": job.source,
        "email_hr": job.email_hr,
        "contact_person": job.contact_person,
        "language": job.language,
        # Data locală (Europe/Berlin), nu UTC — altfel „Datum gefunden" poate
        # ieși cu o zi în urmă pentru joburile adăugate după miezul nopții.
        "gefunden_am": timezone.localtime(job.created_at).strftime("%Y-%m-%d")
        if job.created_at else "",
        "notizen": job.notizen,
        "match_score": job.match_score,
    }
    return {k: v for k, v in data.items() if v is not None and v != ""}


def _client_or_error(cfg: dict) -> tuple[N8nClient | None, str]:
    n8n_cfg = cfg.get("n8n", {}) or {}
    if not n8n_cfg.get("enabled", False):
        return None, "n8n e dezactivat în config/settings.yaml (n8n.enabled: false)."
    secret_env = n8n_cfg.get("secret_env", "N8N_WEBHOOK_SECRET")
    if not os.environ.get(secret_env):
        return None, f"{secret_env} lipsește din mediu — rulează: set -a; source .env; set +a"
    return N8nClient(cfg), ""


def push_to_sheet(job) -> dict:
    """Upsert-ul câmpurilor în Sheet (match pe Apply URL). Nu emite col P/M."""
    # Fără Apply URL nu există cheie de match: clientul ar arunca rândul
    # silențios și am raporta fals succes.
    if not str(job.url or "").strip():
        return {"ok": False, "error": "jobul nu are Apply URL — fără cheie de match, upsert-ul e imposibil."}
    try:
        cfg = load_pipeline_config()
    except Exception as e:
        return {"ok": False, "error": f"config necititbil: {e}"}
    client, err = _client_or_error(cfg)
    if client is None:
        return {"ok": False, "error": err}
    try:
        client.ingest([job_to_internal(job)])
    except Exception as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True}


def mark_sent_in_sheet(job) -> dict:
    """După un LIVE reușit: col R „Email Status"=GESENDET + „Gesendet am".

    GESENDET e recunoscut de ambele sisteme existente → nimeni nu retrimite.
    """
    if not str(job.url or "").strip():
        return {"ok": False, "error": "jobul nu are Apply URL."}
    try:
        cfg = load_pipeline_config()
    except Exception as e:
        return {"ok": False, "error": f"config necititbil: {e}"}
    client, err = _client_or_error(cfg)
    if client is None:
        return {"ok": False, "error": err}
    moment = timezone.localtime(job.sent_at or timezone.now())
    payload = {
        "url": job.url,
        "email_status": "GESENDET",
        "gesendet_am": moment.strftime("%Y-%m-%d %H:%M"),
    }
    try:
        client.ingest([payload])
    except Exception as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True}
