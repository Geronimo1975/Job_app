"""
Cover Letter Agent — Generare Anschreiben personalizate per job.

Responsabilități:
  1. Primește joburi analizate cu cerințe cheie
  2. Generează Anschreiben personalizat cu AI (Claude/GPT)
  3. Salvează ca .txt în documents/cover_letters/generated/
  4. Opțional: convertește în PDF
  5. Actualizează tracker-ul cu path-ul fișierului
"""

from datetime import datetime
from pathlib import Path

from agents.base_agent import (
    AgentResult,
    AgentStatus,
    BaseAgent,
    cv_path_for,
    job_language,
)
from generators.cover_letter_gen import CoverLetterGenerator
from filters.relevance import skills_present


class CoverLetterAgent(BaseAgent):
    """Agent pentru generare automată de scrisori de intenție."""

    def __init__(self):
        super().__init__("CoverLetterAgent")
        self.generator = CoverLetterGenerator(self.config, self.project_root)

    def run(
        self,
        jobs: list[dict] | None = None,
        language: str | None = None,
        force_regenerate: bool = False,
        **kwargs,
    ) -> AgentResult:
        """
        Generează Anschreiben pentru fiecare job.

        Args:
            jobs: Lista de joburi analizate
            language: Override limba (de/en)
            force_regenerate: Regenerează chiar dacă există deja
        """
        start = self._start()
        result = AgentResult(agent_name=self.name, status=AgentStatus.RUNNING)

        if not jobs:
            result.status = AgentStatus.SUCCESS
            result.message = "Niciun job pentru care să generez Anschreiben"
            return self._finish(result)

        generated_files = []

        for job in jobs:
            result.items_processed += 1
            company = job.get("company", "Unknown")
            position = job.get("title", job.get("position", "Unknown"))
            description = job.get("description", "")
            analysis = job.get("analysis", {})

            # Determină limba
            lang = language or analysis.get("language", self.config.get("ai", {}).get("default_language", "de"))

            # Verifică dacă există deja
            output_dir = self.project_root / self.config.get("documents", {}).get(
                "cover_letters_dir", "documents/cover_letters/generated"
            )
            existing = self._find_existing(output_dir, company, position)
            if existing and not force_regenerate:
                self.logger.info(f"  ⏭ {company} — Anschreiben există deja: {existing.name}")
                job["cover_letter_path"] = str(existing)
                result.items_succeeded += 1
                generated_files.append(existing)
                continue

            # Generează
            self.logger.info(f"  ✍ Generiere Anschreiben: {company} — {position} ({lang})")
            try:
                # Construiește instrucțiuni speciale din analiză + ATS
                special = self._build_special_instructions(analysis, description)

                text = self.generator.generate(
                    company=company,
                    position=position,
                    job_description=description,
                    location=job.get("location", ""),
                    language=lang,
                    special_instructions=special,
                )

                # Salvează
                filepath = self.generator.save_cover_letter(
                    text=text,
                    company=company,
                    position=position,
                    output_dir=output_dir,
                )

                job["cover_letter_path"] = str(filepath)
                generated_files.append(filepath)
                result.items_succeeded += 1
                self.logger.info(f"  ✅ Salvat: {filepath.name}")

                # Render PDF Anschreiben + asamblare Bewerbung completă (CV + certificate).
                # Rezilient: un eșec aici NU pică generarea .txt deja salvată.
                try:
                    from generators.pdf_gen import text_to_pdf, assemble_bewerbung

                    pdf_path = filepath.with_suffix(".pdf")
                    text_to_pdf(text, pdf_path)
                    job["cover_letter_pdf_path"] = str(pdf_path)

                    # CV în limba scrisorii — „Bewerbung Komplett" le lipește
                    # pe amândouă, deci o nepotrivire de limbă e vizibilă direct.
                    cv = cv_path_for(self.config, job_language(job))
                    if cv.exists():
                        komplett = filepath.with_name(
                            filepath.stem.replace("Anschreiben_", "Bewerbung_Komplett_") + ".pdf"
                        )
                        certs_dir = self.project_root / self.config.get("documents", {}).get(
                            "certificates_dir", ""
                        )
                        certs = sorted(certs_dir.glob("*.pdf")) if certs_dir.exists() else []
                        assemble_bewerbung(text, cv, komplett, certificates=certs)
                        job["bewerbung_pdf_path"] = str(komplett)
                        self.logger.info(f"  📎 Bewerbung komplett: {komplett.name}")
                except Exception as pdf_err:
                    self.logger.warning(f"  ⚠ PDF Anschreiben eșuat ({company}): {pdf_err}")

            except Exception as e:
                self.logger.error(f"  ❌ Eroare la {company}: {e}")
                result.errors.append(f"{company}: {e}")

        result.data = {
            "generated_files": [str(f) for f in generated_files],
            "jobs_with_letters": [j for j in jobs if j.get("cover_letter_path")],
        }

        if result.errors and result.items_succeeded == 0:
            result.status = AgentStatus.FAILED
            result.message = f"Generare eșuată pentru toate joburile"
        elif result.errors:
            result.status = AgentStatus.PARTIAL
            result.message = f"{result.items_succeeded}/{result.items_processed} Anschreiben generate"
        else:
            result.status = AgentStatus.SUCCESS
            result.message = f"{result.items_succeeded} Anschreiben generate"

        return self._finish(result)

    def _build_special_instructions(self, analysis: dict, description: str = "") -> str:
        """Construiește instrucțiuni speciale din analiză + termeni ATS.

        `description` alimentează potrivirea ATS deterministă (strategia Jobscan):
        skill-urile din config prezente TEXTUAL în anunț sunt cerute explicit în
        Anschreiben, cu cuvintele exacte din job, fiindcă ATS-urile potrivesc pe
        termeni exacți. E complementar `matching_skills` (care vine din AI și
        poate lipsi) — deterministul prinde ce AI-ul ratează.
        """
        parts = []

        matching = analysis.get("matching_skills", [])
        if matching:
            parts.append(f"Betone besonders diese passenden Fähigkeiten: {', '.join(matching[:5])}")

        missing = analysis.get("missing_skills", [])
        if missing:
            parts.append(
                f"Diese Fähigkeiten fehlen — erwähne Lernbereitschaft: {', '.join(missing[:3])}"
            )

        requirements = analysis.get("key_requirements", [])
        if requirements:
            parts.append(f"Hauptanforderungen: {', '.join(requirements[:3])}")

        ats = self._ats_keywords(description)
        if ats:
            parts.append(
                "Verwende exakt diese Begriffe aus der Stellenanzeige (ATS-Optimierung): "
                + ", ".join(ats)
            )

        return "\n".join(parts) if parts else ""

    def _ats_keywords(self, description: str) -> list[str]:
        """Skill-urile userului (primary + secondary) prezente în anunț."""
        if not description:
            return []
        skills_cfg = self.config.get("skills", {}) or {}
        skills = (skills_cfg.get("primary", []) or []) + (skills_cfg.get("secondary", []) or [])
        return skills_present(description, skills)[:8]

    def _find_existing(self, output_dir: Path, company: str, position: str) -> Path | None:
        """Caută un Anschreiben existent pentru acest job."""
        if not output_dir.exists():
            return None

        safe_company = "".join(c if c.isalnum() or c in " _-" else "_" for c in company)
        for f in output_dir.glob(f"Anschreiben_{safe_company}*"):
            return f
        return None
