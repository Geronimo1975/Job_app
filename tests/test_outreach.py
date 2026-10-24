"""
Teste pentru helperii de outreach (link LinkedIn, fără scraping).
"""

from urllib.parse import parse_qs, urlsplit

from outreach import linkedin_people_search_url


class TestLinkedInSearchUrl:
    def test_builds_people_search_url(self):
        url = linkedin_people_search_url("Acme GmbH")
        assert url.startswith(
            "https://www.linkedin.com/search/results/people/?keywords="
        )

    def test_company_name_is_in_keywords(self):
        url = linkedin_people_search_url("Acme Media GmbH")
        kw = parse_qs(urlsplit(url).query)["keywords"][0]
        assert '"Acme Media GmbH"' in kw
        assert "recruiter" in kw.lower()

    def test_empty_company_returns_empty(self):
        assert linkedin_people_search_url("") == ""
        assert linkedin_people_search_url("   ") == ""
        assert linkedin_people_search_url(None) == ""

    def test_special_chars_are_encoded(self):
        url = linkedin_people_search_url("Müller & Co")
        # Fără spații brute sau & în query — totul percent-encoded.
        assert " " not in url
        assert url.count("&") == 0

    def test_custom_roles(self):
        url = linkedin_people_search_url("Acme", roles="CTO OR Engineering")
        kw = parse_qs(urlsplit(url).query)["keywords"][0]
        assert "CTO" in kw
