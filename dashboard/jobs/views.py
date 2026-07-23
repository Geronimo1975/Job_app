"""Views dashboard: index, add (PRG), detail+edit, push/send în Sheet, refresh."""
import hashlib
import json

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.validators import URLValidator
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from .extractor import extract_job
from .forms import JobForm
from .models import Job
from .sheet_bridge import poll_mode, preview_sheet_row, push_to_sheet, refresh_states


def _send_blockers(job) -> list[str]:
    """Motive pentru care SENDEN nu are voie să plece pentru acest job."""
    blockers = []
    if job.send_state != "draft":
        blockers.append(
            f"Starea trimiterii e „{job.send_state}”, nu „draft” — un SENDEN nou ar rămâne "
            "armat în Sheet fără să fie consumat. Resetează întâi starea din pagina jobului."
        )
    if not (job.email_hr or "").strip():
        blockers.append(
            "Jobul nu are Kontakt Email — poll-ul n-ar avea destinatar, iar comanda "
            "ar rămâne blocată în pending la nesfârșit."
        )
    return blockers


def _row_hash(row: dict) -> str:
    """Amprenta rândului aprobat pe pagina de confirmare (GET) — verificată la POST."""
    payload = json.dumps(row, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


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
        job = Job.objects.filter(pk=pk).first()
        if job and job.send_state == "senden_pushed":
            # Ștergerea locală NU dezarmează SENDEN din Sheet — emailul ar
            # pleca oricum, fără nicio urmă locală.
            messages.error(
                request,
                "Rândul are SENDEN armat în Sheet — ștergerea locală NU anulează trimiterea. "
                "Golește întâi col P „Sende Befehl” în Sheet (sau așteaptă poll-ul), apoi șterge.",
            )
            return redirect("index")
        Job.objects.filter(pk=pk).delete()
        messages.info(request, "Rând șters.")
    return redirect("index")


def reset_job(request, pk):
    """Resetează starea locală la draft (nu atinge Sheet-ul — col P rămâne cum e)."""
    if request.method == "POST":
        job = get_object_or_404(Job, pk=pk)
        job.send_state = "draft"
        job.seen_in_pending = False
        job.save(update_fields=["send_state", "seen_in_pending"])
        messages.info(
            request,
            "Stare resetată la draft (doar local — dacă col P din Sheet mai are SENDEN, golește-o manual).",
        )
    return redirect("job_detail", pk=pk)


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
        blockers = _send_blockers(job)
        if blockers:
            for b in blockers:
                messages.error(request, b)
            return redirect("job_detail", pk=pk)
        row = preview_sheet_row(job)
        if request.POST.get("row_hash", "") != _row_hash(row):
            # Rândul s-a schimbat între confirmarea văzută și POST — reafișăm
            messages.warning(
                request, "Rândul s-a schimbat față de ce ai aprobat — reverifică și confirmă din nou."
            )
            return redirect("send_job", pk=pk)
        res = push_to_sheet(job, senden=True)
        if res["ok"]:
            job.send_state = "senden_pushed"
            job.seen_in_pending = False
            job.sheet_pushed_at = timezone.now()
            job.save(update_fields=["send_state", "seen_in_pending", "sheet_pushed_at"])
            messages.success(
                request,
                "SENDEN scris în Sheet — poll-ul trimite emailul la următorul tick.",
            )
            return redirect("index")
        messages.error(
            request,
            f"Trimitere eșuată: {res['error']} — dacă e o eroare de transport, comanda "
            "POATE fi ajuns totuși în Sheet: verifică col P „Sende Befehl” înainte de a reîncerca.",
        )
        return redirect("job_detail", pk=pk)
    row = preview_sheet_row(job)
    return render(request, "jobs/send_confirm.html", {
        "job": job,
        "row": row,
        "row_hash": _row_hash(row),
        "mode": poll_mode(),
        "blockers": _send_blockers(job),
    })


def refresh_states_view(request):
    """Actualizează stările din pending (deducție: absent = procesat de poll)."""
    if request.method == "POST":
        try:
            changed = refresh_states(Job.objects.all())
            messages.info(request, f"Stări actualizate: {changed} rând(uri) și-au schimbat starea.")
        except Exception as e:
            # Orice excepție → mesaj pe pagină, nu 500 („nimic silențios")
            messages.error(request, f"Refresh eșuat: {e}")
    return redirect("index")
