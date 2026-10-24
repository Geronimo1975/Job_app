"""
Teste pentru generatorul de pregătire de interviu — determinist, fără AI.
"""

from generators.interview_prep_gen import generate_prep

SKILLS = ["Python", "Django", "Docker", "AI/ML"]


class TestInterviewPrep:
    def test_technical_questions_from_matched_skills(self):
        out = generate_prep("We use Python and Docker.", "Dev", "Acme", SKILLS, "de")
        assert "Erfahrung mit Python" in out
        assert "Erfahrung mit Docker" in out
        # Django nu e în anunț → nu apare ca Fachfrage.
        assert "Erfahrung mit Django" not in out

    def test_empty_match_falls_back(self):
        out = generate_prep("No tech here", "Dev", "Acme", SKILLS, "de")
        assert "Keine Ihrer Kernkompetenzen" in out

    def test_company_injected_in_reverse_questions(self):
        out = generate_prep("Python", "Dev", "Beta GmbH", SKILLS, "de")
        assert "Beta GmbH" in out

    def test_english_output(self):
        out = generate_prep("We use Python", "Engineer", "Acme", SKILLS, "en")
        assert "Interview Preparation" in out
        assert "experience with Python" in out
        assert "Behavioural questions" in out

    def test_unknown_language_falls_back_to_de(self):
        out = generate_prep("Python", "Dev", "Acme", SKILLS, "fr")
        assert "Interview-Vorbereitung" in out

    def test_has_all_sections(self):
        out = generate_prep("Python", "Dev", "Acme", SKILLS, "de")
        for section in ("Fachfragen", "Verhaltensfragen", "stellen sollten", "Vorbereitungstipps"):
            assert section in out

    def test_empty_position_and_company_safe(self):
        out = generate_prep("Python", "", "", SKILLS, "de")
        assert "das Unternehmen" in out
        assert "die Position" in out

    def test_deterministic(self):
        a = generate_prep("Python Docker", "Dev", "Acme", SKILLS, "de")
        b = generate_prep("Python Docker", "Dev", "Acme", SKILLS, "de")
        assert a == b
