"""
ATS CV Scanner — cât de bine rezonează CV-ul cu un anunț concret.

Strategia de bază a lui Jobscan (și OwlApply `ats-scanner`): compară CV-ul cu
anunțul și arată match %, keywords prezente și lipsă. Diferă de JOB-62: acolo
injectăm termeni ATS în Anschreiben; aici evaluăm DOCUMENTUL CV și semnalăm
golurile ÎNAINTE de a aplica, ca userul să decidă dacă ajustează CV-ul.

Vocabularul de potrivire = skill-urile userului din config (nu orice cuvânt din
anunț), reutilizând `skills_present` — o singură implementare de matching. Miezul
`scan_cv` e PUR (primește text), extragerea din PDF e separată și izolată.
"""

from __future__ import annotations

from pathlib import Path

from filters.relevance import skills_present


def scan_cv(cv_text: str, job_description: str, skills: list[str]) -> dict:
    """Compară CV-ul cu anunțul pe vocabularul de skill-uri al userului.

    Pași:
    - `wanted` = skill-urile cerute de anunț (prezente în job_description),
    - `present` = dintre acestea, cele care apar și în CV,
    - `missing` = cele cerute dar absente din CV,
    - `score` = procent present/wanted (100 dacă anunțul nu cere niciun skill
      cunoscut — nimic de dovedit, deci nu penalizăm).

    Întoarce {score, wanted, present, missing}. Pur: fără I/O.
    """
    wanted = skills_present(job_description or "", skills or [])
    if not wanted:
        return {"score": 100, "wanted": [], "present": [], "missing": []}
    present = skills_present(cv_text or "", wanted)
    present_set = set(present)
    missing = [s for s in wanted if s not in present_set]
    score = round(100 * len(present) / len(wanted))
    return {"score": score, "wanted": wanted, "present": present, "missing": missing}


def extract_pdf_text(path: str | Path) -> str:
    """Textul dintr-un PDF cu strat de text. "" dacă lipsește sau n-are text.

    CV-urile în uz au strat de text (regenerate din HTML, vezi CLAUDE.md); un CV
    care e doar imagine întoarce "" — scanner-ul va raporta onest 0 potriviri,
    nu va inventa. Izolat de `scan_cv` ca testele miezului să nu atingă discul.
    """
    p = Path(path)
    if not p.exists():
        return ""
    try:
        from pypdf import PdfReader
        reader = PdfReader(str(p))
        return "\n".join((page.extract_text() or "") for page in reader.pages)
    except Exception:  # noqa: BLE001 — PDF corupt/criptat → fără text, nu crash
        return ""
