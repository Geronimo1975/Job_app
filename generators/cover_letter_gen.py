"""
Cover Letter Generator — Generare Anschreiben personalizate cu AI.

Folosește Claude API (sau OpenAI) pentru a genera scrisori de intenție
adaptate la fiecare job description.
"""

import json
import sys
from datetime import datetime
from pathlib import Path

try:
    import anthropic
    HAS_ANTHROPIC = True
except ImportError:
    HAS_ANTHROPIC = False

try:
    import openai
    HAS_OPENAI = True
except ImportError:
    HAS_OPENAI = False


SYSTEM_PROMPT_DE = """Du bist ein professioneller Bewerbungsberater, der maßgeschneiderte
Anschreiben auf Deutsch verfasst.

Regeln:
- Formeller Geschäftston, aber nicht steif
- Konkreter Bezug zur Stellenbeschreibung
- Hebe relevante Fähigkeiten und Erfahrungen hervor
- Maximal eine DIN A4-Seite
- Verwende die DIN 5008 Briefnorm
- Kein "Hiermit bewerbe ich mich" — starte interessanter
- Zeige Begeisterung für das Unternehmen und die Position
"""

SYSTEM_PROMPT_EN = """You are a professional career consultant writing tailored cover letters
in English.

Rules:
- Professional but engaging tone
- Specific references to the job description
- Highlight relevant skills and experience
- Maximum one page
- No generic opening lines — start with something compelling
- Show enthusiasm for the company and role
"""

COVER_LETTER_TEMPLATE = """Erstelle ein Anschreiben für folgende Bewerbung:

**Bewerber:**
Name: {name}
E-Mail: {email}
Telefon: {phone}
LinkedIn: {linkedin}
GitHub: {github}

**Kernkompetenzen:**
{skills}

**Zielposition:**
Firma: {company}
Position: {position}
Standort: {location}

**Stellenbeschreibung:**
{job_description}

**Besondere Anweisungen:**
{special_instructions}

Erstelle ein professionelles Anschreiben im {language_name} Format.
Datum: {date}
"""


class CoverLetterGenerator:
    """Generator de scrisori de intenție cu AI."""

    def __init__(self, config: dict, project_root: Path):
        self.config = config
        self.project_root = project_root
        self.ai_config = config.get("ai", {})
        self.applicant = config.get("applicant", {})

    def generate(
        self,
        company: str,
        position: str,
        job_description: str,
        location: str = "",
        language: str = "de",
        special_instructions: str = "",
    ) -> str:
        """
        Generează o scrisoare de intenție personalizată.

        Args:
            company: Numele companiei
            position: Titlul poziției
            job_description: Descrierea jobului
            location: Locația
            language: "de" sau "en"
            special_instructions: Instrucțiuni speciale

        Returns:
            Textul scrisorii de intenție.
        """
        provider = self.ai_config.get("provider", "anthropic")

        # Construiește promptul
        skills_text = "\n".join(
            f"- {s}" for s in self.config.get("skills", {}).get("primary", [])
        )

        lang_name = "Deutsch (DIN 5008)" if language == "de" else "English"

        prompt = COVER_LETTER_TEMPLATE.format(
            name=self.applicant.get("name", ""),
            email=self.applicant.get("email", ""),
            phone=self.applicant.get("phone", ""),
            linkedin=self.applicant.get("linkedin", ""),
            github=self.applicant.get("github", ""),
            skills=skills_text,
            company=company,
            position=position,
            location=location,
            job_description=job_description[:4000],
            special_instructions=special_instructions or "Keine besonderen Anweisungen.",
            language_name=lang_name,
            date=datetime.now().strftime("%d.%m.%Y"),
        )

        system_prompt = SYSTEM_PROMPT_DE if language == "de" else SYSTEM_PROMPT_EN

        try:
            if provider == "anthropic":
                return self._generate_anthropic(system_prompt, prompt)
            elif provider == "openai":
                return self._generate_openai(system_prompt, prompt)
            else:
                raise ValueError(f"AI provider necunoscut: {provider}")
        except Exception as e:
            # Fallback: generare din template fără AI.
            # Avertizează vizibil (stderr) — fallback-ul NU mai e silențios,
            # dar textul livrat rămâne curat (fără note vizibile în scrisoare).
            print(
                f"  ⚠ Anschreiben AI eșuat ({company}) → fallback template fără AI: {e}",
                file=sys.stderr,
            )
            return self._generate_template_fallback(
                company, position, job_description, location, language, str(e)
            )

    def _generate_template_fallback(
        self, company, position, job_description, location, language, error_msg
    ) -> str:
        """Fallback: generează Anschreiben din template când AI nu e disponibil."""
        name = self.applicant.get("name", "George Sebastian Cucuiet")
        email = self.applicant.get("email", "")
        phone = self.applicant.get("phone", "")
        skills = ", ".join(self.config.get("skills", {}).get("primary", [])[:5])
        date = datetime.now().strftime("%d.%m.%Y")

        if language == "de":
            return f"""{name}
{email}
Tel: {phone}

{date}

{company}
{location}

Bewerbung als {position}

Sehr geehrte Damen und Herren,

die ausgeschriebene Position als {position} bei {company} hat sofort mein Interesse geweckt – sie verbindet genau die technischen Schwerpunkte, mit denen ich täglich arbeite.

Als erfahrener Entwickler mit Schwerpunkten in {skills} bringe ich eine starke technische Basis mit, die ich gerne in Ihrem Team einbringen möchte. Meine Erfahrung in der Umsetzung komplexer Integrationsprojekte und meine Fähigkeit, technische Anforderungen in effiziente Lösungen zu übersetzen, machen mich zu einem geeigneten Kandidaten für diese Rolle.

Meine vollständigen Bewerbungsunterlagen finden Sie im Anhang. Ich freue mich auf Ihre Rückmeldung und stehe gerne für ein persönliches Gespräch zur Verfügung.

Mit freundlichen Grüßen,
{name}"""
        else:
            return f"""{name}
{email}
Phone: {phone}

{date}

{company}
{location}

Application for {position}

Dear Hiring Manager,

I am writing to express my strong interest in the {position} position at {company}.

With expertise in {skills}, I bring a solid technical foundation and hands-on experience in building integration solutions, API architectures, and automated workflows.

I am particularly excited about the opportunity to contribute to {company}'s technical vision and would welcome the chance to discuss how my background aligns with your team's needs.

Please find my complete application documents attached.

Best regards,
{name}"""

    def _generate_anthropic(self, system: str, prompt: str) -> str:
        """Generare cu Claude API."""
        if not HAS_ANTHROPIC:
            raise ImportError("anthropic nu este instalat. Rulează: pip install anthropic")

        client = anthropic.Anthropic()
        response = client.messages.create(
            model=self.ai_config.get("model", "claude-sonnet-4-6"),
            max_tokens=self.ai_config.get("max_tokens", 2000),
            temperature=self.ai_config.get("temperature", 0.7),
            system=system,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.content[0].text

    def _generate_openai(self, system: str, prompt: str) -> str:
        """Generare cu OpenAI API."""
        if not HAS_OPENAI:
            raise ImportError("openai nu este instalat. Rulează: pip install openai")

        client = openai.OpenAI()
        response = client.chat.completions.create(
            model="gpt-4o",
            max_tokens=self.ai_config.get("max_tokens", 2000),
            temperature=self.ai_config.get("temperature", 0.7),
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
        )
        return response.choices[0].message.content

    def save_cover_letter(
        self,
        text: str,
        company: str,
        position: str,
        output_dir: Path | None = None,
    ) -> Path:
        """Salvează scrisoarea de intenție ca fișier text."""
        if output_dir is None:
            output_dir = self.project_root / self.config.get(
                "documents", {}
            ).get("cover_letters_dir", "documents/cover_letters/generated")

        output_dir.mkdir(parents=True, exist_ok=True)

        # Normalizează numele fișierului
        safe_company = "".join(c if c.isalnum() or c in " _-" else "_" for c in company)
        safe_position = "".join(c if c.isalnum() or c in " _-" else "_" for c in position)
        date_str = datetime.now().strftime("%Y%m%d")

        filename = f"Anschreiben_{safe_company}_{safe_position}_{date_str}.txt"
        filepath = output_dir / filename

        with open(filepath, "w", encoding="utf-8") as f:
            f.write(text)

        return filepath
