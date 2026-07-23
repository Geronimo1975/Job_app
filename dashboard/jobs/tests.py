"""Teste dashboard jobs. Rulare: python dashboard/manage.py test jobs"""
from django.test import TestCase
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
