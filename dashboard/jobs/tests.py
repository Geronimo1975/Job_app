"""Teste dashboard jobs. Rulare: python dashboard/manage.py test jobs"""
import os
from unittest.mock import Mock, patch

from django.test import SimpleTestCase, TestCase
from django.urls import reverse

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


def _ok_extraction(url):
    return {
        "url": url, "title": "Python Dev", "company": "ACME", "location": "Berlin",
        "description": "desc", "email_hr": "jobs@acme.de", "salary_min": 60000,
        "salary_max": 80000, "language": "en", "date_posted": "2026-07-01",
        "source": "dashboard", "extraction_status": "ok", "raw_json": {},
    }


class ViewTests(TestCase):
    @patch("jobs.views.extract_job")
    def test_add_job_creates_row_and_redirects(self, mock_extract):
        url = "https://acme.de/jobs/1"
        mock_extract.return_value = _ok_extraction(url)
        resp = self.client.post(reverse("add_job"), {"url": url})
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(Job.objects.count(), 1)
        self.assertEqual(Job.objects.get().title, "Python Dev")

    @patch("jobs.views.extract_job")
    def test_duplicate_url_updates_not_duplicates(self, mock_extract):
        url = "https://acme.de/jobs/1"
        mock_extract.return_value = _ok_extraction(url)
        self.client.post(reverse("add_job"), {"url": url})
        updated = _ok_extraction(url)
        updated["title"] = "Senior Python Dev"
        mock_extract.return_value = updated
        self.client.post(reverse("add_job"), {"url": url})
        self.assertEqual(Job.objects.count(), 1)
        self.assertEqual(Job.objects.get().title, "Senior Python Dev")

    def test_invalid_url_rejected(self):
        # fără follow: nu randăm index.html (nu există până la Task 5)
        resp = self.client.post(reverse("add_job"), {"url": "not-a-url"})
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(Job.objects.count(), 0)

    def test_delete_job(self):
        job = Job.objects.create(url="https://a.co/1", title="Alpha")
        resp = self.client.post(reverse("delete_job", args=[job.pk]))
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(Job.objects.count(), 0)


class RenderTests(TestCase):
    def test_index_lists_jobs(self):
        Job.objects.create(url="https://a.co/1", title="Alpha", company="A")
        resp = self.client.get(reverse("index"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Alpha")

    def test_empty_index_shows_hint(self):
        resp = self.client.get(reverse("index"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Niciun job încă")

    def test_detail_renders(self):
        job = Job.objects.create(url="https://a.co/1", title="Alpha", description="Detalii aici")
        resp = self.client.get(reverse("job_detail", args=[job.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Detalii aici")
