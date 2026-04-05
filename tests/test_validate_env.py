"""
Teste pentru validate_environment() din main.py.
"""

import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

PROJECT_ROOT = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(PROJECT_ROOT))

from tests.conftest import MOCK_CONFIG
from main import validate_environment


@pytest.fixture
def config():
    """Config mock pentru teste."""
    return MOCK_CONFIG.copy()


class TestValidateEnvironment:
    """Teste pentru funcția validate_environment()."""

    def test_all_ok(self, config):
        """Trece când toate env vars sunt setate."""
        env = os.environ.copy()
        env["ANTHROPIC_API_KEY"] = "sk-test-key"
        env["GMX_PASSWORD"] = "test-pass"
        with patch.dict(os.environ, env, clear=True):
            result = validate_environment(config)
        assert result is True

    def test_missing_anthropic_key(self, config):
        """Eșuează când ANTHROPIC_API_KEY lipsește."""
        env = os.environ.copy()
        env.pop("ANTHROPIC_API_KEY", None)
        env.pop("GMX_PASSWORD", None)
        with patch.dict(os.environ, env, clear=True):
            result = validate_environment(config)
        assert result is False

    def test_missing_smtp_key(self, config):
        """Eșuează când cheia SMTP a provider-ului activ lipsește."""
        env = os.environ.copy()
        env.pop("GMX_PASSWORD", None)
        env["ANTHROPIC_API_KEY"] = "sk-test"
        with patch.dict(os.environ, env, clear=True):
            result = validate_environment(config)
        assert result is False

    def test_brevo_provider(self, config):
        """Verifică cheia BREVO_SMTP_KEY când provider-ul este brevo."""
        config["email"]["providers"]["brevo"] = {
            "server": "smtp-relay.brevo.com",
            "port": 587,
            "login": "test@gmx.de",
            "env_password_key": "BREVO_SMTP_KEY",
        }
        env = os.environ.copy()
        env["ANTHROPIC_API_KEY"] = "sk-test"
        env["BREVO_SMTP_KEY"] = "brevo-key"
        with patch.dict(os.environ, env, clear=True):
            result = validate_environment(config, provider="brevo")
        assert result is True

    def test_both_keys_present(self, config):
        """Trece când ambele chei critice sunt setate."""
        env = os.environ.copy()
        env["ANTHROPIC_API_KEY"] = "sk-test"
        env["GMX_PASSWORD"] = "pass"
        with patch.dict(os.environ, env, clear=True):
            result = validate_environment(config)
        assert result is True

    def test_empty_env_values_treated_as_missing(self, config):
        """Valori goale sunt tratate ca lipsă."""
        env = os.environ.copy()
        env["ANTHROPIC_API_KEY"] = ""
        env["GMX_PASSWORD"] = ""
        with patch.dict(os.environ, env, clear=True):
            result = validate_environment(config)
        assert result is False
