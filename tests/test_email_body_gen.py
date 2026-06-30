"""
Teste pentru EmailBodyGenerator — salut corect, fallback pe limbă, personalizare.
"""

from pathlib import Path

from generators.email_body_gen import EmailBodyGenerator

ROOT = Path(__file__).parent.parent.resolve()
CFG = {
    "applicant": {
        "name": "Test User",
        "email": "test@example.com",
        "phone": "+49 123",
        "linkedin": "li",
        "github": "gh",
    },
    "skills": {"primary": ["Python", "Django", "AI/ML"]},
}


def _gen():
    return EmailBodyGenerator(CFG, ROOT)


def test_salutation_herr_is_grammatically_correct():
    body = _gen().generate(
        company="X GmbH", position="Developer", contact_person="Herr Müller", language="de"
    )
    assert "Sehr geehrter Herr Müller," in body


def test_salutation_frau_is_grammatically_correct():
    body = _gen().generate(
        company="X GmbH", position="Developer", contact_person="Frau Schmidt", language="de"
    )
    assert "Sehr geehrte Frau Schmidt," in body
    assert "Sehr geehrter Frau" not in body


def test_english_no_contact_uses_english_template_not_german_fallback():
    body = _gen().generate(
        company="X Ltd", position="Engineer", contact_person="", language="en"
    )
    assert "Dear" in body
    assert "Sehr geehrte" not in body  # NU cade pe german (en_generic.txt există)


def test_personalized_paragraph_uses_matching_skills_and_position():
    body = _gen().generate(
        company="Kolibri", position="QA Tester", contact_person="", language="de",
        analysis={
            "matching_skills": ["Testautomatisierung", "Python"],
            "key_requirements": ["CI/CD Pipelines"],
        },
    )
    assert "Testautomatisierung" in body
    assert "QA Tester" in body
    assert "CI/CD Pipelines" in body
