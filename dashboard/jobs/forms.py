"""Formularul de completare a câmpurilor FIELD_MAP (editare în pagina de detaliu)."""
from django import forms

from scrapers.jobspy_scraper import SUPPORTED_SITES

from .models import Job

_JOB_TYPES = [("", "orice"), ("fulltime", "full-time"), ("parttime", "part-time"),
              ("contract", "contract"), ("internship", "internship")]


class JobForm(forms.ModelForm):
    class Meta:
        model = Job
        fields = [
            "title", "company", "location", "email_hr",
            "contact_person", "language", "match_score", "notizen",
        ]


class SearchForm(forms.Form):
    """Parametrii unei căutări JobSpy. Cheile din `cleaned_data` sunt exact
    cele așteptate de `search_bridge.query_from_params`."""

    preset = forms.CharField(required=False, widget=forms.HiddenInput)
    keywords = forms.CharField(
        widget=forms.Textarea(attrs={"rows": 4}),
        help_text="Un grup pe linie; cuvintele de pe aceeași linie se caută cu OR.",
    )
    location = forms.CharField(max_length=120)
    country = forms.CharField(max_length=60,
                              help_text="Enum de țară jobspy, ex. GERMANY.")
    language = forms.CharField(max_length=5, initial="de",
                               help_text="Comandă limba Anschreiben-ului.")
    sites = forms.MultipleChoiceField(
        choices=[(s, s) for s in SUPPORTED_SITES],
        widget=forms.CheckboxSelectMultiple,
    )
    remote = forms.BooleanField(required=False)
    # Gol = fără filtru de vechime.
    hours_old = forms.IntegerField(required=False, min_value=1, max_value=8760)
    # Plafon: peste ~200 rezultate/apel, jobspy petrece minute și ia 429.
    results_wanted = forms.IntegerField(min_value=1, max_value=200, initial=50)
    job_type = forms.ChoiceField(choices=_JOB_TYPES, required=False)
    distance = forms.IntegerField(required=False, min_value=1, max_value=200)
    google_search_term = forms.CharField(
        required=False, max_length=200,
        help_text="Sintaxa proprie Google Jobs, ex. „Python Developer jobs "
                  "near Berlin since yesterday”. Contează doar cu „google” bifat.",
    )
    linkedin_fetch_description = forms.BooleanField(
        required=False,
        help_text="Aduce descrierea completă de pe LinkedIn: un request per job, "
                  "deci mult mai lent.",
    )

    def clean_keywords(self):
        from .search_bridge import parse_keyword_groups
        value = self.cleaned_data["keywords"]
        if not parse_keyword_groups(value):
            raise forms.ValidationError("Scrie cel puțin un cuvânt-cheie.")
        return value
