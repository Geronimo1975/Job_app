"""
Analyzer Agent — Analizează joburile scraped și calculează potrivirea.

Responsabilități:
  1. Citește joburile din data/jobs_scraped.json
  2. Analizează match-ul cu profilul aplicantului
  3. Extrage cerințe cheie din job descriptions
  4. Calculează un Match Score (0-100)
  5. Actualizează tracker-ul cu rezultatele
"""

import json
import re
from datetime import datetime
from pathlib import Path

from agents.base_agent import BaseAgent, AgentResult, AgentStatus

try:
    import anthropic
    HAS_ANTHROPIC = True
except ImportError:
    HAS_ANTHROPIC = False


ANALYSIS_PROMPT = """Analysiere diese Stellenanzeige und bewerte die Passung zum Bewerberprofil.

**Bewerberprofil:**
{skills}

**Stellenanzeige:**
Firma: {company}
Position: {position}
Beschreibung:
{description}

Antworte NUR als JSON (kein Markdown, kein Text davor/danach):
{{
    "match_score": <0-100>,
    "matching_skills": ["skill1", "skill2"],
    "missing_skills": ["skill1", "skill2"],
    "key_requirements": ["req1", "req2", "req3"],
    "language": "de" oder "en",
    "seniority": "junior/mid/senior/lead",
    "summary": "Kurze Zusammenfassung der Passung (1-2 Sätze)"
}}
"""


class AnalyzerAgent(BaseAgent):
    """Agent pentru analiza și scoring-ul joburilor."""

    def __init__(self):
        super().__init__("AnalyzerAgent")
        self.skills = self.config.get("skills", {})
        self.all_skills = (
            self.skills.get("primary", [])
            + self.skills.get("secondary", [])
            + self.skills.get("gaming_specific", [])
        )

    def run(
        self,
        jobs: list[dict] | None = None,
        use_ai: bool = True,
        min_score: int = 30,
        **kwargs,
    ) -> AgentResult:
        """
        Analizează joburi și calculează match score.

        Args:
            jobs: Lista de joburi de analizat (sau citește din fișier)
            use_ai: Folosește AI pentru analiză avansată
            min_score: Scor minim pentru a considera un job relevant
        """
        start = self._start()
        result = AgentResult(agent_name=self.name, status=AgentStatus.RUNNING)

        # Încarcă joburi
        if jobs is None:
            jobs = self._load_jobs()

        if not jobs:
            result.status = AgentStatus.SUCCESS
            result.message = "Niciun job de analizat"
            return self._finish(result)

        analyzed = []
        for job in jobs:
            result.items_processed += 1
            try:
                if use_ai and HAS_ANTHROPIC and job.get("description"):
                    try:
                        analysis = self._analyze_with_ai(job)
                    except Exception as ai_err:
                        self.logger.warning(f"  ⚠ AI fallback → keywords: {ai_err}")
                        analysis = self._analyze_keyword_match(job)
                else:
                    analysis = self._analyze_keyword_match(job)

                job["analysis"] = analysis
                job["match_score"] = analysis.get("match_score", 0)

                if job["match_score"] >= min_score:
                    analyzed.append(job)
                    result.items_succeeded += 1
                    self.logger.info(
                        f"  ✅ {job.get('company', '?')} — {job.get('title', '?')} "
                        f"— Score: {job['match_score']}"
                    )
                else:
                    self.logger.debug(
                        f"  ⬜ {job.get('company', '?')} — Score: {job['match_score']} (skip)"
                    )

            except Exception as e:
                self.logger.error(f"  ❌ Error analyzing {job.get('company', '?')}: {e}")
                result.errors.append(str(e))

        # Sortează după score
        analyzed.sort(key=lambda j: j.get("match_score", 0), reverse=True)

        result.data = {
            "analyzed_jobs": analyzed,
            "total_analyzed": result.items_processed,
            "qualified": len(analyzed),
            "min_score": min_score,
        }

        result.status = AgentStatus.SUCCESS
        result.message = (
            f"{len(analyzed)}/{result.items_processed} joburi cu score >= {min_score}"
        )
        return self._finish(result)

    def _analyze_keyword_match(self, job: dict) -> dict:
        """Analiză simplă bazată pe keyword matching (fără AI)."""
        description = (job.get("description", "") + " " + job.get("title", "")).lower()

        matching = []
        missing = []
        for skill in self.all_skills:
            if skill.lower() in description:
                matching.append(skill)
            else:
                missing.append(skill)

        # Calculează score
        primary_skills = [s.lower() for s in self.skills.get("primary", [])]
        primary_match = sum(1 for s in primary_skills if s in description)
        primary_total = len(primary_skills) or 1

        score = int((primary_match / primary_total) * 70 + (len(matching) / max(len(self.all_skills), 1)) * 30)
        score = min(score, 100)

        # Extrage cerințe din description
        requirements = self._extract_requirements(description)

        return {
            "match_score": score,
            "matching_skills": matching,
            "missing_skills": missing[:5],
            "key_requirements": requirements[:5],
            "language": self._detect_language(description),
            "seniority": self._detect_seniority(description),
            "summary": f"Keyword match: {len(matching)}/{len(self.all_skills)} skills",
            "method": "keyword",
        }

    def _analyze_with_ai(self, job: dict) -> dict:
        """Analiză avansată cu Claude AI."""
        skills_text = "\n".join(f"- {s}" for s in self.all_skills)

        prompt = ANALYSIS_PROMPT.format(
            skills=skills_text,
            company=job.get("company", "?"),
            position=job.get("title", "?"),
            description=job.get("description", "")[:4000],
        )

        client = anthropic.Anthropic()
        response = client.messages.create(
            model=self.config.get("ai", {}).get("model", "claude-sonnet-4-6"),
            max_tokens=800,
            temperature=0,
            messages=[{"role": "user", "content": prompt}],
        )

        text = response.content[0].text.strip()

        # Parsează JSON din răspuns
        try:
            # Curăță eventuale markdown code blocks
            text = re.sub(r"^```json\s*", "", text)
            text = re.sub(r"\s*```$", "", text)
            analysis = json.loads(text)
            analysis["method"] = "ai"
            return analysis
        except json.JSONDecodeError:
            self.logger.warning(f"  AI response nu este JSON valid, fallback la keywords")
            return self._analyze_keyword_match(job)

    def _extract_requirements(self, text: str) -> list[str]:
        """Extrage cerințe cheie din descriere."""
        patterns = [
            r"(?:required|erforderlich|must have)[:\s]*([^\n.]+)",
            r"(?:experience with|Erfahrung mit)[:\s]*([^\n.]+)",
            r"(?:proficiency in|Kenntnisse in)[:\s]*([^\n.]+)",
        ]
        requirements = []
        for pattern in patterns:
            for match in re.finditer(pattern, text, re.I):
                req = match.group(1).strip()[:100]
                if req and req not in requirements:
                    requirements.append(req)
        return requirements

    # Cuvinte-marker pe limbă (comparate pe granițe de cuvânt, nu substring)
    _DE_MARKERS = [
        "und", "für", "wir", "mit", "eine", "einen", "deine", "ihre", "aufgaben",
        "kenntnisse", "erfahrung", "unternehmen", "stelle", "bewerbung", "sie",
        "abgeschlossene", "sowie", "oder", "bei",
    ]
    _EN_MARKERS = [
        "and", "the", "you", "with", "team", "experience", "skills", "role", "we",
        "your", "requirements", "our", "ability", "work", "for", "in", "of",
    ]

    def _detect_language(self, text: str) -> str:
        """Detectează limba anunțului prin scoring pe cuvinte întregi.

        Robust față de euristica veche cu substring (ex: „und" în „around"/
        „background"). Cade pe default_language când nu există semnal clar.
        """
        t = text.lower()
        de = sum(len(re.findall(rf"\b{w}\b", t)) for w in self._DE_MARKERS)
        en = sum(len(re.findall(rf"\b{w}\b", t)) for w in self._EN_MARKERS)
        if de == 0 and en == 0:
            return self.config.get("ai", {}).get("default_language", "de")
        return "de" if de >= en else "en"

    def _detect_seniority(self, text: str) -> str:
        """Detectează nivelul de seniority."""
        if any(w in text for w in ["lead", "principal", "staff", "head"]):
            return "lead"
        if any(w in text for w in ["senior", "sr.", "experienced"]):
            return "senior"
        if any(w in text for w in ["junior", "jr.", "entry", "einstieg"]):
            return "junior"
        return "mid"

    def _load_jobs(self) -> list[dict]:
        """Încarcă joburile din fișiere JSON."""
        jobs = []
        data_dir = self.project_root / "data"
        for json_file in data_dir.glob("jobs_*.json"):
            with open(json_file, "r", encoding="utf-8") as f:
                jobs.extend(json.load(f))
        return jobs
