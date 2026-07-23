"""
Fundația comună a scraperelor: conceptul de piață și forma canonică a jobului.

Sursele primesc un `Market` și nu știu nimic despre țări. O piață nouă se
adaugă editând YAML-ul, fără cod nou.
"""

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

# Descrierile lungi umflă payload-ul spre AI fără să adauge informație.
_MAX_DESCRIPTION_CHARS = 5000


@dataclass(frozen=True)
class Market:
    """O piață de căutat: țară, limbă, keywords și surse proprii."""

    key: str
    country: str
    language: str
    locations: list[str] = field(default_factory=list)
    remote: bool = False
    keyword_groups: list[list[str]] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    # Surse al căror eșec NU trebuie să marcheze runul ca FAILED
    # (ex. scraping HTML pe site-uri fără API, care se strică la redesign).
    best_effort_sources: tuple[str, ...] = ()

    @staticmethod
    def from_config(config: dict) -> list["Market"]:
        """Citește `scraping.markets` din settings.yaml."""
        markets_cfg = (config.get("scraping", {}) or {}).get("markets", {}) or {}
        markets = []
        for key, cfg in markets_cfg.items():
            markets.append(
                Market(
                    key=key,
                    country=str(cfg.get("country", "")),
                    language=str(cfg.get("language", "en")),
                    locations=list(cfg.get("locations", []) or []),
                    remote=bool(cfg.get("remote", False)),
                    keyword_groups=[list(g) for g in (cfg.get("keyword_groups", []) or [])],
                    sources=list(cfg.get("sources", []) or []),
                    best_effort_sources=tuple(cfg.get("best_effort_sources", []) or []),
                )
            )
        return markets


def make_job(
    *,
    source: str,
    platform: str,
    title: str,
    company: str,
    location: str,
    url: str,
    description: str,
    market: Market,
    date_posted: str = "",
    salary_min=None,
    salary_max=None,
    email_hr: str = "",
) -> dict:
    """Construiește forma canonică a unui job, indiferent de sursă.

    `language` vine din piață și ajunge în col „Sprache" din Sheet, care
    comandă limba scrisorii — de asta un job din RO nu primește Anschreiben
    în germană.
    """
    now = datetime.now()
    return {
        "id": f"{source}_{abs(hash(str(url).strip()))}",
        "source": source,
        "platform": platform,
        "market": market.key,
        "language": market.language,
        "title": str(title or "").strip(),
        "company": str(company or "").strip(),
        "location": str(location or "").strip(),
        "url": str(url or "").strip(),
        "description": str(description or "")[:_MAX_DESCRIPTION_CHARS],
        "email_hr": str(email_hr or "").strip(),
        "date_posted": str(date_posted or ""),
        "salary_min": salary_min,
        "salary_max": salary_max,
        "scraped_at": now.isoformat(),
        "gefunden_am": now.strftime("%Y-%m-%d"),
        "applied": False,
        # `new` nu face parte din mașina de stări a col M; `n8n_mapping`
        # îl filtrează la push, ca să nu suprascrie rânduri deja `done`.
        "status": "new",
    }


def save_jobs(jobs: list[dict], path: Path) -> Path:
    """Scrie rezultatele rulării curente. NU face merge cu ce era înainte —
    memoria între rulări e treaba lui `store/seen.py`."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(jobs, f, indent=2, ensure_ascii=False, default=str)
    return path
