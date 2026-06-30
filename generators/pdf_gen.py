"""
PDF Generator — render Anschreiben (text) în PDF + asamblare Bewerbung completă.

`text_to_pdf` folosește fonturile core (latin-1) — suficient pentru germană
(ä ö ü ß). Caracterele „fancy" (en-dash, ghilimele tipografice, €) sunt
normalizate înainte, ca să nu crape encoder-ul.
`assemble_bewerbung` lipește Anschreiben-ul peste CV + certificate (pypdf).
"""

from pathlib import Path

from fpdf import FPDF
from fpdf.enums import XPos, YPos
from pypdf import PdfWriter

# Caractere non-latin-1 frecvente în textul generat de AI → echivalent sigur
_REPLACEMENTS = {
    "–": "-", "—": "-", "‘": "'", "’": "'",
    "‚": "'", "“": '"', "”": '"', "„": '"',
    "…": "...", "€": "EUR", " ": " ", "•": "-",
}


def _latin1_safe(text: str) -> str:
    for bad, good in _REPLACEMENTS.items():
        text = text.replace(bad, good)
    return text.encode("latin-1", "replace").decode("latin-1")


def text_to_pdf(
    text: str,
    output_path: str | Path,
    *,
    font_size: int = 11,
    line_height: float = 6.0,
) -> Path:
    """Render un text simplu într-un PDF A4 (DIN-friendly margins)."""
    pdf = FPDF(format="A4")
    pdf.set_margins(left=25, top=20, right=25)
    pdf.set_auto_page_break(auto=True, margin=20)
    pdf.add_page()
    pdf.set_font("Helvetica", size=font_size)

    for line in _latin1_safe(text).split("\n"):
        if line.strip() == "":
            pdf.ln(line_height * 0.6)
        else:
            pdf.multi_cell(0, line_height, text=line, new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    pdf.output(str(out))
    return out


def merge_pdfs(paths: list[str | Path], output_path: str | Path) -> tuple[Path, list[Path]]:
    """Lipește mai multe PDF-uri într-unul singur (în ordinea dată).

    Ignoră fișierele lipsă / non-PDF. Întoarce (output, lista folosită).
    """
    writer = PdfWriter()
    used: list[Path] = []
    for p in paths:
        p = Path(p)
        if p.exists() and p.suffix.lower() == ".pdf":
            writer.append(str(p))
            used.append(p)

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "wb") as f:
        writer.write(f)
    return out, used


def assemble_bewerbung(
    anschreiben_text: str,
    cv_path: str | Path,
    output_path: str | Path,
    *,
    certificates: list[str | Path] | None = None,
) -> tuple[Path, list[Path]]:
    """Bewerbung completă = Anschreiben (render) + CV + certificate, în această ordine."""
    out = Path(output_path)
    ans_pdf = text_to_pdf(anschreiben_text, out.parent / f"_{out.stem}_anschreiben.pdf")
    order = [ans_pdf, cv_path, *(certificates or [])]
    merged, used = merge_pdfs(order, out)
    ans_pdf.unlink(missing_ok=True)  # curăță temporarul
    return merged, used
