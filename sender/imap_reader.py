"""
Citire IMAP a inbox-ului — DOAR ca să aflăm cine ne-a răspuns.

Siguranță critică: la orice eșec (conexiune, login, parsare) ridicăm `ImapError`
în loc să întoarcem un set gol. Un set gol înseamnă „nimeni n-a răspuns", iar
dacă asta vine dintr-un IMAP picat, follow-up-ul ar pleca la TOȚI — inclusiv la
cei care au răspuns. Agentul de follow-up abortează pe `ImapError`.

Read-only: nu ștergem, nu marcăm citit (folosim `BODY.PEEK`-echivalentul prin
`fetch` de headere fără `\\Seen`). Colectăm doar adresa From.
"""

from __future__ import annotations

import email
import imaplib
from datetime import datetime, timedelta
from email.utils import parseaddr


class ImapError(Exception):
    """IMAP n-a putut fi interogat — follow-up-ul trebuie să se oprească."""


def recent_reply_senders(imap_cfg: dict, password: str,
                         since_days: int = 30) -> set[str]:
    """Adresele From ale mesajelor primite în ultimele `since_days` zile.

    `imap_cfg`: {server, port?, login}. Întoarce un set de adrese lowercased.
    Ridică `ImapError` la orice problemă — niciodată un set gol „optimist".
    """
    server = imap_cfg.get("server")
    login = imap_cfg.get("login")
    port = int(imap_cfg.get("port", 993))
    if not server or not login:
        raise ImapError("Config IMAP incomplet (server/login lipsă)")
    if not password:
        raise ImapError("Parolă IMAP lipsă")

    senders: set[str] = set()
    try:
        conn = imaplib.IMAP4_SSL(server, port)
    except Exception as e:  # noqa: BLE001 — orice eșec de conexiune abortează
        raise ImapError(f"Conectare IMAP eșuată: {type(e).__name__}: {e}") from e

    try:
        conn.login(login, password)
        conn.select("INBOX", readonly=True)
        since = (datetime.now() - timedelta(days=since_days)).strftime("%d-%b-%Y")
        typ, data = conn.search(None, f'(SINCE {since})')
        if typ != "OK":
            raise ImapError(f"Căutare IMAP eșuată: {typ}")
        for num in (data[0].split() if data and data[0] else []):
            typ, msg_data = conn.fetch(num, "(BODY.PEEK[HEADER.FIELDS (FROM)])")
            if typ != "OK" or not msg_data or not msg_data[0]:
                continue
            raw = msg_data[0][1]
            msg = email.message_from_bytes(raw)
            addr = parseaddr(msg.get("From", ""))[1]
            if addr:
                senders.add(addr.strip().lower())
    except ImapError:
        raise
    except Exception as e:  # noqa: BLE001
        raise ImapError(f"Citire IMAP eșuată: {type(e).__name__}: {e}") from e
    finally:
        try:
            conn.logout()
        except Exception:  # noqa: BLE001 — logout best-effort
            pass
    return senders
