"""Modelul Job — oglindește schema canonică din scrapers/base.py::make_job()."""
from django.db import models


class Job(models.Model):
    url = models.URLField(max_length=500, unique=True)
    title = models.CharField(max_length=300, blank=True)
    company = models.CharField(max_length=200, blank=True)
    location = models.CharField(max_length=200, blank=True)
    description = models.TextField(blank=True)
    # CharField, nu EmailField: nu vrem validare pe output-ul AI (poate fi gol/imperfect).
    email_hr = models.CharField(max_length=254, blank=True)
    salary_min = models.IntegerField(null=True, blank=True)
    salary_max = models.IntegerField(null=True, blank=True)
    language = models.CharField(max_length=5, blank=True, default="")
    date_posted = models.CharField(max_length=40, blank=True)
    # Câmpuri FIELD_MAP completabile manual (Sheet: Kontakt Person / Notizen / Match Score)
    contact_person = models.CharField(max_length=200, blank=True)
    notizen = models.TextField(blank=True)
    match_score = models.IntegerField(null=True, blank=True)
    source = models.CharField(max_length=40, default="dashboard")
    # Rezultatul extragerii: "ok" / "partial" / "error".
    extraction_status = models.CharField(max_length=10, default="ok")
    # Extragerea AI brută + eventuale mesaje de eroare, pentru debug.
    raw_json = models.JSONField(default=dict, blank=True)
    # Fluxul Preview & Send: draft → generated (pachet complet) → sent (+ error).
    send_state = models.CharField(max_length=20, default="draft")
    sheet_pushed_at = models.DateTimeField(null=True, blank=True)
    # Pachetul generat („Execută"): editabil în Preview, trimis la „Transmite".
    email_subject = models.CharField(max_length=300, blank=True)
    email_body = models.TextField(blank=True)
    anschreiben_text = models.TextField(blank=True)
    anschreiben_pdf_path = models.CharField(max_length=500, blank=True)
    # Linkul Drive al Anschreiben-ului (JobApp Upload); intră în col O
    # „Anschreiben PDF" prin ingest — n8n cloud nu poate atașa fișiere locale.
    anschreiben_drive_link = models.URLField(max_length=500, blank=True)
    bewerbung_pdf_path = models.CharField(max_length=500, blank=True)
    attachments_json = models.JSONField(default=list, blank=True)
    generated_at = models.DateTimeField(null=True, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    send_error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.title or '(fără titlu)'} @ {self.company or '?'}"


class SearchRun(models.Model):
    """O căutare JobSpy = un snapshot. Rulează într-un thread de fundal."""

    RUNNING, DONE, ERROR = "running", "done", "error"

    label = models.CharField(max_length=200)
    # Parametrii exacți ai căutării, ca să poți repeta sau audita rularea.
    params_json = models.JSONField(default=dict)
    status = models.CharField(max_length=10, default=RUNNING)
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    # Rezultate salvate (nu se ascunde nimic sub prag) și câte au ajuns în Job.
    found_count = models.IntegerField(default=0)
    imported_count = models.IntegerField(default=0)
    # Avertismentele (filtre renunțate) sunt separate de erori: prima categorie
    # descrie o degradare, a doua un eșec de sursă.
    warnings_json = models.JSONField(default=list, blank=True)
    errors_json = models.JSONField(default=list, blank=True)

    class Meta:
        ordering = ["-started_at", "-pk"]

    def __str__(self):
        return f"{self.label} ({self.status})"

    @property
    def duration_sec(self) -> int | None:
        if not self.finished_at:
            return None
        return int((self.finished_at - self.started_at).total_seconds())


class SearchResult(models.Model):
    """Un rezultat de căutare, în staging — devine `Job` doar la import."""

    run = models.ForeignKey(SearchRun, related_name="results",
                            on_delete=models.CASCADE)
    url = models.URLField(max_length=500)
    title = models.CharField(max_length=300, blank=True)
    company = models.CharField(max_length=200, blank=True)
    location = models.CharField(max_length=200, blank=True)
    platform = models.CharField(max_length=40, blank=True)
    date_posted = models.CharField(max_length=40, blank=True)
    language = models.CharField(max_length=5, blank=True, default="")
    description = models.TextField(blank=True)
    email_hr = models.CharField(max_length=254, blank=True)
    salary_min = models.IntegerField(null=True, blank=True)
    salary_max = models.IntegerField(null=True, blank=True)
    # Scorul determinist din filters/relevance.py + motivele, ca să fie auditabil.
    prescore = models.IntegerField(default=0)
    prescore_reasons_json = models.JSONField(default=list, blank=True)
    # Decizia din store/seen.py: "" | aplicat | respins_prefiltru. Citit
    # read-only — dashboard-ul nu scrie în seen_jobs.json.
    seen_state = models.CharField(max_length=20, blank=True, default="")
    # SET_NULL: ștergerea unui Job nu trebuie să șteargă istoricul căutării.
    imported_job = models.ForeignKey("Job", null=True, blank=True,
                                     on_delete=models.SET_NULL,
                                     related_name="search_results")
    # Rândurile SĂRITE (jobul ținea deja un pachet) sunt totuși legate prin
    # `imported_job`, ca tabelul să ducă la jobul existent. Flagul separă
    # „legat" de „importat": fără el, badge-ul verde și `imported_count`
    # (care numără `imported_job__isnull=False`) ar raporta ca importat exact
    # ce mesajul declară sărit. Nu e derivabil din starea jobului la randare:
    # un job chiar importat ajunge `sent` pe calea normală și ar fi arătat
    # retroactiv ca „sărit".
    import_skipped = models.BooleanField(default=False)
    raw_json = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-prescore", "pk"]
        constraints = [
            models.UniqueConstraint(fields=["run", "url"], name="uniq_url_per_run"),
        ]

    def __str__(self):
        return f"{self.title or '(fără titlu)'} @ {self.company or '?'}"
