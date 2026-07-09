from sender.n8n_mapping import job_to_sheet_row, sheet_row_to_job


def test_job_to_sheet_row_maps_core_and_analysis():
    job = {
        "url": "https://x/job/1", "company": "Studio X", "title": "Python Dev",
        "location": "Berlin", "source": "jobspy", "email_hr": "hr@x.de",
        "contact_person": "Frau Y", "language": "de", "status": "Bereit",
        "cover_letter": "Sehr geehrte...",
        "analysis": {"match_score": 72, "matching_skills": ["Python", "Django"]},
    }
    row = job_to_sheet_row(job)
    assert row["Apply URL"] == "https://x/job/1"
    assert row["Firma"] == "Studio X"
    assert row["Position"] == "Python Dev"
    assert row["Kontakt Email"] == "hr@x.de"
    assert row["Sprache"] == "de"
    assert row["Status"] == "Bereit"
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


def test_job_to_sheet_row_tolerates_missing_fields():
    row = job_to_sheet_row({"url": "u"})
    assert row["Apply URL"] == "u"
    assert row["Firma"] == ""
    assert row["Match Score"] == ""


def test_job_to_sheet_row_maps_gefunden_am():
    row = job_to_sheet_row({"url": "u", "gefunden_am": "2026-07-09"})
    assert row["Datum gefunden"] == "2026-07-09"


def test_sheet_row_to_job_roundtrips_gefunden_am():
    job = sheet_row_to_job({"Apply URL": "u", "Datum gefunden": "2026-07-09"})
    assert job["gefunden_am"] == "2026-07-09"
