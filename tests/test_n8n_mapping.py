from sender.n8n_mapping import FIELD_MAP, job_to_sheet_row, sheet_row_to_job


def test_job_to_sheet_row_maps_core_and_analysis():
    job = {
        "url": "https://x/job/1", "company": "Studio X", "title": "Python Dev",
        "location": "Berlin", "source": "jobspy", "email_hr": "hr@x.de",
        "contact_person": "Frau Y", "language": "de", "status": "plan",
        "cover_letter": "Sehr geehrte...",
        "analysis": {"match_score": 72, "matching_skills": ["Python", "Django"]},
    }
    row = job_to_sheet_row(job)
    assert row["Apply URL"] == "https://x/job/1"
    assert row["Firma"] == "Studio X"
    assert row["Position"] == "Python Dev"
    assert row["Kontakt Email"] == "hr@x.de"
    assert row["Sprache"] == "de"
    assert row["Status"] == "plan"
    assert row["Match Score"] == 72
    assert row["Anschreiben PDF"] == "Sehr geehrte..."
    # coloane inexistente în Sheet nu trebuie produse (ar crea coloane fantomă)
    assert "Keywords Match" not in row
    assert "Email Inhalt" not in row


def test_sheet_row_to_job_inverse_core():
    cmd = {
        "action": "send", "Apply URL": "https://x/job/1", "Firma": "Studio X",
        "Position": "Python Dev", "Kontakt Email": "hr@x.de", "Sprache": "de",
        "Anschreiben PDF": "Sehr geehrte...",
    }
    job = sheet_row_to_job(cmd)
    assert job["url"] == "https://x/job/1"
    assert job["company"] == "Studio X"
    assert job["title"] == "Python Dev"
    assert job["email_hr"] == "hr@x.de"
    assert job["language"] == "de"
    assert job["cover_letter"] == "Sehr geehrte..."


def test_job_to_sheet_row_omite_campurile_lipsa():
    """Cheile fără valoare NU trebuie emise: `autoMapInputData` golește celula."""
    row = job_to_sheet_row({"url": "u"})
    assert row["Apply URL"] == "u"
    assert "Firma" not in row
    assert "Match Score" not in row


def test_push_dupa_pending_nu_sterge_coloanele_necuprinse():
    """Regresie: comanda din `JobApp Pending` are doar un subset de coloane.

    Rândul trimis înapoi la upsert nu are voie să conțină chei goale pentru
    coloanele pe care Sheet-ul le are populate din altă sursă.
    """
    comanda = {
        "action": "send", "Apply URL": "https://x/job/1", "Firma": "Studio X",
        "Position": "Python Dev", "Kontakt Email": "hr@x.de", "Sprache": "de",
        "Anschreiben PDF": "", "Email Status": "",
    }
    row = job_to_sheet_row(sheet_row_to_job(comanda))
    for coloana in ("Notizen", "Standort", "Quelle", "Kontakt Person",
                    "Datum gefunden", "Match Score", "Gesendet am"):
        assert coloana not in row, f"{coloana} ar goli celula existentă"


def test_status_din_scraper_nu_ajunge_in_sheet():
    """`new` nu face parte din mașina de stări a Apps Script."""
    assert "Status" not in job_to_sheet_row({"url": "u", "status": "new"})
    assert job_to_sheet_row({"url": "u", "status": "plan"})["Status"] == "plan"


def test_sende_befehl_scris_doar_la_cerere_explicita():
    """Col P e trigger-ul scris de om — se atinge doar când e cerut explicit."""
    assert "Sende Befehl" not in job_to_sheet_row({"url": "u"})
    assert job_to_sheet_row({"url": "u", "sende_befehl": ""})["Sende Befehl"] == ""


def test_field_map_acopera_doar_headere_reale():
    """Fiecare țintă din FIELD_MAP trebuie să existe în Sheet-ul real."""
    headere_reale = {
        "Nr.", "Firma", "Position", "Standort", "Typ", "Gehalt",
        "Kontakt Email", "Kontakt Person", "Sprache", "Quelle", "Apply URL",
        "Datum gefunden", "Status", "Match Score", "Anschreiben PDF",
        "Sende Befehl", "Gesendet am", "Email Status", "Antwort erhalten am",
        "Interview Datum", "Kommunikation Log", "Final Status", "Notizen",
        "Antwort Beispiel", "Antwort Generiert", "Interview Termin",
        "Bewerbungsart", "Danke Absage",
    }
    necunoscute = set(FIELD_MAP.values()) - headere_reale
    assert not necunoscute, f"headere inexistente în Sheet: {necunoscute}"


def test_job_to_sheet_row_maps_gefunden_am():
    row = job_to_sheet_row({"url": "u", "gefunden_am": "2026-07-09"})
    assert row["Datum gefunden"] == "2026-07-09"


def test_sheet_row_to_job_roundtrips_gefunden_am():
    job = sheet_row_to_job({"Apply URL": "u", "Datum gefunden": "2026-07-09"})
    assert job["gefunden_am"] == "2026-07-09"


class TestBewerbungsart:
    """Col AA „Bewerbungsart" spune CUM se aplică. Fără ea, un rând fără
    adresă arăta identic cu unul trimisibil, iar utilizatorul rămânea blocat
    („nu am email, deci nu mă pot conecta"). Joburile remote/nomad n-au
    aproape niciodată email — se aplică prin portal, via „Apply URL"."""

    def test_job_with_email_is_marked_email(self):
        row = job_to_sheet_row({"url": "https://x.de/1", "email_hr": "jobs@x.de"})
        assert row["Bewerbungsart"] == "Email"

    def test_job_without_email_is_marked_portal(self):
        row = job_to_sheet_row({"url": "https://x.de/1", "email_hr": ""})
        assert row["Bewerbungsart"] == "Portal"

    def test_partial_payload_never_overwrites_the_column(self):
        """`--step poll` trimite doar un subset de câmpuri. Dacă `email_hr`
        lipsește CU TOTUL din payload, n-avem de unde ști ruta — a scrie
        „Portal" ar șterge marcajul corect al unui rând trimisibil.
        """
        row = job_to_sheet_row({"url": "https://x.de/1", "email_status": "GESENDET"})
        assert "Bewerbungsart" not in row

    def test_blank_email_key_still_means_portal(self):
        """Cheia prezentă, dar goală, e o informație: chiar nu există adresă."""
        row = job_to_sheet_row({"url": "https://x.de/1", "email_hr": "   "})
        assert row["Bewerbungsart"] == "Portal"
