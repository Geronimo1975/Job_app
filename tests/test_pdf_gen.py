"""
Teste pentru pdf_gen — render text→PDF, normalizare unicode, merge, asamblare.
"""

from pathlib import Path

from pypdf import PdfReader

from generators.pdf_gen import _latin1_safe, text_to_pdf, merge_pdfs, assemble_bewerbung


def test_latin1_safe_normalizes_fancy_chars():
    out = _latin1_safe("Test – „Anführung“ … 60 000 €")
    # en-dash, ghilimele tipografice, …, nbsp, € → echivalente latin-1
    assert "–" not in out and "„" not in out and "…" not in out and "€" not in out
    assert out.encode("latin-1")  # nu mai aruncă


def test_latin1_safe_keeps_german_umlauts():
    out = _latin1_safe("Schöne Grüße, Müller — ä ö ü ß")
    assert "ö" in out and "ü" in out and "ß" in out and "ä" in out


def test_text_to_pdf_creates_valid_pdf(tmp_path):
    out = text_to_pdf("Sehr geehrte Frau Müller,\n\nText mit Umlaut ä ö ü ß.", tmp_path / "x.pdf")
    assert out.exists()
    assert len(PdfReader(str(out)).pages) >= 1


def test_merge_pdfs_skips_missing_and_concatenates(tmp_path):
    a = text_to_pdf("Seite A", tmp_path / "a.pdf")
    b = text_to_pdf("Seite B", tmp_path / "b.pdf")
    merged, used = merge_pdfs([a, tmp_path / "missing.pdf", b], tmp_path / "m.pdf")
    assert merged.exists()
    assert len(used) == 2  # missing.pdf ignorat
    assert len(PdfReader(str(merged)).pages) == 2


def test_assemble_bewerbung_puts_anschreiben_first(tmp_path):
    cv = text_to_pdf("LEBENSLAUF\nName: Test", tmp_path / "cv.pdf")
    out, used = assemble_bewerbung("Mein Anschreiben", cv, tmp_path / "komplett.pdf")
    assert out.exists()
    assert len(PdfReader(str(out)).pages) == 2  # Anschreiben + CV
    # temporarul Anschreiben e curățat
    assert not (tmp_path / "_komplett_anschreiben.pdf").exists()
