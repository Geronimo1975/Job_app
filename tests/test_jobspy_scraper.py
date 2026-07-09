"""
Teste unitare pentru JobSpyScraper._normalize_job — funcție pură, fără rețea.
"""

import re

from scrapers.jobspy_scraper import JobSpyScraper


def test_normalize_job_includes_gefunden_am(mock_config, tmp_path):
    scraper = JobSpyScraper(mock_config, tmp_path)
    row = {
        "job_url": "https://example.com/job/1",
        "title": "Python Developer",
        "company": "TestCo",
        "site": "indeed",
    }

    job = scraper._normalize_job(row, keyword="Python", location="Berlin")

    assert job is not None
    assert "gefunden_am" in job
    assert re.match(r"^\d{4}-\d{2}-\d{2}$", job["gefunden_am"])
