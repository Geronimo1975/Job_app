"""
Document Agent — Organizare și management documente de aplicare.

Responsabilități:
  1. Organizează CV, certificate, referințe în directoare corecte
  2. Pregătește pachetul de documente per aplicare
  3. Verifică completitudinea documentelor
  4. Creează PDF-uri combinate (opțional)
  5. Curăță fișiere temporare
"""

import shutil
from datetime import datetime
from pathlib import Path

from agents.base_agent import BaseAgent, AgentResult, AgentStatus


# Mapping extensie → director
FILE_ROUTING = {
    "cv": ["resume", "lebenslauf", "cv"],
    "certificates": ["zertifikat", "certificate", "diploma", "zeugnis"],
    "references": ["arbeitszeugnis", "reference", "empfehlung"],
    "cover_letters": ["anschreiben", "cover_letter", "bewerbung"],
}


class DocumentAgent(BaseAgent):
    """Agent pentru organizare și management documente."""

    _CLEANUP_EXCLUDE = {".venv", "venv", ".git", "node_modules"}

    def __init__(self):
        super().__init__("DocumentAgent")
        self.docs_config = self.config.get("documents", {})

    def run(
        self,
        action: str = "organize",
        source_dir: str | None = None,
        job: dict | None = None,
        **kwargs,
    ) -> AgentResult:
        """
        Execută acțiuni pe documente.

        Args:
            action: "organize" | "verify" | "prepare_package" | "cleanup"
            source_dir: Director sursă pentru organizare
            job: Job data pentru prepare_package
        """
        start = self._start()

        actions = {
            "organize": self._organize_documents,
            "verify": self._verify_documents,
            "prepare_package": self._prepare_package,
            "cleanup": self._cleanup_temp_files,
        }

        if action not in actions:
            result = AgentResult(
                agent_name=self.name,
                status=AgentStatus.FAILED,
                message=f"Acțiune necunoscută: {action}",
            )
            return self._finish(result)

        result = actions[action](source_dir=source_dir, job=job)
        return self._finish(result)

    def _organize_documents(
        self, source_dir: str | None = None, **kwargs
    ) -> AgentResult:
        """Organizează documentele din source_dir în directoarele corecte."""
        result = AgentResult(agent_name=self.name, status=AgentStatus.RUNNING)

        # Directoare țintă
        dirs = {
            "cv": self.project_root / self.docs_config.get("cv_file", "documents/cv").rsplit("/", 1)[0],
            "certificates": self.project_root / self.docs_config.get("certificates_dir", "documents/certificates"),
            "references": self.project_root / self.docs_config.get("references_dir", "documents/references"),
            "cover_letters": self.project_root / "documents" / "cover_letters" / "generated",
        }
        for d in dirs.values():
            d.mkdir(parents=True, exist_ok=True)

        # Sursă
        src = Path(source_dir) if source_dir else self.project_root
        if not src.exists():
            result.status = AgentStatus.FAILED
            result.message = f"Director sursă nu există: {src}"
            return result

        moved = []
        for pdf in src.glob("*.pdf"):
            name_lower = pdf.name.lower()
            target_dir = None

            for category, keywords in FILE_ROUTING.items():
                if any(kw in name_lower for kw in keywords):
                    target_dir = dirs.get(category)
                    break

            if target_dir and target_dir != pdf.parent:
                dest = target_dir / pdf.name
                if not dest.exists():
                    shutil.copy2(pdf, dest)
                    moved.append({"file": pdf.name, "to": str(target_dir)})
                    self.logger.info(f"  📁 {pdf.name} → {target_dir.name}/")
                    result.items_succeeded += 1
                result.items_processed += 1

        result.data = {"moved_files": moved}
        result.status = AgentStatus.SUCCESS
        result.message = f"{result.items_succeeded} fișiere organizate"
        return result

    def _verify_documents(self, **kwargs) -> AgentResult:
        """Verifică dacă toate documentele necesare sunt prezente."""
        result = AgentResult(agent_name=self.name, status=AgentStatus.RUNNING)
        checks = []

        # CV
        cv_path = self.project_root / self.docs_config.get(
            "cv_file", "documents/cv/Resume George Sebastian Cucuiet.pdf"
        )
        checks.append({
            "document": "CV / Lebenslauf",
            "path": str(cv_path),
            "exists": cv_path.exists(),
            "size_kb": cv_path.stat().st_size / 1024 if cv_path.exists() else 0,
        })

        # Certificates
        certs_dir = self.project_root / self.docs_config.get(
            "certificates_dir", "documents/certificates"
        )
        cert_files = list(certs_dir.glob("*.pdf")) if certs_dir.exists() else []
        checks.append({
            "document": "Zertifikate",
            "path": str(certs_dir),
            "exists": len(cert_files) > 0,
            "count": len(cert_files),
            "files": [f.name for f in cert_files],
        })

        # References
        refs_dir = self.project_root / self.docs_config.get(
            "references_dir", "documents/references"
        )
        ref_files = list(refs_dir.glob("*.pdf")) if refs_dir.exists() else []
        checks.append({
            "document": "Arbeitszeugnisse",
            "path": str(refs_dir),
            "exists": len(ref_files) > 0,
            "count": len(ref_files),
            "files": [f.name for f in ref_files],
        })

        # Cover letters
        cl_dir = self.project_root / self.docs_config.get(
            "cover_letters_dir", "documents/cover_letters/generated"
        )
        cl_files = list(cl_dir.glob("*.*")) if cl_dir.exists() else []
        checks.append({
            "document": "Anschreiben (generierte)",
            "path": str(cl_dir),
            "exists": len(cl_files) > 0,
            "count": len(cl_files),
        })

        all_ok = all(c["exists"] for c in checks)
        result.data = {"checks": checks, "all_present": all_ok}
        result.items_processed = len(checks)
        result.items_succeeded = sum(1 for c in checks if c["exists"])
        result.status = AgentStatus.SUCCESS if all_ok else AgentStatus.PARTIAL
        result.message = (
            "Alle Dokumente vorhanden"
            if all_ok
            else f"{result.items_succeeded}/{result.items_processed} Dokumentgruppen vorhanden"
        )

        for check in checks:
            icon = "✅" if check["exists"] else "❌"
            self.logger.info(f"  {icon} {check['document']}")

        return result

    def _prepare_package(self, job: dict | None = None, **kwargs) -> AgentResult:
        """Pregătește pachetul complet de documente pentru un job."""
        result = AgentResult(agent_name=self.name, status=AgentStatus.RUNNING)

        if not job:
            result.status = AgentStatus.FAILED
            result.message = "Niciun job specificat"
            return result

        company = job.get("company", "Unknown")
        position = job.get("title", "Unknown")

        # Creează director per aplicare
        safe_name = "".join(c if c.isalnum() or c in " _-" else "_" for c in company)
        date_str = datetime.now().strftime("%Y%m%d")
        package_dir = (
            self.project_root / "output" / "sent_emails" / f"{date_str}_{safe_name}"
        )
        package_dir.mkdir(parents=True, exist_ok=True)

        copied = []

        # CV
        cv_path = self.project_root / self.docs_config.get(
            "cv_file", "documents/cv/Resume George Sebastian Cucuiet.pdf"
        )
        if cv_path.exists():
            shutil.copy2(cv_path, package_dir / cv_path.name)
            copied.append(cv_path.name)

        # Cover letter
        cl_path = job.get("cover_letter_path", "")
        if cl_path and Path(cl_path).exists():
            shutil.copy2(cl_path, package_dir / Path(cl_path).name)
            copied.append(Path(cl_path).name)

        self.logger.info(f"  📦 Package: {package_dir.name} ({len(copied)} fișiere)")

        result.data = {
            "package_dir": str(package_dir),
            "files": copied,
            "company": company,
            "position": position,
        }
        result.items_processed = 1
        result.items_succeeded = 1
        result.status = AgentStatus.SUCCESS
        result.message = f"Package pregătit: {package_dir.name}"
        return result

    def _is_excluded(self, path: Path) -> bool:
        """True dacă path-ul trece prin .venv/.git/node_modules — nu curățăm dependințe."""
        return any(part in self._CLEANUP_EXCLUDE for part in path.parts)

    def _cleanup_temp_files(self, **kwargs) -> AgentResult:
        """Curăță fișierele temporare (.tmp, __pycache__, etc.) — exclude .venv/.git."""
        result = AgentResult(agent_name=self.name, status=AgentStatus.RUNNING)
        cleaned = []

        # .tmp files
        for tmp in self.project_root.rglob("*.tmp"):
            if self._is_excluded(tmp):
                continue
            tmp.unlink()
            cleaned.append(str(tmp))
            self.logger.info(f"  🗑 Șters: {tmp.name}")

        # __pycache__
        for cache in self.project_root.rglob("__pycache__"):
            if self._is_excluded(cache):
                continue
            shutil.rmtree(cache)
            cleaned.append(str(cache))

        result.data = {"cleaned": cleaned}
        result.items_processed = len(cleaned)
        result.items_succeeded = len(cleaned)
        result.status = AgentStatus.SUCCESS
        result.message = f"{len(cleaned)} fișiere temporare șterse"
        return result
