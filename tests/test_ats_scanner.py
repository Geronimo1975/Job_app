"""
Teste pentru ATS CV Scanner — miez pur, fără disc.
"""

from ats_scanner import scan_cv, extract_pdf_text

SKILLS = ["Python", "Django", "Docker", "AWS", "AI/ML"]


class TestScanCv:
    def test_full_match(self):
        job = "We need Python and Docker."
        cv = "Experienced in Python and Docker."
        r = scan_cv(cv, job, SKILLS)
        assert r["score"] == 100
        assert set(r["present"]) == {"Python", "Docker"}
        assert r["missing"] == []

    def test_partial_match(self):
        job = "We need Python, Docker and AWS."
        cv = "I know Python."
        r = scan_cv(cv, job, SKILLS)
        assert r["wanted"] == ["Python", "Docker", "AWS"]
        assert r["present"] == ["Python"]
        assert set(r["missing"]) == {"Docker", "AWS"}
        assert r["score"] == 33  # 1/3

    def test_no_wanted_skills_scores_100(self):
        # Anunțul nu cere niciun skill cunoscut → nimic de dovedit.
        r = scan_cv("anything", "We need carpentry and welding.", SKILLS)
        assert r["score"] == 100
        assert r["wanted"] == []

    def test_empty_cv_all_missing(self):
        r = scan_cv("", "We need Python and Docker.", SKILLS)
        assert r["score"] == 0
        assert set(r["missing"]) == {"Python", "Docker"}

    def test_whole_word_matching(self):
        # „ml" din AI/ML nu se prinde în „html" nici în job, nici în CV.
        r = scan_cv("html experience", "html stack", SKILLS)
        assert r["wanted"] == []

    def test_case_insensitive(self):
        r = scan_cv("python expert", "PYTHON required", SKILLS)
        assert r["present"] == ["Python"]


class TestExtractPdfText:
    def test_missing_file_returns_empty(self):
        assert extract_pdf_text("/nonexistent/file.pdf") == ""

    def test_non_pdf_returns_empty(self, tmp_path):
        p = tmp_path / "x.pdf"
        p.write_text("not a real pdf")
        assert extract_pdf_text(p) == ""
