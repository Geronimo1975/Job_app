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
    assert row["URL"] == "https://x/job/1"
    assert row["Firma"] == "Studio X"
    assert row["Position"] == "Python Dev"
    assert row["Kontakt Email"] == "hr@x.de"
    assert row["Sprache"] == "de"
    assert row["Status"] == "Bereit"
    assert row["Match Score"] == 72
    assert row["Keywords Match"] == "Python, Django"
    assert row["Anschreiben"] == "Sehr geehrte..."


def test_sheet_row_to_job_inverse_core():
    cmd = {
        "action": "send", "URL": "https://x/job/1", "Firma": "Studio X",
        "Position": "Python Dev", "Kontakt Email": "hr@x.de", "Sprache": "de",
        "Anschreiben": "Sehr geehrte...",
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
    assert row["URL"] == "u"
    assert row["Firma"] == ""
    assert row["Match Score"] == ""
