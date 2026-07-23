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
    "gefunden_am": "Datum gefunden",
    "status": "Status",
    "cover_letter": "Anschreiben PDF",
    "gesendet_am": "Gesendet am",
    "email_status": "Email Status",
    "notizen": "Notizen",
}

# header german -> internal_key (invers)
_REVERSE = {v: k for k, v in FIELD_MAP.items()}

# Valorile acceptate în col M „Status" — mașina de stări a Apps Script.
# Orice altceva (ex. `new` de la scrapere) NU se scrie în Sheet.
_SHEET_STATUSES = {"plan", "send", "running", "done", "error"}


def job_to_sheet_row(job: dict) -> dict:
    """Convertește un job intern într-un rând pentru Sheet (chei germane).

    Emite DOAR cheile cu valoare efectivă. Nodul Google Sheets din `JobApp
    Ingest` rulează `appendOrUpdate` + `autoMapInputData`: o cheie absentă e
    ignorată, dar o cheie cu "" GOLEȘTE celula din rândul găsit după
    „Apply URL". Cum comanda venită din `JobApp Pending` conține doar un
    subset de câmpuri, varianta veche (care punea "" pentru tot ce lipsea)
    ștergea Standort/Quelle/Kontakt Person/Datum gefunden/Notizen/Match Score
    la fiecare poll.
    """
    row: dict = {}
    for internal_key, german in FIELD_MAP.items():
        value = job.get(internal_key)
        if value is None or value == "":
            continue
        # Col M „Status" e mașina de stări a Apps Script
        # (plan→send→running→done/error). Scraperele pun `status="new"`, care
        # nu face parte din ea; un push l-ar scrie peste rânduri deja `done`.
        if internal_key == "status" and str(value).lower() not in _SHEET_STATUSES:
            continue
        row[german] = value

    # Câmpuri derivate din analiză (doar coloane care există în Sheet)
    analysis = job.get("analysis", {}) or {}
    score = analysis.get("match_score", job.get("match_score"))
    if score is not None and score != "":
        row["Match Score"] = score

    # Col P „Sende Befehl" e trigger-ul scris de om; o atingem doar când
    # apelantul cere explicit resetarea ei (altfel ar rămâne SENDEN pe veci
    # și poll-ul ar reprocesa același rând la fiecare tick).
    if "sende_befehl" in job:
        row["Sende Befehl"] = job["sende_befehl"]

    return row


def sheet_row_to_job(row: dict) -> dict:
    """Convertește o comandă n8n (chei germane) într-un job intern."""
    job: dict = {}
    for german, value in row.items():
        if german in _REVERSE:
            job[_REVERSE[german]] = value
    return job
