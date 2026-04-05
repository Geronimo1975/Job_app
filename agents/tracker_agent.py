"""
Tracker Agent — Centralizare și management aplicații în Excel.

Responsabilități:
  1. Adaugă joburi noi din scraping în tracker
  2. Actualizează statusul bazat pe pipeline
  3. Generează rapoarte / statistici
  4. Sincronizează între JSON (scraped) și Excel (tracker)
"""

import json
from datetime import datetime
from pathlib import Path

from agents.base_agent import BaseAgent, AgentResult, AgentStatus
from tracker.excel_tracker import ExcelTracker


class TrackerAgent(BaseAgent):
    """Agent pentru centralizare și tracking aplicații."""

    def __init__(self):
        super().__init__("TrackerAgent")
        self.tracker = ExcelTracker(self.config, self.project_root)

    def run(
        self,
        action: str = "sync",
        jobs: list[dict] | None = None,
        **kwargs,
    ) -> AgentResult:
        """
        Execută acțiuni pe tracker.

        Args:
            action: "sync" | "add_jobs" | "update_status" | "report"
            jobs: Date despre joburi
        """
        start = self._start()

        actions = {
            "sync": self._sync_from_json,
            "add_jobs": self._add_jobs,
            "update_status": self._update_from_pipeline,
            "report": self._generate_report,
        }

        if action not in actions:
            result = AgentResult(
                agent_name=self.name,
                status=AgentStatus.FAILED,
                message=f"Acțiune necunoscută: {action}",
            )
            return self._finish(result)

        result = actions[action](jobs=jobs, **kwargs)
        return self._finish(result)

    def _sync_from_json(self, **kwargs) -> AgentResult:
        """Sincronizează joburile din JSON files în tracker Excel."""
        result = AgentResult(agent_name=self.name, status=AgentStatus.RUNNING)

        data_dir = self.project_root / "data"
        all_jobs = []

        for json_file in data_dir.glob("jobs_*.json"):
            try:
                with open(json_file, "r", encoding="utf-8") as f:
                    jobs = json.load(f)
                    all_jobs.extend(jobs)
                    self.logger.info(f"  📄 {json_file.name}: {len(jobs)} joburi")
            except Exception as e:
                self.logger.error(f"  ❌ Error reading {json_file.name}: {e}")
                result.errors.append(str(e))

        if not all_jobs:
            result.status = AgentStatus.SUCCESS
            result.message = "Niciun job în fișierele JSON"
            return result

        added = 0
        for job in all_jobs:
            result.items_processed += 1
            try:
                row = self.tracker.add_job(job)
                added += 1
                result.items_succeeded += 1
            except Exception as e:
                self.logger.error(f"  ❌ Error adding job: {e}")
                result.errors.append(str(e))

        result.data = {"total_in_json": len(all_jobs), "added_to_tracker": added}
        result.status = AgentStatus.SUCCESS
        result.message = f"{added} joburi sincronizate în tracker"
        return result

    def _add_jobs(self, jobs: list[dict] | None = None, **kwargs) -> AgentResult:
        """Adaugă joburi direct în tracker."""
        result = AgentResult(agent_name=self.name, status=AgentStatus.RUNNING)

        if not jobs:
            result.status = AgentStatus.SUCCESS
            result.message = "Niciun job de adăugat"
            return result

        for job in jobs:
            result.items_processed += 1
            try:
                row = self.tracker.add_job(job)
                result.items_succeeded += 1
                self.logger.info(f"  ➕ Row {row}: {job.get('company', '?')}")
            except Exception as e:
                result.errors.append(str(e))

        result.status = AgentStatus.SUCCESS
        result.message = f"{result.items_succeeded}/{result.items_processed} joburi adăugate"
        return result

    def _update_from_pipeline(self, jobs: list[dict] | None = None, **kwargs) -> AgentResult:
        """Actualizează tracker-ul cu rezultatele pipeline-ului."""
        result = AgentResult(agent_name=self.name, status=AgentStatus.RUNNING)

        if not jobs:
            result.status = AgentStatus.SUCCESS
            result.message = "Niciun job de actualizat"
            return result

        for job in jobs:
            result.items_processed += 1
            row = job.get("_row")
            if not row:
                continue

            try:
                extra = {}

                # Match score
                analysis = job.get("analysis", {})
                if analysis.get("match_score"):
                    extra["match_score"] = analysis["match_score"]
                if analysis.get("matching_skills"):
                    extra["keywords_match"] = ", ".join(analysis["matching_skills"][:5])

                # Cover letter
                if job.get("cover_letter_path"):
                    extra["anschreiben_pdf"] = Path(job["cover_letter_path"]).name

                # Status
                status = job.get("pipeline_status", "Analysiert")
                self.tracker.update_status(row, status, **extra)
                result.items_succeeded += 1

            except Exception as e:
                result.errors.append(str(e))

        result.status = AgentStatus.SUCCESS
        result.message = f"{result.items_succeeded} joburi actualizate"
        return result

    def _generate_report(self, **kwargs) -> AgentResult:
        """Generează un raport cu statisticile aplicațiilor."""
        result = AgentResult(agent_name=self.name, status=AgentStatus.RUNNING)

        all_jobs = self.tracker.get_all_jobs()
        total = len(all_jobs)

        # Contorizare pe status
        status_counts = {}
        for job in all_jobs:
            status = job.get("Status", "Unknown")
            status_counts[status] = status_counts.get(status, 0) + 1

        # Contorizare pe sursă
        source_counts = {}
        for job in all_jobs:
            source = job.get("Quelle", "Unknown")
            source_counts[source] = source_counts.get(source, 0) + 1

        report = {
            "date": datetime.now().isoformat(),
            "total_jobs": total,
            "by_status": status_counts,
            "by_source": source_counts,
        }

        # Salvează raport
        report_dir = self.project_root / self.config.get("output", {}).get(
            "reports_dir", "output/reports"
        )
        report_dir.mkdir(parents=True, exist_ok=True)
        report_file = report_dir / f"report_{datetime.now():%Y%m%d_%H%M}.json"

        with open(report_file, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)

        # Print report
        self.logger.info(f"\n{'=' * 50}")
        self.logger.info(f"  BEWERBUNGS-REPORT — {datetime.now():%d.%m.%Y}")
        self.logger.info(f"{'=' * 50}")
        self.logger.info(f"  Total Bewerbungen: {total}")
        self.logger.info(f"\n  Nach Status:")
        for status, count in sorted(status_counts.items(), key=lambda x: -x[1]):
            self.logger.info(f"    {status}: {count}")
        self.logger.info(f"\n  Nach Quelle:")
        for source, count in sorted(source_counts.items(), key=lambda x: -x[1]):
            self.logger.info(f"    {source}: {count}")

        result.data = report
        result.status = AgentStatus.SUCCESS
        result.message = f"Report: {total} joburi — gespeichert: {report_file.name}"
        return result
