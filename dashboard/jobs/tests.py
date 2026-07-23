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

    def test_package_fields_defaults(self):
        job = Job.objects.create(url="https://a.co/9", title="X")
        self.assertEqual(job.contact_person, "")
        self.assertEqual(job.notizen, "")
        self.assertIsNone(job.match_score)
        self.assertEqual(job.send_state, "draft")
        self.assertEqual(job.email_subject, "")
        self.assertEqual(job.email_body, "")
        self.assertEqual(job.anschreiben_text, "")
        self.assertEqual(job.attachments_json, [])
        self.assertIsNone(job.generated_at)
        self.assertIsNone(job.sent_at)
        self.assertEqual(job.send_error, "")
        self.assertFalse(hasattr(job, "seen_in_pending"))

    def test_sender_is_importable_from_dashboard(self):
        # sys.path-ul din settings.py trebuie să facă sender/ importabil
        from sender.n8n_mapping import FIELD_MAP
        self.assertEqual(FIELD_MAP["url"], "Apply URL")


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
    def test_salary_float_is_not_mangled(self, mock_get, mock_anthropic):
        # 60000.0 nu trebuie să devină 600000 (regex-ul pe str ar scoate punctul)
        mock_get.return_value = _mock_http("<html>x</html>")
        mock_anthropic.return_value = _mock_anthropic(
            '{"title": "X", "salary_min": 60000.0, "salary_max": 65000.5}'
        )
        result = extractor.extract_job("https://acme.de/jobs/10")
        self.assertEqual(result["salary_min"], 60000)
        self.assertEqual(result["salary_max"], 65000)

    @patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"})
    @patch("jobs.extractor.anthropic.Anthropic")
    @patch("jobs.extractor.httpx.get")
    def test_non_object_json_is_partial(self, mock_get, mock_anthropic):
        # JSON valid dar non-obiect (listă) = extragere neutilizabilă → partial, nu ok
        mock_get.return_value = _mock_http("<html>x</html>")
        mock_anthropic.return_value = _mock_anthropic('[{"title": "X"}]')
        result = extractor.extract_job("https://acme.de/jobs/11")
        self.assertEqual(result["extraction_status"], "partial")
        self.assertEqual(result["title"], "")

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

    @patch("jobs.views.extract_job")
    def test_failed_reextraction_keeps_existing_data(self, mock_extract):
        # Eroare tranzitorie la re-lipirea aceluiași URL NU șterge datele bune salvate
        url = "https://acme.de/jobs/1"
        mock_extract.return_value = _ok_extraction(url)
        self.client.post(reverse("add_job"), {"url": url})
        failed = {
            "url": url, "title": "", "company": "", "location": "", "description": "",
            "email_hr": "", "salary_min": None, "salary_max": None, "language": "",
            "date_posted": "", "source": "dashboard", "extraction_status": "error",
            "raw_json": {"error": "fetch eșuat: timeout"},
        }
        mock_extract.return_value = failed
        self.client.post(reverse("add_job"), {"url": url})
        self.assertEqual(Job.objects.count(), 1)
        job = Job.objects.get()
        self.assertEqual(job.title, "Python Dev")          # datele vechi păstrate
        self.assertEqual(job.extraction_status, "ok")      # statusul bun neatins

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


class SheetBridgeTests(TestCase):
    def _job(self, **kw):
        base = dict(url="https://acme.de/jobs/1", title="Python Dev", company="ACME",
                    location="Berlin", email_hr="jobs@acme.de", language="en",
                    contact_person="Anna Schmidt", notizen="de sunat", match_score=85)
        base.update(kw)
        return Job.objects.create(**base)

    def test_job_to_internal_maps_and_omits_blanks(self):
        from jobs import sheet_bridge
        job = self._job(location="", match_score=None)
        internal = sheet_bridge.job_to_internal(job)
        self.assertEqual(internal["company"], "ACME")
        self.assertEqual(internal["contact_person"], "Anna Schmidt")
        self.assertEqual(internal["gefunden_am"], job.created_at.strftime("%Y-%m-%d"))
        self.assertNotIn("location", internal)      # gol → omis
        self.assertNotIn("match_score", internal)   # None → omis
        self.assertNotIn("status", internal)        # col M nu se emite NICIODATĂ
        self.assertNotIn("description", internal)
        self.assertNotIn("sende_befehl", internal)  # doar la cerere

    @patch.dict(os.environ, {}, clear=True)
    @patch("jobs.sheet_bridge.load_pipeline_config",
           return_value={"n8n": {"enabled": True, "secret_env": "N8N_WEBHOOK_SECRET"}})
    def test_push_without_secret_fails_without_http(self, _cfg):
        from jobs import sheet_bridge
        res = sheet_bridge.push_to_sheet(self._job())
        self.assertFalse(res["ok"])
        self.assertIn("N8N_WEBHOOK_SECRET", res["error"])

    @patch("jobs.sheet_bridge.load_pipeline_config", return_value={"n8n": {"enabled": False}})
    def test_push_with_n8n_disabled_fails(self, _cfg):
        from jobs import sheet_bridge
        res = sheet_bridge.push_to_sheet(self._job())
        self.assertFalse(res["ok"])
        self.assertIn("dezactivat", res["error"])

    @patch.dict(os.environ, {"N8N_WEBHOOK_SECRET": "s3cret"})
    @patch("jobs.sheet_bridge.N8nClient")
    @patch("jobs.sheet_bridge.load_pipeline_config",
           return_value={"n8n": {"enabled": True, "secret_env": "N8N_WEBHOOK_SECRET"}})
    def test_push_ok_never_emits_sende_befehl(self, _cfg, mock_client_cls):
        from jobs import sheet_bridge
        mock_client_cls.return_value.ingest.return_value = {"ok": True}
        res = sheet_bridge.push_to_sheet(self._job())
        self.assertTrue(res["ok"])
        sent_jobs = mock_client_cls.return_value.ingest.call_args[0][0]
        self.assertNotIn("sende_befehl", sent_jobs[0])
        self.assertNotIn("status", sent_jobs[0])

    @patch.dict(os.environ, {"N8N_WEBHOOK_SECRET": "s3cret"})
    @patch("jobs.sheet_bridge.N8nClient")
    @patch("jobs.sheet_bridge.load_pipeline_config",
           return_value={"n8n": {"enabled": True, "secret_env": "N8N_WEBHOOK_SECRET"}})
    def test_push_http_error_returns_error(self, _cfg, mock_client_cls):
        from jobs import sheet_bridge
        mock_client_cls.return_value.ingest.side_effect = RuntimeError("n8n 5xx")
        res = sheet_bridge.push_to_sheet(self._job())
        self.assertFalse(res["ok"])
        self.assertIn("n8n 5xx", res["error"])

    def test_push_blank_url_fails_without_http(self):
        from jobs import sheet_bridge
        job = self._job(url="   ")
        res = sheet_bridge.push_to_sheet(job)
        self.assertFalse(res["ok"])
        self.assertIn("URL", res["error"])

    def test_gefunden_am_uses_berlin_date(self):
        # 23:30 UTC = 01:30 Berlin a doua zi (vară) — data trebuie să fie cea locală
        import datetime
        from jobs import sheet_bridge
        job = self._job()
        utc_dt = datetime.datetime(2026, 7, 22, 23, 30, tzinfo=datetime.timezone.utc)
        Job.objects.filter(pk=job.pk).update(created_at=utc_dt)
        job.refresh_from_db()
        internal = sheet_bridge.job_to_internal(job)
        self.assertEqual(internal["gefunden_am"], "2026-07-23")

    @patch.dict(os.environ, {"N8N_WEBHOOK_SECRET": "s3cret"})
    @patch("jobs.sheet_bridge.N8nClient")
    @patch("jobs.sheet_bridge.load_pipeline_config",
           return_value={"n8n": {"enabled": True, "secret_env": "N8N_WEBHOOK_SECRET"}})
    def test_mark_sent_writes_gesendet(self, _cfg, mock_client_cls):
        import datetime
        from jobs import sheet_bridge
        job = self._job()
        job.sent_at = datetime.datetime(2026, 7, 23, 12, 0, tzinfo=datetime.timezone.utc)
        res = sheet_bridge.mark_sent_in_sheet(job)
        self.assertTrue(res["ok"])
        payload = mock_client_cls.return_value.ingest.call_args[0][0][0]
        self.assertEqual(payload["email_status"], "GESENDET")
        self.assertEqual(payload["url"], job.url)
        self.assertTrue(payload["gesendet_am"].startswith("2026-07-23"))

    @patch("jobs.sheet_bridge.load_pipeline_config", return_value={"n8n": {"enabled": False}})
    def test_mark_sent_disabled_fails(self, _cfg):
        from jobs import sheet_bridge
        res = sheet_bridge.mark_sent_in_sheet(self._job())
        self.assertFalse(res["ok"])

class SheetViewTests(TestCase):
    def _job(self, **kw):
        base = dict(url="https://acme.de/jobs/1", title="Python Dev", company="ACME",
                    email_hr="hr@acme.de")
        base.update(kw)
        return Job.objects.create(**base)

    def test_detail_form_saves_new_fields(self):
        job = self._job()
        resp = self.client.post(reverse("job_detail", args=[job.pk]), {
            "title": "Python Dev", "company": "ACME", "location": "Berlin",
            "email_hr": "hr@acme.de", "contact_person": "Anna Schmidt",
            "language": "de", "match_score": "90", "notizen": "urgent",
        })
        self.assertEqual(resp.status_code, 302)
        job.refresh_from_db()
        self.assertEqual(job.contact_person, "Anna Schmidt")
        self.assertEqual(job.match_score, 90)
        self.assertEqual(job.notizen, "urgent")

    @patch("jobs.views.push_to_sheet", return_value={"ok": True})
    def test_push_job_does_not_change_send_state(self, mock_push):
        job = self._job()
        resp = self.client.post(reverse("push_job", args=[job.pk]))
        self.assertEqual(resp.status_code, 302)
        mock_push.assert_called_once()
        job.refresh_from_db()
        self.assertEqual(job.send_state, "draft")

    @patch("jobs.views.push_to_sheet", return_value={"ok": True})
    def test_push_job_get_does_not_push(self, mock_push):
        # Singura gardă între un GET și o scriere în Sheet-ul de producție
        job = self._job()
        resp = self.client.get(reverse("push_job", args=[job.pk]))
        self.assertEqual(resp.status_code, 302)
        mock_push.assert_not_called()

class GenerationBridgeTests(TestCase):
    def _job(self, **kw):
        base = dict(url="https://acme.de/jobs/1", title="Python Dev", company="ACME",
                    email_hr="hr@acme.de", language="de", description="Beschreibung")
        base.update(kw)
        return Job.objects.create(**base)

    @patch("jobs.generation_bridge.assemble_bewerbung")
    @patch("jobs.generation_bridge.text_to_pdf")
    @patch("jobs.generation_bridge.EmailBodyGenerator")
    @patch("jobs.generation_bridge.CoverLetterGenerator")
    @patch("jobs.generation_bridge.load_pipeline_config",
           return_value={"applicant": {"name": "George"}})
    def test_generate_package_populates_all_fields(self, _cfg, mock_cl, mock_eb, mock_pdf, mock_asm):
        import tempfile
        from pathlib import Path
        from jobs import generation_bridge
        with tempfile.TemporaryDirectory() as td:
            txt = Path(td) / "Anschreiben_ACME.txt"
            txt.write_text("Sehr geehrte...", encoding="utf-8")
            mock_cl.return_value.generate.return_value = "Sehr geehrte..."
            mock_cl.return_value.save_cover_letter.return_value = txt
            mock_eb.return_value.generate.return_value = "Body email"
            res = generation_bridge.generate_package(self._job())
        self.assertTrue(res["ok"])
        self.assertEqual(res["email_subject"], "Bewerbung als Python Dev — George")
        self.assertEqual(res["email_body"], "Body email")
        self.assertEqual(res["anschreiben_text"], "Sehr geehrte...")
        self.assertTrue(res["anschreiben_pdf_path"].endswith(".pdf"))
        mock_pdf.assert_called_once()

    @patch("jobs.generation_bridge.CoverLetterGenerator")
    @patch("jobs.generation_bridge.load_pipeline_config", return_value={})
    def test_generate_package_ai_error_is_error(self, _cfg, mock_cl):
        from jobs import generation_bridge
        mock_cl.return_value.generate.side_effect = Exception("AI down")
        res = generation_bridge.generate_package(self._job())
        self.assertFalse(res["ok"])
        self.assertIn("AI down", res["error"])

    @patch("jobs.generation_bridge.SmtpSender")
    @patch("jobs.generation_bridge.load_pipeline_config", return_value={})
    def test_send_package_dry_run(self, _cfg, mock_smtp):
        from jobs import generation_bridge
        mock_smtp.return_value.send.return_value = True
        job = self._job(email_subject="S", email_body="B")
        res = generation_bridge.send_package(job, live=False)
        self.assertTrue(res["ok"])
        self.assertFalse(res["live"])
        self.assertTrue(mock_smtp.return_value.send.call_args.kwargs["dry_run"])

    @patch("jobs.generation_bridge.SmtpSender")
    @patch("jobs.generation_bridge.load_pipeline_config", return_value={})
    def test_send_package_live(self, _cfg, mock_smtp):
        from jobs import generation_bridge
        mock_smtp.return_value.send.return_value = True
        res = generation_bridge.send_package(
            self._job(email_subject="S", email_body="B"), live=True)
        self.assertTrue(res["ok"])
        self.assertTrue(res["live"])
        self.assertFalse(mock_smtp.return_value.send.call_args.kwargs["dry_run"])

    @patch("jobs.generation_bridge.load_pipeline_config", return_value={})
    def test_send_package_without_email_fails(self, _cfg):
        from jobs import generation_bridge
        res = generation_bridge.send_package(self._job(email_hr=""), live=True)
        self.assertFalse(res["ok"])
        self.assertIn("Kontakt Email", res["error"])

    @patch("jobs.generation_bridge.SmtpSender")
    @patch("jobs.generation_bridge.load_pipeline_config", return_value={})
    def test_send_package_refused_is_error(self, _cfg, mock_smtp):
        from jobs import generation_bridge
        mock_smtp.return_value.send.return_value = False
        res = generation_bridge.send_package(
            self._job(email_subject="S", email_body="B"), live=True)
        self.assertFalse(res["ok"])
        self.assertIn("refuzat", res["error"])

    @patch.dict(os.environ, {"N8N_WEBHOOK_SECRET": "s"})
    @patch("jobs.sheet_bridge.N8nClient")  # clientul se construiește în _client_or_error
    @patch("jobs.generation_bridge.load_pipeline_config",
           return_value={"n8n": {"enabled": True, "secret_env": "N8N_WEBHOOK_SECRET"}})
    def test_already_sent_checks_known_urls(self, _cfg, mock_client):
        from jobs import generation_bridge
        mock_client.return_value.get_known_urls.return_value = ["https://acme.de/jobs/1"]
        self.assertEqual(generation_bridge.already_sent_in_sheet(self._job()), "da")
        mock_client.return_value.get_known_urls.return_value = []
        self.assertEqual(
            generation_bridge.already_sent_in_sheet(self._job(url="https://x.de/2")), "nu")

    @patch("jobs.generation_bridge.load_pipeline_config", side_effect=Exception("boom"))
    def test_already_sent_unknown_on_error(self, _cfg):
        from jobs import generation_bridge
        self.assertIn("necunoscut", generation_bridge.already_sent_in_sheet(self._job()))


class PreviewSendViewTests(TestCase):
    def _job(self, **kw):
        base = dict(url="https://acme.de/jobs/1", title="Python Dev", company="ACME",
                    email_hr="hr@acme.de", send_state="generated",
                    email_subject="S", email_body="B", anschreiben_text="A",
                    attachments_json=[])
        base.update(kw)
        return Job.objects.create(**base)

    @patch("jobs.views.generate_package", return_value={
        "ok": True, "email_subject": "Subj", "email_body": "Body",
        "anschreiben_text": "Text", "anschreiben_pdf_path": "/tmp/a.pdf",
        "bewerbung_pdf_path": "", "attachments": ["/tmp/a.pdf"]})
    def test_execute_populates_and_redirects_to_preview(self, _gen):
        job = self._job(send_state="draft")
        resp = self.client.post(reverse("execute_job", args=[job.pk]))
        self.assertEqual(resp.status_code, 302)
        job.refresh_from_db()
        self.assertEqual(job.send_state, "generated")
        self.assertEqual(job.email_subject, "Subj")
        self.assertIsNotNone(job.generated_at)

    @patch("jobs.views.generate_package", return_value={"ok": False, "error": "AI down"})
    def test_execute_error_sets_error_state(self, _gen):
        job = self._job(send_state="draft")
        self.client.post(reverse("execute_job", args=[job.pk]))
        job.refresh_from_db()
        self.assertEqual(job.send_state, "error")
        self.assertIn("AI down", job.send_error)

    @patch("jobs.views.generate_package")
    def test_execute_blocked_on_sent(self, mock_gen):
        job = self._job(send_state="sent")
        self.client.post(reverse("execute_job", args=[job.pk]))
        mock_gen.assert_not_called()

    def test_preview_get_shows_package(self):
        job = self._job()
        resp = self.client.get(reverse("preview_job", args=[job.pk]))
        self.assertContains(resp, "hr@acme.de")
        self.assertContains(resp, "B")

    @patch("jobs.views.regenerate_pdfs", return_value={"ok": True})
    def test_preview_post_saves_edits_and_regenerates(self, mock_regen):
        job = self._job(anschreiben_pdf_path="/tmp/a.pdf")
        resp = self.client.post(reverse("preview_job", args=[job.pk]), {
            "email_subject": "Nou", "email_body": "Body nou",
            "anschreiben_text": "Text nou",
        })
        self.assertEqual(resp.status_code, 302)
        job.refresh_from_db()
        self.assertEqual(job.email_subject, "Nou")
        self.assertEqual(job.anschreiben_text, "Text nou")
        mock_regen.assert_called_once()

    @patch("jobs.views.already_sent_in_sheet", return_value="nu")
    def test_transmit_get_shows_confirm(self, _chk):
        job = self._job()
        resp = self.client.get(reverse("transmit_job", args=[job.pk]))
        self.assertContains(resp, "LIVE")
        self.assertContains(resp, "hr@acme.de")

    @patch("jobs.views.send_package", return_value={"ok": True, "live": False})
    @patch("jobs.views.already_sent_in_sheet", return_value="nu")
    def test_transmit_dry_run_keeps_generated(self, _chk, mock_send):
        job = self._job()
        self.client.post(reverse("transmit_job", args=[job.pk]), {})  # fără live
        self.assertFalse(mock_send.call_args.kwargs["live"])
        job.refresh_from_db()
        self.assertEqual(job.send_state, "generated")

    @patch("jobs.views.mark_sent_in_sheet", return_value={"ok": True})
    @patch("jobs.views.send_package", return_value={"ok": True, "live": True})
    @patch("jobs.views.already_sent_in_sheet", return_value="nu")
    def test_transmit_live_marks_sent_and_sheet(self, _chk, mock_send, mock_mark):
        job = self._job()
        self.client.post(reverse("transmit_job", args=[job.pk]), {"live": "on"})
        self.assertTrue(mock_send.call_args.kwargs["live"])
        mock_mark.assert_called_once()
        job.refresh_from_db()
        self.assertEqual(job.send_state, "sent")
        self.assertIsNotNone(job.sent_at)

    @patch("jobs.views.send_package")
    @patch("jobs.views.already_sent_in_sheet", return_value="da")
    def test_transmit_live_blocked_when_already_sent(self, _chk, mock_send):
        job = self._job()
        self.client.post(reverse("transmit_job", args=[job.pk]), {"live": "on"})
        mock_send.assert_not_called()

    @patch("jobs.views.send_package",
           return_value={"ok": False, "live": True, "error": "rate limit"})
    @patch("jobs.views.already_sent_in_sheet", return_value="nu")
    def test_transmit_failure_sets_error(self, _chk, _send):
        job = self._job()
        self.client.post(reverse("transmit_job", args=[job.pk]), {"live": "on"})
        job.refresh_from_db()
        self.assertEqual(job.send_state, "error")
        self.assertIn("rate limit", job.send_error)

    @patch("jobs.views.send_package")
    def test_transmit_blocked_wrong_state(self, mock_send):
        job = self._job(send_state="draft")
        self.client.post(reverse("transmit_job", args=[job.pk]), {"live": "on"})
        mock_send.assert_not_called()

    def test_serve_doc_missing_404(self):
        job = self._job()  # fără anschreiben_pdf_path
        resp = self.client.get(reverse("serve_doc", args=[job.pk, "anschreiben"]))
        self.assertEqual(resp.status_code, 404)

    def test_serve_doc_serves_real_file(self):
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            f.write(b"%PDF-1.4 test")
        job = self._job(anschreiben_pdf_path=f.name)
        resp = self.client.get(reverse("serve_doc", args=[job.pk, "anschreiben"]))
        self.assertEqual(resp.status_code, 200)

    def test_serve_attachment_out_of_range_404(self):
        job = self._job(attachments_json=["/tmp/nu-exista.pdf"])
        resp = self.client.get(reverse("serve_attachment", args=[job.pk, 5]))
        self.assertEqual(resp.status_code, 404)

    def test_sent_list_filters(self):
        self._job(url="https://a.co/1", send_state="sent", title="Trimis")
        self._job(url="https://a.co/2", send_state="generated", title="Netrimis")
        resp = self.client.get(reverse("sent_list"))
        self.assertContains(resp, "Trimis")
        self.assertNotContains(resp, "Netrimis")


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

    def test_index_states_legend_present(self):
        Job.objects.create(url="https://a.co/1", title="Alpha", send_state="sent")
        resp = self.client.get(reverse("index"))
        self.assertContains(resp, "GESENDET")
