"""
BaseAgent — Clasa de bază pentru toți agenții din sistem.

Fiecare agent:
  - Încarcă configurația din config/settings.yaml
  - Are logging propriu în output/logs/
  - Raportează status și rezultate prin AgentResult
  - Poate fi rulat independent sau orchestrat din main.py
"""

import logging
import yaml
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any


# Directorul rădăcină al proiectului
PROJECT_ROOT = Path(__file__).parent.parent.resolve()


class AgentStatus(Enum):
    """Status posibile pentru un agent."""
    IDLE = "idle"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    PARTIAL = "partial"  # unele task-uri au reușit, altele nu


@dataclass
class AgentResult:
    """Rezultatul execuției unui agent."""
    agent_name: str
    status: AgentStatus
    message: str = ""
    data: dict = field(default_factory=dict)
    errors: list = field(default_factory=list)
    started_at: datetime = field(default_factory=datetime.now)
    finished_at: datetime | None = None
    items_processed: int = 0
    items_succeeded: int = 0
    items_failed: int = 0

    @property
    def duration_sec(self) -> float:
        if self.finished_at:
            return (self.finished_at - self.started_at).total_seconds()
        return 0.0

    def summary(self) -> str:
        return (
            f"[{self.agent_name}] {self.status.value} — "
            f"{self.items_succeeded}/{self.items_processed} OK "
            f"({self.duration_sec:.1f}s) — {self.message}"
        )


def load_config() -> dict:
    """Încarcă configurația principală din settings.yaml."""
    config_path = PROJECT_ROOT / "config" / "settings.yaml"
    if not config_path.exists():
        raise FileNotFoundError(f"Config lipsă: {config_path}")
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


# Folosit doar când configul nu spune nimic — nu e „CV-ul", ci plasa de siguranță.
_DEFAULT_CV = "documents/cv/Resume_George_Sebastian_Cucuiet.pdf"


def job_language(job: dict, default: str = "de") -> str:
    """Limba în care se face candidatura pentru acest job.

    Ordinea: analiza AI (a citit anunțul) → limba pieței (`market.language`,
    care ajunge și în col „Sprache") → implicit. Treapta din mijloc lipsea:
    fără analiză, un job de pe o piață engleză primea email și CV în germană.
    """
    analysis = job.get("analysis") or {}
    return (
        str(analysis.get("language") or "").strip()
        or str(job.get("language") or "").strip()
        or default
    )


def cv_path_for(config: dict, language: str | None = None) -> Path:
    """CV-ul potrivit limbii cerute, ca traseu absolut.

    Profilul are două CV-uri echivalente — `Lebenslauf` (DE) și `Resume` (EN);
    un Anschreiben german însoțit de un CV englezesc e o inconsecvență pe care
    o vede imediat cel care citește candidatura.

    `documents.cv_files.<limbă>` bate `documents.cv_file`, iar limbile
    neconfigurate cad pe `cv_file` — deci o config veche, fără `cv_files`,
    funcționează neschimbată.
    """
    docs = config.get("documents", {}) or {}
    fallback = docs.get("cv_file") or _DEFAULT_CV
    per_language = docs.get("cv_files") or {}
    key = str(language or "").strip().lower()
    return PROJECT_ROOT / (per_language.get(key) or fallback)


def package_is_stale(package: Path, *sources: Path) -> bool:
    """True dacă `package` a fost construit ÎNAINTEA vreuneia dintre surse.

    „Bewerbung Komplett" nu e un link către CV, ci un PDF în care CV-ul și
    certificatele sunt LIPITE la momentul generării — iar `_collect_attachments`
    îl preferă și îl trimite singur. Fără verificarea asta, un pachet generat
    înainte de schimbarea CV-ului continuă să trimită CV-ul VECHI, tăcut și
    la nesfârșit (întâmplat pe 2026-08-08: pachetul din 23 iulie a plecat cu
    CV-ul vechi, deși configul arăta deja spre cel nou).

    Un pachet inexistent e „învechit" prin definiție; o sursă inexistentă nu
    poate învechi nimic.
    """
    if not package.exists():
        return True
    built = package.stat().st_mtime
    return any(s.exists() and s.stat().st_mtime > built for s in sources)


def setup_logger(name: str, log_dir: Path | None = None) -> logging.Logger:
    """Creează logger cu output în fișier + consolă."""
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    config = load_config()
    level = getattr(logging, config.get("output", {}).get("log_level", "INFO"))
    logger.setLevel(level)

    formatter = logging.Formatter(
        "[%(asctime)s] %(name)-20s %(levelname)-7s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Console handler
    ch = logging.StreamHandler()
    ch.setFormatter(formatter)
    logger.addHandler(ch)

    # File handler
    if log_dir is None:
        log_dir = PROJECT_ROOT / config.get("output", {}).get("logs_dir", "output/logs")
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"{name}_{datetime.now():%Y%m%d}.log"
    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setFormatter(formatter)
    logger.addHandler(fh)

    return logger


class BaseAgent(ABC):
    """
    Clasa abstractă de bază pentru toți agenții.

    Subclasele trebuie să implementeze:
      - run(**kwargs) -> AgentResult
    """

    def __init__(self, name: str | None = None):
        self.name = name or self.__class__.__name__
        self.config = load_config()
        self.logger = setup_logger(self.name)
        self.project_root = PROJECT_ROOT
        self._status = AgentStatus.IDLE

    @property
    def status(self) -> AgentStatus:
        return self._status

    @abstractmethod
    def run(self, **kwargs) -> AgentResult:
        """Execută task-ul principal al agentului."""
        ...

    def _start(self) -> datetime:
        """Marchează începutul execuției."""
        self._status = AgentStatus.RUNNING
        start = datetime.now()
        self.logger.info(f"▶ {self.name} started")
        return start

    def _finish(self, result: AgentResult) -> AgentResult:
        """Marchează sfârșitul execuției."""
        result.finished_at = datetime.now()
        self._status = result.status
        self.logger.info(result.summary())
        return result

    def get_path(self, config_key: str) -> Path:
        """Rezolvă un path relativ din config în path absolut."""
        keys = config_key.split(".")
        val = self.config
        for k in keys:
            val = val.get(k, {})
        if isinstance(val, str):
            return self.project_root / val
        raise ValueError(f"Config key '{config_key}' nu este un path valid: {val}")
