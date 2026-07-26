"""Teste dashboard jobs. Rulare: python dashboard/manage.py test jobs"""
import os
from unittest.mock import Mock, patch

from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from jobs import extractor
from jobs.models import Job, SearchResult, SearchRun


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
        # Rândul complet, nu doar statusul: pe fluxul implicit rândul poate să
        # NU existe încă în Sheet, iar un append cu 3 celule ar crea un rând ciot.
        self.assertEqual(payload["company"], "ACME")
        self.assertEqual(payload["title"], "Python Dev")

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
            job = self._job()
            res = generation_bridge.generate_package(job)
        self.assertTrue(res["ok"])
        # Anti-coliziune: fiecare job scrie în propriul subdirector
        out_dir = mock_cl.return_value.save_cover_letter.call_args.kwargs["output_dir"]
        self.assertIn(f"job-{job.pk}", str(out_dir))
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
        import tempfile
        from jobs import generation_bridge
        mock_smtp.return_value.send.return_value = True
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            f.write(b"%PDF")
        res = generation_bridge.send_package(
            self._job(email_subject="S", email_body="B", attachments_json=[f.name]), live=True)
        self.assertTrue(res["ok"])
        self.assertTrue(res["live"])
        self.assertFalse(mock_smtp.return_value.send.call_args.kwargs["dry_run"])

    @patch("jobs.generation_bridge.SmtpSender")
    @patch("jobs.generation_bridge.load_pipeline_config", return_value={})
    def test_send_package_live_blocked_without_attachments(self, _cfg, mock_smtp):
        # Un email LIVE fără niciun atașament e aproape sigur un pachet stricat
        from jobs import generation_bridge
        res = generation_bridge.send_package(
            self._job(email_subject="S", email_body="B", attachments_json=[]), live=True)
        self.assertFalse(res["ok"])
        self.assertIn("atașamente", res["error"])
        mock_smtp.return_value.send.assert_not_called()

    @patch("jobs.generation_bridge.SmtpSender")
    @patch("jobs.generation_bridge.load_pipeline_config", return_value={})
    def test_send_package_live_blocked_when_attachment_missing(self, _cfg, mock_smtp):
        # Atașament dispărut de pe disc → LIVE blocat, nu trimis silențios fără el
        from jobs import generation_bridge
        res = generation_bridge.send_package(
            self._job(email_subject="S", email_body="B",
                      attachments_json=["/tmp/disparut-xyz.pdf"]), live=True)
        self.assertFalse(res["ok"])
        mock_smtp.return_value.send.assert_not_called()

    @patch.dict(os.environ, {}, clear=True)
    @patch("jobs.generation_bridge.SmtpSender")
    @patch("jobs.generation_bridge.load_pipeline_config", return_value={
        "email": {"provider": "gmx", "providers": {"gmx": {"env_password_key": "GMX_PASSWORD"}}}})
    def test_send_package_live_blocked_without_smtp_password(self, _cfg, mock_smtp):
        # Fără parolă în env, SmtpSender ar cădea pe getpass → request blocat la infinit
        import tempfile
        from jobs import generation_bridge
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            f.write(b"%PDF")
        res = generation_bridge.send_package(
            self._job(email_subject="S", email_body="B", attachments_json=[f.name]), live=True)
        self.assertFalse(res["ok"])
        self.assertIn("GMX_PASSWORD", res["error"])
        mock_smtp.return_value.send.assert_not_called()

    @patch("jobs.generation_bridge.assemble_bewerbung")
    @patch("jobs.generation_bridge.text_to_pdf")
    @patch("jobs.generation_bridge.load_pipeline_config", return_value={
        "documents": {"cv_file": "documents/cv/inexistent-xyz.pdf"}})
    def test_regenerate_fails_when_komplett_cannot_rebuild(self, _cfg, _pdf, mock_asm):
        # CV lipsă → Komplett-ul ar rămâne cu textul VECHI pe disc; trebuie eroare, nu ok
        from jobs import generation_bridge
        job = self._job(anschreiben_text="Nou", anschreiben_pdf_path="/tmp/a.pdf",
                        bewerbung_pdf_path="/tmp/k.pdf")
        res = generation_bridge.regenerate_pdfs(job)
        self.assertFalse(res["ok"])
        self.assertIn("CV", res["error"])
        mock_asm.assert_not_called()

    @patch("jobs.generation_bridge.load_pipeline_config", return_value={})
    def test_send_package_without_email_fails(self, _cfg):
        from jobs import generation_bridge
        res = generation_bridge.send_package(self._job(email_hr=""), live=True)
        self.assertFalse(res["ok"])
        self.assertIn("Kontakt Email", res["error"])

    @patch("jobs.generation_bridge.SmtpSender")
    @patch("jobs.generation_bridge.load_pipeline_config", return_value={})
    def test_send_package_refused_is_error(self, _cfg, mock_smtp):
        import tempfile
        from jobs import generation_bridge
        mock_smtp.return_value.send.return_value = False
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            f.write(b"%PDF")
        res = generation_bridge.send_package(
            self._job(email_subject="S", email_body="B", attachments_json=[f.name]),
            live=True)
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

    @patch("jobs.views.send_package",
           return_value={"ok": False, "live": False, "error": "rate limit"})
    @patch("jobs.views.already_sent_in_sheet", return_value="nu")
    def test_transmit_dry_failure_keeps_generated(self, _chk, _send):
        # O SIMULARE eșuată nu are voie să distrugă pachetul revizuit (generated→error)
        job = self._job()
        self.client.post(reverse("transmit_job", args=[job.pk]), {})
        job.refresh_from_db()
        self.assertEqual(job.send_state, "generated")

    @patch("jobs.views.send_package")
    @patch("jobs.views.already_sent_in_sheet", return_value="nu")
    def test_transmit_live_blocked_while_sending(self, _chk, mock_send):
        # Anti-dublu-submit: revendicarea atomică refuză al doilea POST
        job = self._job(send_state="sending")
        self.client.post(reverse("transmit_job", args=[job.pk]), {"live": "on"})
        mock_send.assert_not_called()

    @patch("jobs.views.mark_sent_in_sheet", return_value={"ok": False, "error": "n8n jos"})
    @patch("jobs.views.send_package", return_value={"ok": True, "live": True})
    @patch("jobs.views.already_sent_in_sheet", return_value="nu")
    def test_transmit_live_sent_even_if_mark_fails(self, _chk, _send, _mark):
        # Emailul A PLECAT: starea locală trebuie să fie sent chiar dacă Sheet-ul nu s-a marcat
        job = self._job()
        self.client.post(reverse("transmit_job", args=[job.pk]), {"live": "on"})
        job.refresh_from_db()
        self.assertEqual(job.send_state, "sent")

    @patch("jobs.views.send_package", return_value={"ok": True, "live": True})
    @patch("jobs.views.mark_sent_in_sheet", return_value={"ok": True})
    @patch("jobs.views.already_sent_in_sheet", return_value="necunoscut (n8n jos)")
    def test_transmit_live_proceeds_with_warning_on_unknown_verdict(self, _chk, _mark, mock_send):
        # Fail-open advisory: verdict necunoscut NU blochează, dar trimiterea are loc conștient
        job = self._job()
        self.client.post(reverse("transmit_job", args=[job.pk]), {"live": "on"})
        mock_send.assert_called_once()

    def test_preview_post_blocked_on_sent(self):
        # Pachetul trimis e ARHIVĂ — editarea lui ar falsifica ce a plecat spre firmă
        job = self._job(send_state="sent", email_body="Original trimis")
        self.client.post(reverse("preview_job", args=[job.pk]), {
            "email_subject": "X", "email_body": "Falsificat", "anschreiben_text": "X",
        })
        job.refresh_from_db()
        self.assertEqual(job.email_body, "Original trimis")

    @patch("jobs.views.regenerate_pdfs", return_value={"ok": True})
    def test_preview_save_and_continue_redirects_to_transmit(self, _regen):
        # „Salvează și transmite" — editările nu se pierd pe drumul spre confirmare
        job = self._job()
        resp = self.client.post(reverse("preview_job", args=[job.pk]), {
            "email_subject": "S2", "email_body": "B2", "anschreiben_text": "A",
            "next": "transmit",
        })
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, reverse("transmit_job", args=[job.pk]))
        job.refresh_from_db()
        self.assertEqual(job.email_body, "B2")

    def test_recover_error_returns_to_generated(self):
        # Recuperare fără regenerare AI: editările umane se păstrează
        from django.utils import timezone as djtz
        job = self._job(send_state="error", send_error="SMTP jos",
                        generated_at=djtz.now())
        resp = self.client.post(reverse("recover_job", args=[job.pk]))
        self.assertEqual(resp.status_code, 302)
        job.refresh_from_db()
        self.assertEqual(job.send_state, "generated")

    def test_recover_blocked_without_package(self):
        job = self._job(send_state="error", generated_at=None)
        self.client.post(reverse("recover_job", args=[job.pk]))
        job.refresh_from_db()
        self.assertEqual(job.send_state, "error")

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


class SearchModelTests(TestCase):
    def _run(self, **over):
        data = dict(label="de · Python Developer", params_json={"preset": "de"},
                    status="running")
        data.update(over)
        return SearchRun.objects.create(**data)

    def test_run_defaults(self):
        run = self._run()
        self.assertEqual(run.found_count, 0)
        self.assertEqual(run.imported_count, 0)
        self.assertEqual(run.warnings_json, [])
        self.assertEqual(run.errors_json, [])
        self.assertIsNone(run.finished_at)
        self.assertIsNotNone(run.started_at)

    def test_runs_are_newest_first(self):
        first = self._run(label="prima")
        second = self._run(label="a doua")
        self.assertEqual(
            list(SearchRun.objects.values_list("pk", flat=True)),
            [second.pk, first.pk],
        )

    def test_duration_is_none_while_running(self):
        self.assertIsNone(self._run().duration_sec)

    def test_duration_counts_seconds_when_finished(self):
        from datetime import timedelta
        run = self._run()
        run.finished_at = run.started_at + timedelta(seconds=42)
        run.save()
        self.assertEqual(run.duration_sec, 42)

    def test_results_are_sorted_by_score_desc(self):
        run = self._run()
        SearchResult.objects.create(run=run, url="https://a.co/1", prescore=30)
        SearchResult.objects.create(run=run, url="https://a.co/2", prescore=80)
        self.assertEqual(
            list(run.results.values_list("prescore", flat=True)), [80, 30]
        )

    def test_same_url_twice_in_one_run_is_rejected(self):
        from django.db import IntegrityError
        run = self._run()
        SearchResult.objects.create(run=run, url="https://a.co/1")
        with self.assertRaises(IntegrityError):
            SearchResult.objects.create(run=run, url="https://a.co/1")

    def test_same_url_in_two_runs_is_allowed(self):
        """Fiecare rulare e un snapshot propriu — dublura se vede din badge."""
        SearchResult.objects.create(run=self._run(), url="https://a.co/1")
        SearchResult.objects.create(run=self._run(), url="https://a.co/1")
        self.assertEqual(SearchResult.objects.count(), 2)

    def test_deleting_run_deletes_its_results(self):
        run = self._run()
        SearchResult.objects.create(run=run, url="https://a.co/1")
        run.delete()
        self.assertEqual(SearchResult.objects.count(), 0)

    def test_deleting_imported_job_keeps_the_result(self):
        """Ștergerea unui Job nu trebuie să șteargă istoricul căutării."""
        job = Job.objects.create(url="https://a.co/1")
        res = SearchResult.objects.create(run=self._run(), url="https://a.co/1",
                                          imported_job=job)
        job.delete()
        res.refresh_from_db()
        self.assertIsNone(res.imported_job)


class SearchBridgeQueryTests(TestCase):
    def test_presets_come_from_settings_yaml(self):
        from jobs import search_bridge
        keys = [p["key"] for p in search_bridge.presets()]
        self.assertIn("de", keys)
        self.assertIn("ro_remote", keys)

    def test_preset_exposes_keywords_one_group_per_line(self):
        from jobs import search_bridge
        de = next(p for p in search_bridge.presets() if p["key"] == "de")
        self.assertIn("\n", de["keywords"])
        self.assertIn("Python Developer", de["keywords"])

    def test_query_from_params_parses_keyword_groups(self):
        from jobs import search_bridge
        q = search_bridge.query_from_params({
            "keywords": "Python Developer, Django Developer\nAI Engineer",
            "location": "Berlin", "country": "GERMANY", "language": "de",
            "sites": ["indeed"], "hours_old": 48, "results_wanted": 20,
        })
        self.assertEqual(q.keyword_groups,
                         [["Python Developer", "Django Developer"], ["AI Engineer"]])
        self.assertEqual(q.hours_old, 48)
        self.assertEqual(q.market_key, "adhoc")

    def test_label_summarises_the_search(self):
        from jobs import search_bridge
        label = search_bridge.label_for({
            "preset": "de", "keywords": "Python Developer\nAI Engineer",
            "location": "Berlin",
        })
        self.assertIn("Python Developer", label)
        self.assertIn("Berlin", label)


class SearchWorkerTests(TestCase):
    PARAMS = {
        "preset": "de", "keywords": "Python Developer", "location": "Berlin",
        "country": "GERMANY", "language": "de", "sites": ["indeed"],
        "hours_old": 72, "results_wanted": 10, "remote": False,
    }

    def _job(self, **over):
        job = {
            "url": "https://acme.de/jobs/1", "title": "Python Developer",
            "company": "ACME", "location": "Berlin", "platform": "indeed",
            "description": "Django and Python backend", "email_hr": "hr@acme.de",
            "date_posted": "2026-07-20", "language": "de",
            "salary_min": None, "salary_max": None, "raw": {"currency": "EUR"},
        }
        job.update(over)
        return job

    def _start(self, jobs, errors):
        """Pornește o rulare și execută worker-ul sincron (fără thread)."""
        from jobs import search_bridge
        from jobs.models import SearchRun
        run = SearchRun.objects.create(label="test", params_json=self.PARAMS)
        with patch.object(search_bridge.JobSpyScraper, "scrape_query",
                          return_value=(jobs, errors)):
            search_bridge.run_search_now(run.pk)
        run.refresh_from_db()
        return run

    def test_successful_run_saves_results(self):
        run = self._start([self._job()], [])
        self.assertEqual(run.status, "done")
        self.assertEqual(run.found_count, 1)
        self.assertIsNotNone(run.finished_at)
        res = run.results.get()
        self.assertEqual(res.title, "Python Developer")
        self.assertEqual(res.email_hr, "hr@acme.de")
        self.assertEqual(res.platform, "indeed")
        self.assertEqual(res.raw_json["currency"], "EUR")

    def test_prescore_and_reasons_are_stored(self):
        run = self._start([self._job()], [])
        res = run.results.get()
        self.assertGreater(res.prescore, 0)
        self.assertTrue(res.prescore_reasons_json)

    def test_warnings_and_errors_are_split_by_prefix(self):
        from scrapers.jobspy_scraper import WARNING_PREFIX
        run = self._start(
            [self._job()],
            [f"{WARNING_PREFIX} [de/indeed] am renunțat la hours_old",
             "[de/linkedin] Python Developer: 429"],
        )
        self.assertEqual(len(run.warnings_json), 1)
        self.assertEqual(len(run.errors_json), 1)
        self.assertIn("429", run.errors_json[0])

    def test_duplicate_urls_are_collapsed(self):
        run = self._start([self._job(), self._job()], [])
        self.assertEqual(run.found_count, 1)

    def test_no_results_with_errors_is_an_error_run(self):
        run = self._start([], ["[de/indeed] Python Developer: 500"])
        self.assertEqual(run.status, "error")

    def test_no_results_without_errors_is_a_done_run(self):
        run = self._start([], [])
        self.assertEqual(run.status, "done")
        self.assertEqual(run.found_count, 0)

    def test_exception_in_scraper_marks_run_as_error(self):
        from jobs import search_bridge
        from jobs.models import SearchRun
        run = SearchRun.objects.create(label="test", params_json=self.PARAMS)
        with patch.object(search_bridge.JobSpyScraper, "scrape_query",
                          side_effect=RuntimeError("jobspy a explodat")):
            search_bridge.run_search_now(run.pk)
        run.refresh_from_db()
        self.assertEqual(run.status, "error")
        self.assertIn("jobspy a explodat", run.errors_json[0])

    def test_seen_state_comes_from_the_index(self):
        from jobs import search_bridge
        from store.seen import APLICAT

        class FakeIndex:
            def decision_for(self, job):
                return APLICAT

        with patch.object(search_bridge, "_load_seen", return_value=FakeIndex()):
            run = self._start([self._job()], [])
        self.assertEqual(run.results.get().seen_state, APLICAT)


class SearchConcurrencyTests(TestCase):
    def test_second_search_is_refused_while_one_runs(self):
        from jobs import search_bridge
        from jobs.models import SearchRun
        SearchRun.objects.create(label="în curs", params_json={}, status="running")
        res = search_bridge.start_search(SearchWorkerTests.PARAMS)
        self.assertFalse(res["ok"])
        self.assertIn("în curs", res["error"])

    def test_stale_run_does_not_block_a_new_search(self):
        from datetime import timedelta
        from django.utils import timezone
        from jobs import search_bridge
        from jobs.models import SearchRun

        old = SearchRun.objects.create(label="mort", params_json={}, status="running")
        SearchRun.objects.filter(pk=old.pk).update(
            started_at=timezone.now() - timedelta(minutes=25))

        self.assertEqual(search_bridge.expire_stale_runs(), 1)
        old.refresh_from_db()
        self.assertEqual(old.status, "error")
        self.assertIn("întrerupt", old.errors_json[0])

    def test_start_search_expires_stale_runs_first(self):
        from datetime import timedelta
        from django.utils import timezone
        from jobs import search_bridge
        from jobs.models import SearchRun

        old = SearchRun.objects.create(label="mort", params_json={}, status="running")
        SearchRun.objects.filter(pk=old.pk).update(
            started_at=timezone.now() - timedelta(minutes=25))

        with patch.object(search_bridge.threading, "Thread") as thread:
            res = search_bridge.start_search(SearchWorkerTests.PARAMS)
        self.assertTrue(res["ok"])
        thread.assert_called_once()
