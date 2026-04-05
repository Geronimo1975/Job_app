"""
Fixtures partajate pentru test suite.
"""

import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# Adaugă project root în path
PROJECT_ROOT = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(PROJECT_ROOT))


# ── Config fixtures ────────────────────────────────────────────

MOCK_CONFIG = {
    "applicant": {
        "name": "Test User",
        "email": "test@example.com",
        "phone": "+49 123 456 7890",
        "location": "Deutschland",
        "languages": ["Deutsch (B2)", "Englisch (C1)"],
    },
    "skills": {
        "primary": ["Python", "Django", "AI/ML"],
        "secondary": ["JavaScript", "Docker", "PostgreSQL", "REST APIs", "Git"],
        "gaming_specific": ["Unity", "Game Design", "QA Automation"],
    },
    "scraping": {
        "sources": {"jobspy": True, "career_pages": True},
        "search_keywords": ["Python Developer"],
        "locations": ["Germany"],
        "max_results_per_source": 10,
        "filters": {"exclude_keywords": ["VP", "Director"]},
    },
    "ai": {
        "provider": "anthropic",
        "model": "claude-sonnet-4-20250514",
        "temperature": 0.7,
        "max_tokens": 2000,
        "default_language": "de",
    },
    "email": {
        "active_provider": "gmx",
        "providers": {
            "gmx": {
                "server": "mail.gmx.net",
                "port": 587,
                "login": "test@gmx.de",
                "env_password_key": "GMX_PASSWORD",
            },
        },
        "max_emails_per_day": 10,
        "delay_between_emails_sec": 0,
        "dry_run_default": True,
    },
    "tracker": {
        "file": "data/Job_Application_Tracker.xlsx",
        "auto_backup": False,
        "backup_dir": "data/backups",
    },
    "documents": {
        "cv_file": "documents/cv/test_cv.pdf",
        "certificates_dir": "documents/certificates",
        "references_dir": "documents/references",
        "cover_letters_dir": "documents/cover_letters/generated",
        "templates_dir": "documents/cover_letters/templates",
    },
    "output": {
        "logs_dir": "output/logs",
        "reports_dir": "output/reports",
        "sent_dir": "output/sent_emails",
        "log_level": "WARNING",
    },
}


@pytest.fixture
def mock_config():
    """Returnează configurația mock."""
    return MOCK_CONFIG.copy()


@pytest.fixture
def sample_job():
    """Un job de test standard."""
    return {
        "id": "test_001",
        "source": "career_page",
        "platform": "Test Platform",
        "title": "Python Developer (m/w/d)",
        "company": "Test GmbH",
        "location": "Berlin",
        "url": "https://example.com/jobs/python-dev",
        "email_hr": "hr@test-gmbh.de",
        "contact_person": "Frau Schmidt",
        "language": "de",
        "description": (
            "Python Developer (m/w/d) — Test GmbH, Berlin\n\n"
            "Aufgaben:\n"
            "- Entwicklung von Backend-Services mit Python und Django\n"
            "- REST API Design und Implementierung\n"
            "- CI/CD Pipeline Wartung mit Docker\n"
            "- Datenbankdesign mit PostgreSQL\n\n"
            "Anforderungen:\n"
            "- 3+ Jahre Python Erfahrung\n"
            "- Django oder Flask Kenntnisse\n"
            "- Docker und Git\n"
            "- Fließend Deutsch und Englisch"
        ),
        "scraped_at": datetime.now().isoformat(),
        "applied": False,
        "status": "new",
    }


@pytest.fixture
def sample_job_analyzed(sample_job):
    """Job cu analiza completă."""
    job = sample_job.copy()
    job["analysis"] = {
        "match_score": 75,
        "matching_skills": ["Python", "Django", "Docker", "Git"],
        "missing_skills": ["Flask"],
        "key_requirements": ["3+ Jahre Python", "Django", "Docker"],
        "summary": "Bun match pentru backend Python developer.",
    }
    return job


@pytest.fixture
def sample_jobs(sample_job):
    """Lista de joburi de test."""
    job2 = sample_job.copy()
    job2.update({
        "id": "test_002",
        "title": "AI Engineer",
        "company": "GameStudio AG",
        "location": "München",
        "email_hr": "jobs@gamestudio.de",
        "description": (
            "AI Engineer — GameStudio AG\n\n"
            "- Machine Learning pipeline development\n"
            "- Python, TensorFlow, PyTorch\n"
            "- Game AI systems\n"
            "- Unity integration"
        ),
    })
    return [sample_job, job2]


@pytest.fixture
def tmp_project(tmp_path):
    """Creează o structură de proiect temporară."""
    # Directoare necesare
    (tmp_path / "config").mkdir()
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "backups").mkdir()
    (tmp_path / "output" / "logs").mkdir(parents=True)
    (tmp_path / "output" / "reports").mkdir(parents=True)
    (tmp_path / "output" / "sent_emails").mkdir(parents=True)
    (tmp_path / "documents" / "cv").mkdir(parents=True)
    (tmp_path / "documents" / "certificates").mkdir(parents=True)
    (tmp_path / "documents" / "references").mkdir(parents=True)
    (tmp_path / "documents" / "cover_letters" / "generated").mkdir(parents=True)
    (tmp_path / "documents" / "cover_letters" / "templates").mkdir(parents=True)
    (tmp_path / "config" / "email_templates").mkdir(parents=True)

    # Scrie settings.yaml
    import yaml
    config_path = tmp_path / "config" / "settings.yaml"
    config = MOCK_CONFIG.copy()
    with open(config_path, "w") as f:
        yaml.dump(config, f)

    # Scrie un CV dummy
    cv_path = tmp_path / "documents" / "cv" / "test_cv.pdf"
    cv_path.write_bytes(b"%PDF-1.4 dummy cv content")

    # Scrie un template email dummy
    tpl = tmp_path / "config" / "email_templates" / "de_generic.txt"
    tpl.write_text(
        "Sehr geehrte/r {kontakt_person},\n\n"
        "hiermit bewerbe ich mich als {position} bei {firma}.\n\n"
        "Mit freundlichen Grüßen\n{sender_name}\n"
    )

    return tmp_path
