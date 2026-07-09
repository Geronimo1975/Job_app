"""
Teste unitare pentru CareerPageScraper — extragere email din pagină/Impressum.
"""

from unittest.mock import MagicMock, patch

import pytest
from bs4 import BeautifulSoup

from scrapers.career_page_scraper import CareerPageScraper


@pytest.fixture
def scraper(mock_config, tmp_path):
    return CareerPageScraper(mock_config, tmp_path)


class TestExtractEmail:
    def test_finds_mailto_link(self, scraper):
        soup = BeautifulSoup(
            '<html><body><a href="mailto:bewerbung@studio.de">Kontakt</a></body></html>',
            "html.parser",
        )
        email = scraper._extract_email(soup, "https://studio.de/careers", {})
        assert email == "bewerbung@studio.de"

    def test_prefers_preferred_prefix_over_first_match(self, scraper):
        soup = BeautifulSoup(
            '<html><body>'
            '<a href="mailto:webmaster@studio.de">Web</a>'
            '<a href="mailto:karriere@studio.de">Karriere</a>'
            '</body></html>',
            "html.parser",
        )
        email = scraper._extract_email(soup, "https://studio.de/careers", {})
        assert email == "karriere@studio.de"

    def test_ignores_image_filename_false_positives(self, scraper):
        soup = BeautifulSoup(
            "<html><body><p>logo@2x.png ist ein bestand</p></body></html>",
            "html.parser",
        )
        email = scraper._extract_email(soup, "https://studio.de/careers", {})
        assert email == ""

    @patch("scrapers.career_page_scraper.httpx.get")
    def test_falls_back_to_impressum_page(self, mock_get, scraper):
        main_soup = BeautifulSoup(
            '<html><body><a href="/impressum">Impressum</a></body></html>',
            "html.parser",
        )
        impressum_response = MagicMock()
        impressum_response.text = '<html><body><a href="mailto:hr@studio.de">HR</a></body></html>'
        impressum_response.raise_for_status.return_value = None
        mock_get.return_value = impressum_response

        email = scraper._extract_email(main_soup, "https://studio.de/careers", {})

        assert email == "hr@studio.de"
        mock_get.assert_called_once()

    def test_returns_empty_when_nothing_found(self, scraper):
        soup = BeautifulSoup("<html><body><p>Keine Kontaktdaten hier.</p></body></html>", "html.parser")
        email = scraper._extract_email(soup, "https://studio.de/careers", {})
        assert email == ""


class TestScrapeStudioMetadata:
    @patch("scrapers.career_page_scraper.httpx.get")
    def test_scrape_studio_sets_gefunden_am(self, mock_get, scraper):
        response = MagicMock()
        response.text = '<html><body><a href="/jobs/python-dev">Python Developer Position</a></body></html>'
        response.raise_for_status.return_value = None
        mock_get.return_value = response

        jobs = scraper._scrape_studio({
            "name": "TestStudio",
            "career_url": "https://teststudio.de/jobs",
            "email_hr": "hr@teststudio.de",
        })

        assert len(jobs) == 1
        assert "gefunden_am" in jobs[0]
        import re
        assert re.match(r"^\d{4}-\d{2}-\d{2}$", jobs[0]["gefunden_am"])
