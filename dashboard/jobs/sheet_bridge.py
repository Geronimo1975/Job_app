"""Puntea dashboard → pipeline: upsert în Sheet + comanda SENDEN prin n8n.

Singurul modul al dashboard-ului care importă din sender/ (read-only).
Nu emite NICIODATĂ col M „Status" — aparține mașinii de stări Apps Script.
"""
import os
from pathlib import Path

import yaml

from sender.n8n_client import N8nClient
from sender.n8n_mapping import job_to_sheet_row

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SETTINGS = _REPO_ROOT / "config" / "settings.yaml"
_POLL_PLIST = _REPO_ROOT / "scripts" / "com.jobapp.poll.plist"


def load_pipeline_config() -> dict:
    """Config-ul pipeline-ului (bloc n8n etc.), fără import din agents/."""
    with open(_SETTINGS, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def job_to_internal(job, sende_befehl: str | None = None) -> dict:
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
        "gefunden_am": job.created_at.strftime("%Y-%m-%d") if job.created_at else "",
        "notizen": job.notizen,
        "match_score": job.match_score,
    }
    internal = {k: v for k, v in data.items() if v is not None and v != ""}
    if sende_befehl:
        internal["sende_befehl"] = sende_befehl
    return internal


def preview_sheet_row(job) -> dict:
    """Rândul EXACT (chei germane) care ar pleca la „Trimite" — pt. confirmare."""
    return job_to_sheet_row(job_to_internal(job, sende_befehl="SENDEN"))


def _client_or_error(cfg: dict) -> tuple[N8nClient | None, str]:
    n8n_cfg = cfg.get("n8n", {}) or {}
    if not n8n_cfg.get("enabled", False):
        return None, "n8n e dezactivat în config/settings.yaml (n8n.enabled: false)."
    secret_env = n8n_cfg.get("secret_env", "N8N_WEBHOOK_SECRET")
    if not os.environ.get(secret_env):
        return None, f"{secret_env} lipsește din mediu — rulează: set -a; source .env; set +a"
    return N8nClient(cfg), ""


def push_to_sheet(job, senden: bool) -> dict:
    """Upsert în Sheet; cu senden=True scrie și col P „Sende Befehl"=SENDEN."""
    try:
        cfg = load_pipeline_config()
    except Exception as e:
        return {"ok": False, "error": f"config necititbil: {e}"}
    client, err = _client_or_error(cfg)
    if client is None:
        return {"ok": False, "error": err}
    internal = job_to_internal(job, sende_befehl="SENDEN" if senden else None)
    try:
        client.ingest([internal])
    except Exception as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True}


def poll_mode() -> str:
    """Modul poll-ului launchd: LIVE dacă plist-ul conține --live."""
    try:
        content = _POLL_PLIST.read_text(encoding="utf-8")
    except OSError:
        return "necunoscut (dry-run implicit)"
    return "LIVE" if "--live" in content else "dry-run"


def refresh_states(jobs_qs) -> int:
    """Deducție din pending: rândurile senden_pushed absente din pending → processed.

    Ridică RuntimeError dacă n8n e indisponibil/dezactivat — un pending gol
    fals ar marca totul „processed".
    """
    cfg = load_pipeline_config()
    client, err = _client_or_error(cfg)
    if client is None:
        raise RuntimeError(err)
    pending_urls = {
        str(c.get("Apply URL", "")).strip() for c in client.get_pending()
    }
    changed = 0
    for job in jobs_qs.filter(send_state="senden_pushed"):
        if job.url.strip() not in pending_urls:
            job.send_state = "processed"
            job.save(update_fields=["send_state"])
            changed += 1
    return changed
