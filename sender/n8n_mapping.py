"""
Mapping între dict-ul intern de job (chei engleze) și headerele germane ale
Google Sheet-ului „Bewerbungen" (tab din „Job Application Tracker").
Valorile = numele EXACT al coloanelor din Sheet. Funcții pure, fără I/O.
"""

# internal_key -> german_header (1:1, valori simple)
FIELD_MAP = {
    "company": "Firma",
    "title": "Position",
    "location": "Standort",
    "url": "Apply URL",
    "source": "Quelle",
    "email_hr": "Kontakt Email",
    "contact_person": "Kontakt Person",
    "language": "Sprache",
    "status": "Status",
    "cover_letter": "Anschreiben PDF",
    "gesendet_am": "Gesendet am",
    "email_status": "Email Status",
    "notizen": "Notizen",
}

# header german -> internal_key (invers)
_REVERSE = {v: k for k, v in FIELD_MAP.items()}


def job_to_sheet_row(job: dict) -> dict:
    """Convertește un job intern într-un rând pentru Sheet (chei germane)."""
    row: dict = {}
    for internal_key, german in FIELD_MAP.items():
        row[german] = job.get(internal_key, "")

    # Câmpuri derivate din analiză (doar coloane care există în Sheet)
    analysis = job.get("analysis", {}) or {}
    score = analysis.get("match_score", job.get("match_score", ""))
    row["Match Score"] = score if score is not None else ""

    row["Datum gefunden"] = job.get("gefunden_am", "")
    return row


def sheet_row_to_job(row: dict) -> dict:
    """Convertește o comandă n8n (chei germane) într-un job intern."""
    job: dict = {}
    for german, value in row.items():
        if german in _REVERSE:
            job[_REVERSE[german]] = value
    return job
