"""
Logica de follow-up — pură, fără I/O. Decide CINE primește un nudge și CE scrie.

Regula de aur (de ce feature-ul a fost amânat până acum): NU trimitem follow-up
cuiva care a răspuns deja. `replied_emails` vine din IMAP (sender/imap_reader);
o adresă acolo = fir deja viu, deci se sare. Fără acest gard, follow-up-ul
automat ar deranja exact candidaturile care merg bine.
"""

from __future__ import annotations

from datetime import datetime


def due_for_followup(entry: dict, replied_emails: set[str],
                     now: datetime, after_days: int) -> bool:
    """True dacă intrarea merită un follow-up ACUM.

    Condiții, toate necesare:
    - a fost trimisă (are `sent_at` parsabil),
    - au trecut ≥ `after_days` de la trimitere,
    - NU s-a trimis deja follow-up (`followup_sent_at` gol),
    - destinatarul NU a răspuns (nu e în `replied_emails`).
    """
    if entry.get("followup_sent_at"):
        return False
    email = str(entry.get("email") or "").strip().lower()
    if not email or email in replied_emails:
        return False
    sent_raw = entry.get("sent_at")
    if not sent_raw:
        return False
    try:
        sent_at = datetime.fromisoformat(sent_raw)
    except (TypeError, ValueError):
        return False
    return (now - sent_at).days >= int(after_days)


def followup_subject(position: str, language: str = "de") -> str:
    pos = position or ("die ausgeschriebene Stelle" if language == "de" else "the role")
    if language == "de":
        return f"Nachfrage zu meiner Bewerbung – {position}" if position else "Nachfrage zu meiner Bewerbung"
    return f"Following up on my application – {position}" if position else "Following up on my application"


def build_followup_body(name: str, company: str, position: str,
                        language: str = "de") -> str:
    """Nudge scurt și politicos. Determinist (fără AI) — un follow-up e scurt
    prin natura lui, iar un template stabil evită costul și riscul unui apel AI.
    """
    firma = company or ("Ihr Unternehmen" if language == "de" else "your company")
    stelle = position or ("die ausgeschriebene Position" if language == "de" else "the position")
    if language == "de":
        return (
            f"Sehr geehrte Damen und Herren,\n\n"
            f"vor einigen Tagen habe ich mich bei {firma} auf die Position "
            f"{stelle} beworben. Ich möchte höflich nachfragen, ob meine "
            f"Unterlagen angekommen sind und ob es bereits einen Zwischenstand gibt.\n\n"
            f"Über eine kurze Rückmeldung würde ich mich sehr freuen.\n\n"
            f"Mit freundlichen Grüßen\n{name}"
        )
    return (
        f"Hello,\n\n"
        f"A few days ago I applied to {firma} for the {stelle} position. "
        f"I wanted to kindly follow up and check whether my application arrived "
        f"and whether there is any update.\n\n"
        f"I would greatly appreciate a brief reply.\n\n"
        f"Best regards,\n{name}"
    )
