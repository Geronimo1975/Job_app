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

        Returns:
            Textul emailului.
        """
        # Determină template-ul
        if template_name:
            template_file = self.templates_dir / f"{template_name}.txt"
        elif contact_person:
            template_file = self.templates_dir / f"{language}_formal.txt"
        else:
            template_file = self.templates_dir / f"{language}_generic.txt"

        if not template_file.exists():
            # Fallback la template german generic
            template_file = self.templates_dir / "de_generic.txt"

        template_text = template_file.read_text(encoding="utf-8")

        # Determină sufixul de adresare (Herr/Frau)
        anrede_suffix = ""
        if contact_person:
            anrede_suffix = ""  # user trebuie să specifice "r Herr X" / " Frau Y"

        # Completează template-ul
        body = template_text.format(
            firma=company,
            position=position,
            kontakt_person=contact_person or "Damen und Herren",
            anrede_suffix=anrede_suffix,
            custom_paragraph=custom_paragraph or self._default_paragraph(position),
            sender_name=self.applicant.get("name", ""),
            sender_email=self.applicant.get("email", ""),
            phone=self.applicant.get("phone", ""),
            linkedin=self.applicant.get("linkedin", ""),
            github=self.applicant.get("github", ""),
        )

        return body

    def _default_paragraph(self, position: str) -> str:
        """Generează un paragraf default bazat pe skills."""
        skills = self.config.get("skills", {}).get("primary", [])
        skills_text = ", ".join(skills[:4])
        return (
            f"Als erfahrener Entwickler mit Schwerpunkt auf {skills_text} "
            f"bringe ich die technische Expertise mit, die für die Position "
            f"als {position} gefragt ist."
        )
