"""
Job_app Agents — Multi-Agent System pentru automatizarea aplicațiilor.

Agenți disponibili:
  - ScraperAgent:      Scraping anunțuri de joburi
  - AnalyzerAgent:     Analiză cerințe și potrivire
  - CoverLetterAgent:  Generare scrisori de intenție
  - EmailAgent:        Trimitere emailuri SMTP
  - DocumentAgent:     Organizare documente
  - TrackerAgent:      Tracking aplicații în Excel
"""

from agents.base_agent import BaseAgent, AgentStatus, AgentResult
from agents.scraper_agent import ScraperAgent
from agents.analyzer_agent import AnalyzerAgent
from agents.cover_letter_agent import CoverLetterAgent
from agents.email_agent import EmailAgent
from agents.document_agent import DocumentAgent
from agents.tracker_agent import TrackerAgent

__all__ = [
    "BaseAgent",
    "AgentStatus",
    "AgentResult",
    "ScraperAgent",
    "AnalyzerAgent",
    "CoverLetterAgent",
    "EmailAgent",
    "DocumentAgent",
    "TrackerAgent",
]
