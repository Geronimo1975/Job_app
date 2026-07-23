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

    def test_job_to_internal_with_senden(self):
        from jobs import sheet_bridge
        internal = sheet_bridge.job_to_internal(self._job(), sende_befehl="SENDEN")
        self.assertEqual(internal["sende_befehl"], "SENDEN")

    def test_preview_sheet_row_has_german_headers(self):
        from jobs import sheet_bridge
        row = sheet_bridge.preview_sheet_row(self._job())
        self.assertEqual(row["Firma"], "ACME")
        self.assertEqual(row["Sende Befehl"], "SENDEN")
        self.assertEqual(row["Match Score"], 85)
        self.assertNotIn("Status", row)

    @patch.dict(os.environ, {}, clear=True)
    @patch("jobs.sheet_bridge.load_pipeline_config",
           return_value={"n8n": {"enabled": True, "secret_env": "N8N_WEBHOOK_SECRET"}})
    def test_push_without_secret_fails_without_http(self, _cfg):
        from jobs import sheet_bridge
        res = sheet_bridge.push_to_sheet(self._job(), senden=True)
        self.assertFalse(res["ok"])
        self.assertIn("N8N_WEBHOOK_SECRET", res["error"])

    @patch("jobs.sheet_bridge.load_pipeline_config", return_value={"n8n": {"enabled": False}})
    def test_push_with_n8n_disabled_fails(self, _cfg):
        from jobs import sheet_bridge
        res = sheet_bridge.push_to_sheet(self._job(), senden=False)
        self.assertFalse(res["ok"])
        self.assertIn("dezactivat", res["error"])

    @patch.dict(os.environ, {"N8N_WEBHOOK_SECRET": "s3cret"})
    @patch("jobs.sheet_bridge.N8nClient")
    @patch("jobs.sheet_bridge.load_pipeline_config",
           return_value={"n8n": {"enabled": True, "secret_env": "N8N_WEBHOOK_SECRET"}})
    def test_push_ok_calls_ingest_with_senden(self, _cfg, mock_client_cls):
        from jobs import sheet_bridge
        mock_client_cls.return_value.ingest.return_value = {"ok": True}
        res = sheet_bridge.push_to_sheet(self._job(), senden=True)
        self.assertTrue(res["ok"])
        sent_jobs = mock_client_cls.return_value.ingest.call_args[0][0]
        self.assertEqual(sent_jobs[0]["sende_befehl"], "SENDEN")

    @patch.dict(os.environ, {"N8N_WEBHOOK_SECRET": "s3cret"})
    @patch("jobs.sheet_bridge.N8nClient")
    @patch("jobs.sheet_bridge.load_pipeline_config",
           return_value={"n8n": {"enabled": True, "secret_env": "N8N_WEBHOOK_SECRET"}})
    def test_push_http_error_returns_error(self, _cfg, mock_client_cls):
        from jobs import sheet_bridge
        mock_client_cls.return_value.ingest.side_effect = RuntimeError("n8n 5xx")
        res = sheet_bridge.push_to_sheet(self._job(), senden=False)
        self.assertFalse(res["ok"])
        self.assertIn("n8n 5xx", res["error"])

    @patch.dict(os.environ, {"N8N_WEBHOOK_SECRET": "s3cret"})
    @patch("jobs.sheet_bridge.N8nClient")
    @patch("jobs.sheet_bridge.load_pipeline_config",
           return_value={"n8n": {"enabled": True, "secret_env": "N8N_WEBHOOK_SECRET"}})
    def test_refresh_processed_only_after_seen_in_pending(self, _cfg, mock_client_cls):
        # „absent din pending" ≠ „procesat": filtrul isSent din n8n exclude
        # rândurile deja trimise, care n-ar apărea NICIODATĂ în pending.
        from jobs import sheet_bridge
        still = self._job(url="https://a.co/still", send_state="senden_pushed")
        done = self._job(url="https://a.co/done", send_state="senden_pushed",
                         seen_in_pending=True)
        fresh = self._job(url="https://a.co/fresh", send_state="senden_pushed")
        draft = self._job(url="https://a.co/draft")  # send_state=draft
        mock_client_cls.return_value.get_pending.return_value = [
            {"Apply URL": "https://a.co/still "}  # spațiu → testează normalizarea
        ]
        changed = sheet_bridge.refresh_states(Job.objects.all())
        self.assertEqual(changed, 2)  # done→processed, fresh→unconfirmed
        still.refresh_from_db(); done.refresh_from_db()
        fresh.refresh_from_db(); draft.refresh_from_db()
        self.assertEqual(still.send_state, "senden_pushed")
        self.assertTrue(still.seen_in_pending)          # văzut acum
        self.assertEqual(done.send_state, "processed")  # văzut cândva, acum absent
        self.assertEqual(fresh.send_state, "unconfirmed")  # niciodată văzut
        self.assertEqual(draft.send_state, "draft")

    @patch("jobs.sheet_bridge.load_pipeline_config", return_value={"n8n": {"enabled": False}})
    def test_refresh_with_n8n_disabled_raises(self, _cfg):
        # Dezactivat → pending gol ar minți „processed"; trebuie eroare explicită
        from jobs import sheet_bridge
        self._job(send_state="senden_pushed")
        with self.assertRaises(RuntimeError):
            sheet_bridge.refresh_states(Job.objects.all())

    @patch("jobs.sheet_bridge.load_pipeline_config", side_effect=Exception("yaml corupt"))
    def test_refresh_with_broken_config_raises_runtimeerror(self, _cfg):
        # Config necititbil trebuie să devină RuntimeError (mesaj), nu 500
        from jobs import sheet_bridge
        with self.assertRaises(RuntimeError):
            sheet_bridge.refresh_states(Job.objects.all())

    def test_push_blank_url_fails_without_http(self):
        from jobs import sheet_bridge
        job = self._job(url="   ")
        res = sheet_bridge.push_to_sheet(job, senden=True)
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

    def test_poll_mode_checks_installed_plist_first(self):
        # Job-ul launchd EFECTIV e o copie în ~/Library/LaunchAgents — dacă
        # oricare plist conține --live, bannerul trebuie să spună LIVE.
        import tempfile
        from pathlib import Path as P
        from jobs import sheet_bridge
        with tempfile.TemporaryDirectory() as td:
            installed = P(td) / "installed.plist"
            repo = P(td) / "repo.plist"
            with patch.object(sheet_bridge, "_POLL_PLIST_CANDIDATES", [installed, repo]):
                # niciun plist → necunoscut
                self.assertIn("necunoscut", sheet_bridge.poll_mode())
                # doar repo, fără --live → dry-run
                repo.write_text("<string>poll</string>", encoding="utf-8")
                self.assertEqual(sheet_bridge.poll_mode(), "dry-run")
                # instalatul are --live (repo nu) → LIVE
                installed.write_text("<string>--live</string>", encoding="utf-8")
                self.assertEqual(sheet_bridge.poll_mode(), "LIVE")


class SheetViewTests(TestCase):
    def _job(self, **kw):
        base = dict(url="https://acme.de/jobs/1", title="Python Dev", company="ACME",
                    email_hr="hr@acme.de")
        base.update(kw)
        return Job.objects.create(**base)

    def _confirm_hash(self, job):
        """Ia row_hash-ul din pagina de confirmare (fluxul real GET→POST)."""
        resp = self.client.get(reverse("send_job", args=[job.pk]))
        return resp.context["row_hash"]

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
        self.assertFalse(mock_push.call_args.kwargs["senden"])
        job.refresh_from_db()
        self.assertEqual(job.send_state, "draft")

    @patch("jobs.views.poll_mode", return_value="dry-run")
    def test_send_job_get_shows_confirmation(self, _mode):
        job = self._job()
        resp = self.client.get(reverse("send_job", args=[job.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Firma")       # rândul cu chei germane
        self.assertContains(resp, "SENDEN")
        self.assertContains(resp, "dry-run")     # modul poll vizibil
        self.assertContains(resp, 'name="row_hash"')  # amprenta rândului aprobat

    @patch("jobs.views.poll_mode", return_value="LIVE")
    def test_send_job_get_live_banner(self, _mode):
        # Bannerul LIVE e singura semnalizare înaintea unui email REAL
        job = self._job()
        resp = self.client.get(reverse("send_job", args=[job.pk]))
        self.assertContains(resp, "LIVE")
        self.assertContains(resp, "REAL")

    @patch("jobs.views.poll_mode", return_value="dry-run")
    def test_send_job_get_blocked_without_email(self, _mode):
        # Fără Kontakt Email comanda nu poate fi consumată → fără formular de confirmare
        job = self._job(email_hr="")
        resp = self.client.get(reverse("send_job", args=[job.pk]))
        self.assertContains(resp, "Kontakt Email")
        self.assertNotContains(resp, 'name="row_hash"')

    @patch("jobs.views.poll_mode", return_value="dry-run")
    def test_send_job_get_blocked_when_not_draft(self, _mode):
        job = self._job(send_state="senden_pushed")
        resp = self.client.get(reverse("send_job", args=[job.pk]))
        self.assertNotContains(resp, 'name="row_hash"')

    @patch("jobs.views.push_to_sheet", return_value={"ok": True})
    def test_send_job_post_pushes_and_marks_state(self, mock_push):
        job = self._job()
        with patch("jobs.views.poll_mode", return_value="dry-run"):
            row_hash = self._confirm_hash(job)
        resp = self.client.post(reverse("send_job", args=[job.pk]), {"row_hash": row_hash})
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(mock_push.call_args.kwargs["senden"])
        job.refresh_from_db()
        self.assertEqual(job.send_state, "senden_pushed")
        self.assertIsNotNone(job.sheet_pushed_at)

    @patch("jobs.views.push_to_sheet", return_value={"ok": True})
    def test_send_job_post_blocked_without_email(self, mock_push):
        job = self._job(email_hr="")
        self.client.post(reverse("send_job", args=[job.pk]), {"row_hash": "x"})
        mock_push.assert_not_called()
        job.refresh_from_db()
        self.assertEqual(job.send_state, "draft")

    @patch("jobs.views.push_to_sheet", return_value={"ok": True})
    def test_send_job_post_blocked_when_not_draft(self, mock_push):
        # Re-send pe un rând deja împins/trimis ar arma SENDEN orfan în Sheet
        job = self._job(send_state="processed")
        self.client.post(reverse("send_job", args=[job.pk]), {"row_hash": "x"})
        mock_push.assert_not_called()
        job.refresh_from_db()
        self.assertEqual(job.send_state, "processed")

    @patch("jobs.views.push_to_sheet", return_value={"ok": True})
    def test_send_job_post_blocked_on_stale_hash(self, mock_push):
        # Rândul s-a schimbat între confirmarea văzută și POST → nu trimite
        job = self._job()
        with patch("jobs.views.poll_mode", return_value="dry-run"):
            row_hash = self._confirm_hash(job)
        job.title = "ALT TITLU"
        job.save()
        resp = self.client.post(reverse("send_job", args=[job.pk]), {"row_hash": row_hash})
        mock_push.assert_not_called()
        self.assertEqual(resp.status_code, 302)  # înapoi la confirmare

    @patch("jobs.views.push_to_sheet", return_value={"ok": False, "error": "n8n 5xx"})
    def test_send_job_post_error_keeps_state(self, _push):
        job = self._job()
        with patch("jobs.views.poll_mode", return_value="dry-run"):
            row_hash = self._confirm_hash(job)
        self.client.post(reverse("send_job", args=[job.pk]), {"row_hash": row_hash})
        job.refresh_from_db()
        self.assertEqual(job.send_state, "draft")
        self.assertIsNone(job.sheet_pushed_at)

    @patch("jobs.views.push_to_sheet", return_value={"ok": True})
    def test_push_job_get_does_not_push(self, mock_push):
        # Singura gardă între un GET și o scriere în Sheet-ul de producție
        job = self._job()
        resp = self.client.get(reverse("push_job", args=[job.pk]))
        self.assertEqual(resp.status_code, 302)
        mock_push.assert_not_called()

    def test_delete_blocked_when_senden_armed(self):
        # Ștergerea locală NU dezarmează SENDEN din Sheet — trebuie blocată
        job = self._job(send_state="senden_pushed")
        resp = self.client.post(reverse("delete_job", args=[job.pk]), follow=True)
        self.assertEqual(Job.objects.count(), 1)
        self.assertContains(resp, "SENDEN")

    def test_reset_job_returns_to_draft(self):
        job = self._job(send_state="processed", seen_in_pending=True)
        resp = self.client.post(reverse("reset_job", args=[job.pk]))
        self.assertEqual(resp.status_code, 302)
        job.refresh_from_db()
        self.assertEqual(job.send_state, "draft")
        self.assertFalse(job.seen_in_pending)

    @patch("jobs.views.refresh_states", return_value=2)
    def test_refresh_view_reports_count(self, mock_refresh):
        resp = self.client.post(reverse("refresh_states"), follow=True)
        self.assertContains(resp, "2")

    @patch("jobs.views.refresh_states", side_effect=RuntimeError("n8n dezactivat"))
    def test_refresh_view_surfaces_error(self, _refresh):
        resp = self.client.post(reverse("refresh_states"), follow=True)
        self.assertContains(resp, "n8n dezactivat")

    @patch("jobs.views.refresh_states", side_effect=ValueError("crash neașteptat"))
    def test_refresh_view_catches_any_exception(self, _refresh):
        # Orice excepție → mesaj pe pagină, nu 500 („nimic silențios")
        resp = self.client.post(reverse("refresh_states"), follow=True)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "crash neașteptat")


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

    def test_index_states_legend_is_honest(self):
        # „processed" e o deducție — legenda trebuie să trimită la Sheet
        Job.objects.create(url="https://a.co/1", title="Alpha", send_state="processed")
        resp = self.client.get(reverse("index"))
        self.assertContains(resp, "Email Status")
