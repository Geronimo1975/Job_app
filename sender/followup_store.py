"""
Store local pentru follow-up — ce s-a trimis, când, și dacă s-a dat deja nudge.

Sursa de adevăr pentru follow-up stă LOCAL (ca rate-limit-ul din smtp_sender),
NU în Sheet: follow-up-ul nu trebuie să atingă maparea n8n/Sheet. Cheia e `url`
(aceeași cheie de corelare folosită de run_poll), ca o trimitere și follow-up-ul
ei să fie același rând chiar dacă firma are mai multe anunțuri.

Scriere atomică (temp + replace) ca un proces întrerupt să nu lase JSON corupt;
trimiterea (EmailAgent) și pasul followup pot rula ca procese separate.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path


class FollowupStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._data: dict[str, dict] = self._load()

    def _load(self) -> dict[str, dict]:
        if not self.path.exists():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (json.JSONDecodeError, OSError):
            return {}

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(self._data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)  # atomic

    def record_sent(self, url: str, email: str, company: str = "",
                    position: str = "", language: str = "de",
                    when: datetime | None = None) -> None:
        """Înregistrează o candidatură trimisă. Fără url SAU email, nu facem
        nimic — follow-up-ul are nevoie de cheia de corelare ȘI de destinatar."""
        if not url or not email:
            return
        entry = self._data.setdefault(url, {})
        # Nu suprascrie un follow-up deja marcat dacă jobul se re-trimite.
        entry.update({
            "email": email.strip().lower(),
            "company": company,
            "position": position,
            "language": language or "de",
            "sent_at": (when or datetime.now()).isoformat(),
        })
        entry.setdefault("followup_sent_at", None)
        self._save()

    def mark_followed_up(self, url: str, when: datetime | None = None) -> None:
        if url in self._data:
            self._data[url]["followup_sent_at"] = (when or datetime.now()).isoformat()
            self._save()

    def entries(self) -> dict[str, dict]:
        return dict(self._data)
