"""Views dashboard: index, add (PRG), detail+edit, Execută/Preview/Transmite, Sheet."""
from pathlib import Path

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.validators import URLValidator
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from .extractor import extract_job
from .forms import JobForm
from .generation_bridge import (already_sent_in_sheet, generate_package,
                                regenerate_pdfs, send_package)
from .models import Job
from .sheet_bridge import mark_sent_in_sheet, push_to_sheet


def index(request):
    return render(request, "jobs/index.html",
                  {"jobs": Job.objects.all(), "section": "jobs"})


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
        messages.info(request, "Rând șters (doar local — Sheet-ul rămâne).")
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
    res = push_to_sheet(job)
    if res["ok"]:
        messages.success(request, "Rând salvat în Sheet (upsert pe Apply URL).")
    else:
        messages.error(request, f"Salvare în Sheet eșuată: {res['error']}")
    return redirect("job_detail", pk=pk)


def execute_job(request, pk):
    """„Execută" — generează pachetul (email + Anschreiben + PDF-uri). Sincron, ~10-30s."""
    if request.method != "POST":
        return redirect("index")
    job = get_object_or_404(Job, pk=pk)
    if job.send_state == "sent":
        messages.error(request, "Jobul e deja trimis — nu regenerez peste un pachet trimis.")
        return redirect("index")
    res = generate_package(job)
    if res["ok"]:
        job.email_subject = res["email_subject"]
        job.email_body = res["email_body"]
        job.anschreiben_text = res["anschreiben_text"]
        job.anschreiben_pdf_path = res["anschreiben_pdf_path"]
        job.bewerbung_pdf_path = res["bewerbung_pdf_path"]
        job.attachments_json = res["attachments"]
        job.send_state = "generated"
        job.generated_at = timezone.now()
        job.send_error = ""
        job.save()
        messages.success(request, "Pachet generat — verifică-l în Preview înainte de transmitere.")
        return redirect("preview_job", pk=pk)
    job.send_state = "error"
    job.send_error = res["error"]
    job.save(update_fields=["send_state", "send_error"])
    messages.error(request, f"Generare eșuată: {res['error']}")
    return redirect("index")


def _attachment_entries(job) -> list[dict]:
    """Atașamentele jobului, cu indexul pentru serve_attachment + starea pe disc."""
    return [
        {"idx": i, "name": Path(p).name, "exists": Path(p).exists()}
        for i, p in enumerate(job.attachments_json or [])
    ]


def preview_job(request, pk):
    """Preview editabil: email (subiect+body) și Anschreiben; PDF-urile se refac la salvare."""
    job = get_object_or_404(Job, pk=pk)
    if request.method == "POST":
        job.email_subject = request.POST.get("email_subject", job.email_subject)
        job.email_body = request.POST.get("email_body", job.email_body)
        new_text = request.POST.get("anschreiben_text", job.anschreiben_text)
        text_changed = new_text != job.anschreiben_text
        job.anschreiben_text = new_text
        job.save(update_fields=["email_subject", "email_body", "anschreiben_text"])
        if text_changed and job.anschreiben_pdf_path:
            res = regenerate_pdfs(job)
            if res["ok"]:
                messages.success(request, "Editări salvate; PDF-urile refăcute din textul editat.")
            else:
                messages.warning(request, f"Editări salvate, dar PDF-ul NU s-a refăcut: {res['error']}")
        else:
            messages.success(request, "Editări salvate.")
        return redirect("preview_job", pk=pk)
    return render(request, "jobs/preview.html", {
        "job": job, "attachments": _attachment_entries(job), "section": "jobs",
    })


def transmit_job(request, pk):
    """Confirmare + trimitere: dry-run implicit, LIVE doar cu comutatorul bifat."""
    job = get_object_or_404(Job, pk=pk)
    if request.method == "POST":
        if job.send_state != "generated":
            messages.error(request, f"Starea e „{job.send_state}” — doar pachetele „generated” se transmit.")
            return redirect("index")
        if not (job.email_hr or "").strip():
            messages.error(request, "Fără Kontakt Email — completează-l întâi în pagina jobului.")
            return redirect("job_detail", pk=pk)
        live = request.POST.get("live") == "on"
        if live and already_sent_in_sheet(job) == "da":
            messages.error(request, "Sheet-ul arată jobul DEJA trimis (done/sent/GESENDET) — LIVE blocat.")
            return redirect("transmit_job", pk=pk)
        res = send_package(job, live=live)
        if res["ok"] and live:
            job.send_state = "sent"
            job.sent_at = timezone.now()
            job.send_error = ""
            job.save(update_fields=["send_state", "sent_at", "send_error"])
            mark = mark_sent_in_sheet(job)
            if mark["ok"]:
                messages.success(request, "Email TRIMIS + Sheet marcat GESENDET.")
            else:
                messages.warning(request, f"Email TRIMIS, dar Sheet-ul NU s-a marcat ({mark['error']}) "
                                          "— completează manual col R „Email Status”=GESENDET.")
            return redirect("sent_list")
        if res["ok"]:
            messages.info(request, "Dry-run OK — nimic nu a fost trimis. Bifează LIVE când ești gata.")
            return redirect("transmit_job", pk=pk)
        job.send_state = "error"
        job.send_error = res["error"]
        job.save(update_fields=["send_state", "send_error"])
        messages.error(request, f"Trimitere eșuată: {res['error']}")
        return redirect("index")
    return render(request, "jobs/transmit_confirm.html", {
        "job": job, "attachments": _attachment_entries(job),
        "sheet_verdict": already_sent_in_sheet(job), "section": "jobs",
    })


def serve_doc(request, pk, kind):
    """Servește DOAR PDF-urile jobului (căi stocate pe rând, nu căi libere)."""
    job = get_object_or_404(Job, pk=pk)
    path = {"anschreiben": job.anschreiben_pdf_path,
            "bewerbung": job.bewerbung_pdf_path}.get(kind, "")
    if not path or not Path(path).exists():
        raise Http404
    return FileResponse(open(path, "rb"), content_type="application/pdf")


def serve_attachment(request, pk, idx):
    job = get_object_or_404(Job, pk=pk)
    paths = job.attachments_json or []
    if not (0 <= idx < len(paths)) or not Path(paths[idx]).exists():
        raise Http404
    return FileResponse(open(paths[idx], "rb"))


def sent_list(request):
    """Secțiunea „Trimise" din meniul lateral."""
    return render(request, "jobs/sent.html", {
        "jobs": Job.objects.filter(send_state="sent"), "section": "sent",
    })
