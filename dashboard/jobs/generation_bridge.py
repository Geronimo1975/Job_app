"""Puntea de generare + trimitere: refolosește generators/ + sender/ (read-only).

„Execută" produce pachetul (email + Anschreiben + PDF-uri + atașamente),
„Transmite" îl trimite prin SmtpSender (rate-limit pe disc, partajat cu poll-ul).
Nicio funcție nu ridică excepții spre views — eșecul e codat în {ok, error}.
"""
from pathlib import Path

from generators.cover_letter_gen import CoverLetterGenerator
from generators.email_body_gen import EmailBodyGenerator
from generators.pdf_gen import assemble_bewerbung, text_to_pdf
from sender.smtp_sender import SmtpSender

from .sheet_bridge import _client_or_error, load_pipeline_config

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _docs_paths(cfg: dict) -> tuple[Path, list[Path]]:
    """CV-ul + certificatele, cu aceleași chei de config ca EmailAgent."""
    docs = cfg.get("documents", {}) or {}
    cv = _REPO_ROOT / docs.get("cv_file", "documents/cv/Resume George Sebastian Cucuiet.pdf")
    certs_dir = _REPO_ROOT / docs.get("certificates_dir", "documents/certificates")
    certs = sorted(certs_dir.glob("*.pdf")) if certs_dir.exists() else []
    return cv, certs


def _collect_attachments(bewerbung_pdf: str, anschreiben_pdf: str, cfg: dict) -> list[str]:
    """Prioritatea din EmailAgent: Komplett singur; altfel Anschreiben+CV+certificate."""
    if bewerbung_pdf and Path(bewerbung_pdf).exists():
        return [bewerbung_pdf]
    out: list[str] = []
    if anschreiben_pdf and Path(anschreiben_pdf).exists():
        out.append(anschreiben_pdf)
    cv, certs = _docs_paths(cfg)
    if cv.exists():
        out.append(str(cv))
    out.extend(str(p) for p in certs)
    return out


def generate_package(job) -> dict:
    """Generează emailul + Anschreiben + PDF-urile pentru un job."""
    try:
        cfg = load_pipeline_config()
    except Exception as e:
        return {"ok": False, "error": f"config necititbil: {e}"}
    company = job.company or "Unknown"
    position = job.title or "Unknown"
    language = job.language or "de"
    try:
        gen = CoverLetterGenerator(cfg, _REPO_ROOT)
        text = gen.generate(
            company=company, position=position,
            job_description=job.description or "",
            location=job.location or "", language=language,
        )
        txt_path = Path(gen.save_cover_letter(text, company, position))
    except Exception as e:
        return {"ok": False, "error": f"generare Anschreiben eșuată: {e}"}

    anschreiben_pdf = ""
    bewerbung_pdf = ""
    try:
        pdf_path = txt_path.with_suffix(".pdf")
        text_to_pdf(text, pdf_path)
        anschreiben_pdf = str(pdf_path)
        cv, certs = _docs_paths(cfg)
        if cv.exists():
            komplett = pdf_path.with_name(
                pdf_path.stem.replace("Anschreiben_", "Bewerbung_Komplett_") + ".pdf"
            )
            assemble_bewerbung(text, cv, komplett, certificates=certs)
            bewerbung_pdf = str(komplett)
    except Exception as e:
        # PDF-ul e best-effort (convenția CoverLetterAgent) — textul rămâne valid
        print(f"  ⚠ PDF eșuat: {e}")

    try:
        body = EmailBodyGenerator(cfg, _REPO_ROOT).generate(
            company=company, position=position,
            contact_person=job.contact_person or "", language=language,
        )
    except Exception as e:
        return {"ok": False, "error": f"generare body email eșuată: {e}"}

    applicant = (cfg.get("applicant") or {}).get("name", "")
    return {
        "ok": True,
        "email_subject": f"Bewerbung als {position} — {applicant}",
        "email_body": body,
        "anschreiben_text": text,
        "anschreiben_pdf_path": anschreiben_pdf,
        "bewerbung_pdf_path": bewerbung_pdf,
        "attachments": _collect_attachments(bewerbung_pdf, anschreiben_pdf, cfg),
    }


def regenerate_pdfs(job) -> dict:
    """După editarea Anschreiben-ului în Preview: PDF-urile reflectă textul editat."""
    if not job.anschreiben_pdf_path:
        return {"ok": False, "error": "niciun PDF generat — rulează Execută întâi."}
    try:
        cfg = load_pipeline_config()
        pdf_path = Path(job.anschreiben_pdf_path)
        text_to_pdf(job.anschreiben_text, pdf_path)
        if job.bewerbung_pdf_path:
            cv, certs = _docs_paths(cfg)
            if cv.exists():
                assemble_bewerbung(job.anschreiben_text, cv,
                                   Path(job.bewerbung_pdf_path), certificates=certs)
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": f"regenerare PDF eșuată: {e}"}


def send_package(job, live: bool, provider: str | None = None) -> dict:
    """Trimite pachetul prin SmtpSender. live=False → dry-run (nimic nu pleacă)."""
    to_email = (job.email_hr or "").strip()
    if not to_email:
        return {"ok": False, "live": live, "error": "jobul nu are Kontakt Email."}
    try:
        cfg = load_pipeline_config()
    except Exception as e:
        return {"ok": False, "live": live, "error": f"config necititbil: {e}"}
    attachments = [Path(p) for p in (job.attachments_json or []) if Path(p).exists()]
    try:
        smtp = SmtpSender(cfg, _REPO_ROOT)
        msg = smtp.build_email(
            to_email=to_email, subject=job.email_subject,
            body=job.email_body, attachments=attachments,
        )
        ok = smtp.send(msg, provider=provider, dry_run=not live)
    except Exception as e:
        return {"ok": False, "live": live, "error": str(e)}
    if not ok:
        return {"ok": False, "live": live,
                "error": "SMTP a refuzat trimiterea (rate limit zilnic sau eroare — vezi log)."}
    return {"ok": True, "live": live}


def already_sent_in_sheet(job) -> str:
    """Gardă advisory pre-LIVE: e URL-ul deja done/sent/GESENDET în Sheet?"""
    try:
        cfg = load_pipeline_config()
        client, err = _client_or_error(cfg)
        if client is None:
            return f"necunoscut ({err})"
        known = set(client.get_known_urls())
        return "da" if job.url.strip() in known else "nu"
    except Exception as e:
        return f"necunoscut ({e})"
