"""Teste dashboard jobs. Rulare: python dashboard/manage.py test jobs"""
import os
from unittest.mock import Mock, patch

from django.test import SimpleTestCase, TestCase

from jobs import extractor
from jobs.models import Job


class JobModelTests(TestCase):
    def test_create_job_with_canonical_fields(self):
        job = Job.objects.create(
            url="https://acme.de/jobs/1",
            title="Python Developer",
            company="ACME",
            location="Berlin",
            description="Build things",
            email_hr="jobs@acme.de",
            salary_min=60000,
            salary_max=80000,
            language="en",
            date_posted="2026-07-01",
        )
        self.assertEqual(job.source, "dashboard")
        self.assertEqual(job.extraction_status, "ok")
        self.assertEqual(job.raw_json, {})
        self.assertIsNotNone(job.created_at)

    def test_ordering_newest_first(self):
        Job.objects.create(url="https://a.co/1", title="First")
        Job.objects.create(url="https://a.co/2", title="Second")
        titles = list(Job.objects.values_list("title", flat=True))
        self.assertEqual(titles, ["Second", "First"])

    def test_url_is_unique(self):
        Job.objects.create(url="https://a.co/1", title="First")
        from django.db import IntegrityError
        with self.assertRaises(IntegrityError):
            Job.objects.create(url="https://a.co/1", title="Dup")


def _mock_http(text):
    resp = Mock()
    resp.text = text
    resp.raise_for_status = Mock()
    return resp


def _mock_anthropic(json_text):
    client = Mock()
    msg = Mock()
    msg.content = [Mock(text=json_text)]
    client.messages.create.return_value = msg
    return client


class ExtractorTests(SimpleTestCase):
    @patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"})
    @patch("jobs.extractor.anthropic.Anthropic")
    @patch("jobs.extractor.httpx.get")
    def test_maps_ai_json_to_canonical_fields(self, mock_get, mock_anthropic):
        mock_get.return_value = _mock_http("<html><body>Python Dev at ACME</body></html>")
        mock_anthropic.return_value = _mock_anthropic(
            '{"title": "Python Developer", "company": "ACME", "location": "Berlin", '
            '"description": "Build things", "email_hr": "jobs@acme.de", '
            '"salary_min": 60000, "salary_max": 80000, "language": "en", '
            '"date_posted": "2026-07-01"}'
        )
        result = extractor.extract_job("https://acme.de/jobs/1")
        self.assertEqual(result["extraction_status"], "ok")
        self.assertEqual(result["title"], "Python Developer")
        self.assertEqual(result["company"], "ACME")
        self.assertEqual(result["salary_min"], 60000)
        self.assertEqual(result["email_hr"], "jobs@acme.de")
        self.assertEqual(result["url"], "https://acme.de/jobs/1")
        self.assertEqual(result["source"], "dashboard")

    @patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"})
    @patch("jobs.extractor.anthropic.Anthropic")
    @patch("jobs.extractor.httpx.get")
    def test_salary_as_string_is_coerced(self, mock_get, mock_anthropic):
        mock_get.return_value = _mock_http("<html>x</html>")
        mock_anthropic.return_value = _mock_anthropic(
            '{"title": "X", "salary_min": "60.000 €", "salary_max": null}'
        )
        result = extractor.extract_job("https://acme.de/jobs/9")
        self.assertEqual(result["extraction_status"], "ok")
        self.assertEqual(result["salary_min"], 60000)
        self.assertIsNone(result["salary_max"])

    @patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"})
    @patch("jobs.extractor.anthropic.Anthropic")
    @patch("jobs.extractor.httpx.get")
    def test_non_json_ai_response_is_partial(self, mock_get, mock_anthropic):
        mock_get.return_value = _mock_http("<html>x</html>")
        mock_anthropic.return_value = _mock_anthropic("Îmi pare rău, nu pot extrage.")
        result = extractor.extract_job("https://acme.de/jobs/2")
        self.assertEqual(result["extraction_status"], "partial")
        self.assertEqual(result["title"], "")
        self.assertIn("raw", result["raw_json"])

    @patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"})
    @patch("jobs.extractor.httpx.get", side_effect=Exception("timeout"))
    def test_fetch_failure_is_error(self, mock_get):
        result = extractor.extract_job("https://broken.example/x")
        self.assertEqual(result["extraction_status"], "error")
        self.assertEqual(result["title"], "")
        self.assertIn("error", result["raw_json"])

    @patch.dict(os.environ, {}, clear=True)
    def test_missing_api_key_is_error(self):
        result = extractor.extract_job("https://acme.de/jobs/3")
        self.assertEqual(result["extraction_status"], "error")
        self.assertIn("ANTHROPIC_API_KEY", result["raw_json"]["error"])
