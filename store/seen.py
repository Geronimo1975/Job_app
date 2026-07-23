"""
Index persistent de joburi deja văzute.

Rezolvă două probleme ale variantei vechi: comparația O(n²) peste tot
fișierul de joburi și faptul că joburile deja aplicate reapăreau ca noi,
pentru că nimic nu se uita la Sheet.
"""

import json
import os
import re
import sys
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

# Deciziile posibile pentru un job văzut.
RESPINS_PREFILTRU = "respins_prefiltru"
ANALIZAT = "analizat"
TRIMIS_IN_SHEET = "trimis_in_sheet"
APLICAT = "aplicat"

# Parametri de URL care nu identifică jobul, doar de unde ai venit.
# NU se aruncă tot query string-ul: pe Indeed id-ul jobului stă în `jk`.
_TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "gh_src", "from", "src", "ref", "referrer", "source", "trk", "trkinfo",
}


def normalize_url(url: str) -> str:
    """Aduce URL-ul la o formă canonică, ca aceeași ofertă să dea aceeași cheie."""
    if not url or not str(url).strip():
        return ""
    parts = urlsplit(str(url).strip())
    query = urlencode(
        sorted((k, v) for k, v in parse_qsl(parts.query) if k.lower() not in _TRACKING_PARAMS)
    )
    path = parts.path.rstrip("/")
    return urlunsplit((parts.scheme, parts.netloc.lower(), path, query, ""))


def _slug(value: str) -> str:
    """Normalizează un text pentru comparație: minuscule, doar litere/cifre/cratime."""
    slug = re.sub(r"[^a-z0-9]+", "-", str(value or "").lower())
    return slug.strip("-")


def url_key(job: dict) -> str:
    """Cheia primară: URL-ul normalizat."""
    return normalize_url(job.get("url", ""))


def company_title_key(job: dict) -> str:
    """Cheia secundară: prinde același job postat pe două board-uri diferite."""
    company = _slug(job.get("company", ""))
    title = _slug(job.get("title", ""))
    if not company or not title:
        return ""
    return f"{company}|{title}"


class SeenIndex:
    """Set persistent de chei de joburi văzute, cu decizia luată pentru fiecare."""

    def __init__(self, path: Path, entries: dict | None = None):
        self.path = Path(path)
        self.entries: dict = entries or {}

    @classmethod
    def load(cls, path: Path) -> "SeenIndex":
        """Încarcă indexul; un fișier corupt sau lipsă dă un index gol.

        Un index pierdut înseamnă doar că vezi din nou niște joburi vechi —
        nu merită să oprească pipeline-ul. Dar nu tăcem: pierderea intrărilor
        `aplicat` duce la re-aplicare pe joburi la care s-a aplicat deja.
        """
        path = Path(path)
        if not path.exists():
            return cls(path)
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            # JSON valid dar de alt tip (listă, număr) ar trece de json.load
            # și ar crăpa abia la primul mark(), cu AttributeError.
            if not isinstance(data, dict):
                raise ValueError(f"conținut de tip {type(data).__name__}, aștept obiect")
            return cls(path, data)
        except (json.JSONDecodeError, OSError, ValueError) as e:
            print(
                f"  ⚠ Index dedupe ilizibil ({path}): {e}. "
                "Pornesc de la zero — joburile deja aplicate pot reapărea.",
                file=sys.stderr,
            )
            return cls(path)

    def __len__(self) -> int:
        return len(self.entries)

    def _keys(self, job: dict) -> list[str]:
        return [k for k in (url_key(job), company_title_key(job)) if k]

    def is_seen(self, job: dict) -> bool:
        return any(k in self.entries for k in self._keys(job))

    def mark(self, job: dict, decision: str) -> None:
        """Marchează jobul cu o decizie, păstrând `first_seen` la prima vedere."""
        now = datetime.now().isoformat(timespec="seconds")
        for key in self._keys(job):
            existing = self.entries.get(key)
            self.entries[key] = {
                "first_seen": existing["first_seen"] if existing else now,
                "last_seen": now,
                "decision": decision,
            }

    def seed_urls(self, urls: Iterable[str], decision: str = APLICAT) -> int:
        """Alimentează indexul cu URL-uri din Sheet. Întoarce câte au fost adăugate."""
        added = 0
        for url in urls:
            key = normalize_url(url or "")
            if not key or key in self.entries:
                continue
            self.mark({"url": key}, decision)
            added += 1
        return added

    def filter_new(self, jobs: list[dict]) -> list[dict]:
        """Întoarce doar joburile nevăzute, eliminând și dublurile din lot.

        NU marchează nimic: decizia despre ce s-a întâmplat cu jobul aparține
        apelantului, care abia după prefiltru știe ce să scrie.

        Eliminările pe cheia secundară (`firma|titlu`) se semnalează: acolo
        pot apărea fals-pozitive — două posturi diferite la aceeași firmă cu
        același titlu generic — spre deosebire de potrivirea pe URL, care e
        sigură.
        """
        fresh: list[dict] = []
        batch_keys: set[str] = set()
        for job in jobs:
            keys = self._keys(job)
            if not keys:
                continue
            if self.is_seen(job) or any(k in batch_keys for k in keys):
                if not self._matched_by_url(job, batch_keys):
                    print(
                        f"  ⓘ Eliminat pe firmă+titlu (nu pe URL): "
                        f"{job.get('company', '')} — {job.get('title', '')}",
                        file=sys.stderr,
                    )
                continue
            batch_keys.update(keys)
            fresh.append(job)
        return fresh

    def _matched_by_url(self, job: dict, batch_keys: set[str]) -> bool:
        """True dacă jobul a fost eliminat pe cheia de URL (potrivire sigură)."""
        key = url_key(job)
        return bool(key) and (key in self.entries or key in batch_keys)

    def save(self) -> None:
        """Scrie indexul pe disc, atomic.

        Scrie într-un fișier temporar și abia apoi îl mută peste cel real:
        `os.replace` e atomic pe același sistem de fișiere, deci un crash la
        mijlocul scrierii lasă indexul vechi intact, nu unul trunchiat.
        """
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.entries, f, indent=2, ensure_ascii=False)
            os.replace(tmp, self.path)
        except Exception:
            # Scrierea a eșuat: indexul vechi e neatins, dar temporarul nu
            # trebuie lăsat în urmă. Excepția urcă — apelantul decide.
            tmp.unlink(missing_ok=True)
            raise
