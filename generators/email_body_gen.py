"""
Email Body Generator — Generare body email din templates.

Încarcă template-uri din config/email_templates/ și le completează
cu datele aplicantului și ale jobului.
"""

from pathlib import Path


class EmailBodyGenerator:
    """Generator de email body din template-uri."""

    def __init__(self, config: dict, project_root: Path):
        self.config = config
        self.project_root = project_root
        self.applicant = config.get("applicant", {})
        self.templates_dir = project_root / "config" / "email_templates"

    def generate(
        self,
        company: str,
        position: str,
        contact_person: str = "",
        language: str = "de",
        custom_paragraph: str = "",
        template_name: str | None = None,
        analysis: dict | None = None,
    ) -> str:
        """
        Generează body-ul emailului din template.

        Args:
            company: Numele companiei
            position: Titlul poziției
            contact_person: Persoana de contact (opțional)
            language: "de" sau "en"
            custom_paragraph: Paragraf personalizat (opțional)
            template_name: Numele template-ului (opțional)
            analysis: Rezultatul analyzer-ului (matching_skills, key_requirements)
                pentru personalizarea paragrafului conform anunțului.

        Returns:
            Textul emailului.
        """
        cp = (contact_person or "").strip()

        # Formal doar când avem o adresare validă:
        #   DE → titlu „Herr "/„Frau " (altfel saluturi gramatical greșite)
        #   EN → orice persoană de contact e suficientă
        if language == "de":
            use_formal = cp.startswith("Herr ") or cp.startswith("Frau ")
        else:
            use_formal = bool(cp)

        # Determină template-ul, cu fallback pe limbă, apoi pe germană generică
        if template_name:
            template_file = self.templates_dir / f"{template_name}.txt"
        else:
            kind = "formal" if use_formal else "generic"
            template_file = self.templates_dir / f"{language}_{kind}.txt"
        if not template_file.exists():
            template_file = self.templates_dir / f"{language}_generic.txt"
        if not template_file.exists():
            template_file = self.templates_dir / "de_generic.txt"

        template_text = template_file.read_text(encoding="utf-8")

        # Sufix de adresare corect gramatical (DE): „Herr" → „r", „Frau" → „"
        anrede_suffix = "r" if cp.startswith("Herr ") else ""
        default_anrede = "Damen und Herren" if language == "de" else "Hiring Manager"

        # Completează template-ul
        body = template_text.format(
            firma=company,
            position=position,
            kontakt_person=cp or default_anrede,
            anrede_suffix=anrede_suffix,
            custom_paragraph=custom_paragraph
            or self._personalized_paragraph(position, analysis, language),
            sender_name=self.applicant.get("name", ""),
            sender_email=self.applicant.get("email", ""),
            phone=self.applicant.get("phone", ""),
            linkedin=self.applicant.get("linkedin", ""),
            github=self.applicant.get("github", ""),
        )

        return body

    def _personalized_paragraph(
        self, position: str, analysis: dict | None, language: str = "de"
    ) -> str:
        """Paragraf personalizat conform anunțului (skills potrivite + cerință cheie).

        Folosește rezultatul analyzer-ului; cade pe skills primare doar dacă
        nu există analiză.
        """
        analysis = analysis or {}
        matched = [s for s in analysis.get("matching_skills", []) if s][:4]
        if not matched:
            matched = self.config.get("skills", {}).get("primary", [])[:4]
        reqs = [r for r in analysis.get("key_requirements", []) if r][:1]

        if language == "en":
            p = (
                f"My background in {', '.join(matched)} maps directly to what you are "
                f"looking for in the {position} role"
                if matched
                else f"My professional background maps directly to the {position} role"
            )
            if reqs:
                p += f", in particular {reqs[0].rstrip('. ')}"
            return p + "."

        p = (
            f"Mit meinem Schwerpunkt auf {', '.join(matched)} bringe ich genau die "
            f"Kompetenzen mit, die für die Position als {position} gefragt sind"
            if matched
            else f"Ich bringe genau die Kompetenzen mit, die für die Position als {position} gefragt sind"
        )
        if reqs:
            p += f" – insbesondere {reqs[0].rstrip('. ')}"
        return p + "."
