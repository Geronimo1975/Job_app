"""Views dashboard: index, add (PRG), detail+edit, push/send în Sheet, refresh."""
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.validators import URLValidator
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from .extractor import extract_job
from .forms import JobForm
from .models import Job
from .sheet_bridge import poll_mode, preview_sheet_row, push_to_sheet, refresh_states


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
    job = get_object_or_404(Job, pk=pk)
    if request.method == "POST":
        form = JobForm(request.POST, instance=job)
        if form.is_valid():
            form.save()
            messages.success(request, "Câmpuri salvate local.")
            return redirect("job_detail", pk=pk)
    else:
        form = JobForm(instance=job)
    return render(request, "jobs/detail.html", {"job": job, "form": form})


def delete_job(request, pk):
    if request.method == "POST":
        Job.objects.filter(pk=pk).delete()
        messages.info(request, "Rând șters.")
    return redirect("index")


def push_job(request, pk):
    """„Salvează în Sheet" — upsert-ul câmpurilor, FĂRĂ comandă de trimitere."""
    if request.method != "POST":
        return redirect("job_detail", pk=pk)
    job = get_object_or_404(Job, pk=pk)
    res = push_to_sheet(job, senden=False)
    if res["ok"]:
        messages.success(request, "Rând salvat în Sheet (upsert pe Apply URL).")
    else:
        messages.error(request, f"Salvare în Sheet eșuată: {res['error']}")
    return redirect("job_detail", pk=pk)


def send_job(request, pk):
    """„Trimite (SENDEN)" — GET: confirmare cu rândul exact; POST: push cu col P=SENDEN."""
    job = get_object_or_404(Job, pk=pk)
    if request.method == "POST":
        res = push_to_sheet(job, senden=True)
        if res["ok"]:
            job.send_state = "senden_pushed"
            job.sheet_pushed_at = timezone.now()
            job.save(update_fields=["send_state", "sheet_pushed_at"])
            messages.success(
                request,
                "SENDEN scris în Sheet — poll-ul trimite emailul la următorul tick.",
            )
            return redirect("index")
        messages.error(request, f"Trimitere eșuată: {res['error']}")
        return redirect("job_detail", pk=pk)
    return render(request, "jobs/send_confirm.html", {
        "job": job,
        "row": preview_sheet_row(job),
        "mode": poll_mode(),
    })


def refresh_states_view(request):
    """Actualizează stările din pending (deducție: absent = procesat de poll)."""
    if request.method == "POST":
        try:
            changed = refresh_states(Job.objects.all())
            messages.info(request, f"Stări actualizate: {changed} rând(uri) marcate procesate.")
        except RuntimeError as e:
            messages.error(request, f"Refresh eșuat: {e}")
    return redirect("index")
