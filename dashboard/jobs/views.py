"""Views dashboard: index (formular + tabel), add (PRG), detail, delete."""
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.validators import URLValidator
from django.shortcuts import get_object_or_404, redirect, render

from .extractor import extract_job
from .models import Job


def index(request):
    return render(request, "jobs/index.html", {"jobs": Job.objects.all()})


def add_job(request):
    if request.method != "POST":
        return redirect("index")

    url = (request.POST.get("url") or "").strip()
    try:
        URLValidator()(url)
    except ValidationError:
        messages.error(request, "URL invalid.")
        return redirect("index")

    data = extract_job(url)
    status = data["extraction_status"]

    # Extragere eșuată pe un URL deja salvat: NU suprascrie datele bune cu goluri
    # (o eroare tranzitorie de rețea nu trebuie să șteargă un rând extras corect).
    if status != "ok" and Job.objects.filter(url=url).exists():
        err = (data.get("raw_json") or {}).get("error", "AI n-a întors JSON valid")
        messages.error(request, f"Extragere eșuată ({err}) — rândul existent a fost păstrat.")
        return redirect("index")

    defaults = {k: v for k, v in data.items() if k != "url"}
    job, created = Job.objects.update_or_create(url=url, defaults=defaults)

    if status == "ok":
        verb = "Adăugat" if created else "Actualizat"
        messages.success(request, f"{verb}: {job.title or url}")
    elif status == "partial":
        messages.warning(request, "Extragere parțială — AI n-a întors JSON valid. Verifică manual.")
    else:
        err = (data.get("raw_json") or {}).get("error", "eroare necunoscută")
        messages.error(request, f"Extragere eșuată: {err}")
    return redirect("index")


def job_detail(request, pk):
    return render(request, "jobs/detail.html", {"job": get_object_or_404(Job, pk=pk)})


def delete_job(request, pk):
    if request.method == "POST":
        Job.objects.filter(pk=pk).delete()
        messages.info(request, "Rând șters.")
    return redirect("index")
