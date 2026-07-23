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
    # Starea locală a fluxului de trimitere prin Sheet:
    # draft → senden_pushed (SENDEN împins) → processed (dedus din pending)
    #                                       ↘ unconfirmed (niciodată văzut în pending).
    send_state = models.CharField(max_length=20, default="draft")
    sheet_pushed_at = models.DateTimeField(null=True, blank=True)
    # A apărut vreodată în pending după push? „Absent din pending" înseamnă
    # „procesat" DOAR dacă a fost văzut măcar o dată (altfel e blocat de
    # filtrul isSent din n8n sau șters din Sheet).
    seen_in_pending = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.title or '(fără titlu)'} @ {self.company or '?'}"
