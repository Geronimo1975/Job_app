"""Views dashboard: index, add (PRG), detail+edit, Execută/Preview/Transmite, Sheet."""
from pathlib import Path

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.validators import URLValidator
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from . import search_bridge
from .extractor import extract_job
from .forms import JobForm, SearchForm
from .generation_bridge import (already_sent_in_sheet, generate_package,
                                regenerate_pdfs, send_package)
from .models import Job, SearchRun
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
    # „Insider connections" (Jobright), etic: link de căutare, fără scraping.
    from outreach import linkedin_people_search_url
    linkedin_search = linkedin_people_search_url(job.company)
    return render(
        request,
        "jobs/detail.html",
        {"job": job, "form": form, "linkedin_search": linkedin_search},
    )


def interview_prep(request, pk):
    """Foaie de pregătire de interviu, generată determinist din anunț + skills."""
    job = get_object_or_404(Job, pk=pk)
    from agents.base_agent import load_config
    from generators.interview_prep_gen import generate_prep

    cfg = load_config()
    skills_cfg = cfg.get("skills", {}) or {}
    skills = (skills_cfg.get("primary", []) or []) + (skills_cfg.get("secondary", []) or [])
    prep = generate_prep(
        job.description or "", job.title or "", job.company or "",
        skills, job.language or "de",
    )
    return render(request, "jobs/interview_prep.html", {"job": job, "prep": prep})


def ats_scan(request, pk):
    """Scor ATS: cât de bine rezonează CV-ul (pe limba jobului) cu anunțul."""
    job = get_object_or_404(Job, pk=pk)
    from agents.base_agent import load_config, cv_path_for, job_language
    from ats_scanner import scan_cv, extract_pdf_text

    cfg = load_config()
    skills_cfg = cfg.get("skills", {}) or {}
    skills = (skills_cfg.get("primary", []) or []) + (skills_cfg.get("secondary", []) or [])
    lang = job.language or job_language(job)
    cv_path = cv_path_for(cfg, lang)
    cv_text = extract_pdf_text(cv_path)
    result = scan_cv(cv_text, job.description or "", skills)
    result["cv_found"] = bool(cv_text)
    result["cv_name"] = Path(cv_path).name
    return render(request, "jobs/ats_scan.html", {"job": job, "ats": result})


def delete_job(request, pk):
    if request.method == "POST":
        Job.objects.filter(pk=pk).delete()
        messages.info(request, "Rând șters (doar local — Sheet-ul rămâne).")
    return redirect("index")


def push_job(request, pk):
    """„Salvează în Sheet" — upsert-ul câmpurilor, FĂRĂ comandă de trimitere."""
    if request.method != "POST":
        return redirect("job_detail", pk=pk)
    job = get_object_or_404(Job, pk=pk)
    res = push_to_sheet(job)
    if res["ok"] and res.get("warning"):
        messages.warning(request, res["warning"])
    elif res["ok"]:
        extra = " Anschreiben PDF e în Drive (col O)." if res.get("link") else ""
        messages.success(request, f"Rând salvat în Sheet (upsert pe Apply URL).{extra}")
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
        if job.send_state == "sent":
            # Pachetul trimis e ARHIVA a ceea ce a plecat — nu se rescrie.
            messages.error(request, "Pachetul e deja trimis — arhiva nu se editează.")
            return redirect("preview_job", pk=pk)
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
        # „Salvează și transmite" — editările ajung garantat în pachet înainte de confirmare
        if request.POST.get("next") == "transmit":
            return redirect("transmit_job", pk=pk)
        return redirect("preview_job", pk=pk)
    return render(request, "jobs/preview.html", {
        "job": job, "attachments": _attachment_entries(job), "section": "jobs",
    })


def transmit_job(request, pk):
    """Confirmare + trimitere: dry-run implicit, LIVE doar cu comutatorul bifat."""
    job = get_object_or_404(Job, pk=pk)
    if request.method == "POST":
        if not (job.email_hr or "").strip():
            messages.error(request, "Fără Kontakt Email — completează-l întâi în pagina jobului.")
            return redirect("job_detail", pk=pk)
        live = request.POST.get("live") == "on"

        if not live:
            # SIMULARE: nu revendică starea și nu o distruge la eșec.
            if job.send_state != "generated":
                messages.error(request, f"Starea e „{job.send_state}” — doar pachetele „generated” se transmit.")
                return redirect("index")
            res = send_package(job, live=False)
            if res["ok"]:
                messages.info(request, "Dry-run OK — nimic nu a fost trimis. Bifează LIVE când ești gata.")
            else:
                job.send_error = res["error"]
                job.save(update_fields=["send_error"])
                messages.error(request, f"Dry-run eșuat: {res['error']} — pachetul rămâne intact (generated).")
            return redirect("transmit_job", pk=pk)

        # LIVE: garda Sheet (fail-open advisory, dar niciodată silențios)
        verdict = already_sent_in_sheet(job)
        if verdict == "da":
            messages.error(request, "Sheet-ul arată jobul DEJA trimis (done/sent/GESENDET) — LIVE blocat.")
            return redirect("transmit_job", pk=pk)
        if verdict.startswith("necunoscut"):
            messages.warning(request, f"Verificarea Sheet-ului indisponibilă ({verdict}) — trimit totuși, asigură-te că nu retrimiți.")

        # Revendicare ATOMICĂ anti-dublu-submit: trimiterea durează ~35s
        # (SMTP + delay-ul dintre emailuri), fereastră în care un al doilea
        # POST ar trece de o simplă verificare a stării.
        claimed = Job.objects.filter(pk=pk, send_state="generated").update(send_state="sending")
        if claimed != 1:
            job.refresh_from_db()
            messages.error(request, f"Trimitere neîncepută: starea e „{job.send_state}” (posibil o trimitere deja în curs).")
            return redirect("index")
        job.refresh_from_db()

        res = send_package(job, live=True)
        if res["ok"]:
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


def recover_job(request, pk):
    """error → generated: reia pachetul editat FĂRĂ regenerare AI (editările rămân)."""
    if request.method == "POST":
        job = get_object_or_404(Job, pk=pk)
        if job.send_state == "error" and job.generated_at:
            job.send_state = "generated"
            job.save(update_fields=["send_state"])
            messages.info(request, "Pachet reactivat — poți transmite din nou (editările păstrate).")
        else:
            messages.error(request, "Reactivarea e doar pentru stări „error” cu pachet generat.")
    return redirect("index")


def sent_list(request):
    """Secțiunea „Trimise" din meniul lateral."""
    return render(request, "jobs/sent.html", {
        "jobs": Job.objects.filter(send_state="sent"), "section": "sent",
    })


def search_index(request):
    """Secțiunea „Căutare": formular (preset + override) și istoricul rulărilor."""
    presets = search_bridge.presets()
    selected_preset_key = ""

    if request.method == "POST":
        form = SearchForm(request.POST)
        selected_preset_key = request.POST.get("preset", "")
        if form.is_valid():
            res = search_bridge.start_search(form.cleaned_data)
            if res["ok"]:
                messages.info(request, "Căutare pornită — pagina se "
                                       "reîmprospătează până se termină.")
                return redirect("search_run", pk=res["run"].pk)
            messages.error(request, res["error"])
            if res["run"]:
                return redirect("search_run", pk=res["run"].pk)
        else:
            messages.error(request, "Formular invalid — verifică câmpurile marcate.")
    else:
        search_bridge.expire_stale_runs()
        # Presetul cerut prin `?preset=` (butonul „Încarcă preset" fără JS,
        # sau un link direct) — cu fallback pe primul preset dacă lipsește
        # sau nu se potrivește niciunul (query string necunoscut/vechi).
        requested_key = request.GET.get("preset")
        selected = next((p for p in presets if p["key"] == requested_key), None)
        if selected is None:
            selected = presets[0] if presets else None
        selected_preset_key = selected["key"] if selected else ""
        # Presetul (cerut sau implicit) vine randat din server, ca formularul
        # să funcționeze și fără JavaScript.
        form = SearchForm(initial=selected)

    return render(request, "jobs/cauta.html", {
        "form": form,
        "presets": presets,
        "selected_preset_key": selected_preset_key,
        "runs": SearchRun.objects.all()[:20],
        "section": "search",
    })


def search_run(request, pk):
    """Starea unei rulări + rezultatele, cu import în bloc."""
    run = get_object_or_404(SearchRun, pk=pk)
    if run.status == SearchRun.RUNNING:
        search_bridge.expire_stale_runs()
        run.refresh_from_db()

    order = "-prescore" if request.GET.get("sort") != "data" else "-date_posted"
    return render(request, "jobs/cauta_run.html", {
        "run": run,
        "results": run.results.order_by(order, "pk"),
        "sort": request.GET.get("sort", "scor"),
        "is_running": run.status == SearchRun.RUNNING,
        "section": "search",
    })


def import_search_results(request, pk):
    """„Importă selectate" — rezultatele bifate devin rânduri Job (draft)."""
    if request.method != "POST":
        return redirect("search_run", pk=pk)
    run = get_object_or_404(SearchRun, pk=pk)

    # Filtrăm pe rulare: un id trimis manual nu trebuie să importe din altă listă.
    ids = [int(i) for i in request.POST.getlist("result_ids") if str(i).isdigit()]
    ids = list(run.results.filter(pk__in=ids).values_list("pk", flat=True))

    report = search_bridge.import_results(ids)
    # ABATERE de la brief: cu `errors` în joc (Task 7), un import cu reușite
    # ȘI eșecuri nu trebuie să se afișeze verde ca succes deplin — devine
    # warning chiar dacă `created`/`updated` sunt nenule.
    if report["errors"]:
        messages.warning(request, report["message"])
    elif report["created"] or report["updated"]:
        messages.success(request, report["message"])
    elif report["skipped"]:
        messages.warning(request, report["message"])
    else:
        messages.error(request, report["message"])
    return redirect("search_run", pk=pk)
