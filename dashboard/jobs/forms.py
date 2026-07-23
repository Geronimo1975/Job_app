"""Formularul de completare a câmpurilor FIELD_MAP (editare în pagina de detaliu)."""
from django import forms

from .models import Job


class JobForm(forms.ModelForm):
    class Meta:
        model = Job
        fields = [
            "title", "company", "location", "email_hr",
            "contact_person", "language", "match_score", "notizen",
        ]
