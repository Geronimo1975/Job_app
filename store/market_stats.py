"""
Statistici per piață peste lotul reținut (`data/jobs_filtered.json`).

Strategia „Find statistics and get improved" de la LoopCV, aplicată onest: NU
inventează rate de interviu, ci agregă datele pe care pipeline-ul le are deja
după poartă — câte joburi per piață, scorul mediu, acoperirea de email și câte
s-au aplicat. Scopul e să vezi CE PIAȚĂ merită efortul: dacă `ro_remote` aduce
100 de joburi dar 1% au email, iar `de` aduce 20 cu 17% email, numerele o spun.

Funcții pure, fără I/O în `aggregate()` — ușor de testat și de reapelat din
dashboard. `load_and_render()` e singurul loc care atinge discul.
"""

from __future__ import annotations

import json
from pathlib import Path


def _market_of(job: dict) -> str:
    """Piața jobului; joburile fără piață (career pages) merg la „(fără piață)"."""
    return str(job.get("market") or "").strip() or "(fără piață)"


def aggregate(jobs: list[dict]) -> dict[str, dict]:
    """Agregă lista de joburi pe piață.

    Întoarce {piață: {count, avg_prescore, with_email, pct_email, applied}}.
    `with_email` numără doar adresele ne-goale — `email_hr` poate fi "" sau
    lipsă, iar ambele înseamnă „fără destinatar", nu „zero".
    """
    buckets: dict[str, dict] = {}
    for job in jobs:
        market = _market_of(job)
        b = buckets.setdefault(
            market,
            {"count": 0, "_score_sum": 0.0, "with_email": 0, "applied": 0},
        )
        b["count"] += 1
        try:
            b["_score_sum"] += float(job.get("prescore") or 0)
        except (TypeError, ValueError):
            pass
        if str(job.get("email_hr") or "").strip():
            b["with_email"] += 1
        if job.get("applied"):
            b["applied"] += 1

    for b in buckets.values():
        n = b["count"] or 1
        b["avg_prescore"] = round(b.pop("_score_sum") / n, 1)
        b["pct_email"] = round(100 * b["with_email"] / n, 1)
    return buckets


def render(stats: dict[str, dict]) -> str:
    """Tabel text, piețe sortate după acoperirea de email (desc).

    Email-ul decide dacă o piață poate alimenta trimiterea automată, deci e
    cheia de sortare cea mai utilă — nu numărul brut de joburi.
    """
    if not stats:
        return "Statistici per piață: niciun job în lot."

    rows = sorted(
        stats.items(),
        key=lambda kv: (kv[1]["pct_email"], kv[1]["count"]),
        reverse=True,
    )
    head = f"{'Piață':<16}{'Joburi':>7}{'Scor med':>10}{'Cu email':>10}{'% email':>9}{'Aplicate':>10}"
    lines = ["Statistici per piață (lot reținut):", head, "─" * len(head)]
    total = {"count": 0, "with_email": 0, "applied": 0}
    for market, b in rows:
        lines.append(
            f"{market:<16}{b['count']:>7}{b['avg_prescore']:>10}"
            f"{b['with_email']:>10}{b['pct_email']:>8}%{b['applied']:>10}"
        )
        total["count"] += b["count"]
        total["with_email"] += b["with_email"]
        total["applied"] += b["applied"]
    pct = round(100 * total["with_email"] / (total["count"] or 1), 1)
    lines.append("─" * len(head))
    lines.append(
        f"{'TOTAL':<16}{total['count']:>7}{'':>10}"
        f"{total['with_email']:>10}{pct:>8}%{total['applied']:>10}"
    )
    return "\n".join(lines)


def load_and_render(path: str | Path = "data/jobs_filtered.json") -> str:
    """Citește lotul de pe disc și întoarce tabelul; mesaj clar dacă lipsește."""
    p = Path(path)
    if not p.exists():
        return f"Statistici per piață: {p} nu există (rulează întâi --step scrape)."
    try:
        jobs = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        return f"Statistici per piață: nu pot citi {p} ({type(e).__name__})."
    if not isinstance(jobs, list):
        return f"Statistici per piață: {p} nu conține o listă de joburi."
    return render(aggregate(jobs))
