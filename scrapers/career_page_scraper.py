"""
Career Page Scraper — Scraping direct pe paginile de cariere ale studiourilor.

Folosește httpx + BeautifulSoup pentru a extrage joburi de pe
paginile career ale studiourilor din config/game_studios.yaml.
"""

import json
import re
import yaml
from datetime import datetime
from pathlib import Path

try:
    import httpx
    HAS_HTTPX = True
except ImportError:
    HAS_HTTPX = False

try:
    from bs4 import BeautifulSoup
    HAS_BS4 = True
except ImportError:
    HAS_BS4 = False


class CareerPageScraper:
    """Scraper pentru paginile de cariere ale studiourilor de jocuri."""

    # Patterns comune pentru link-uri de joburi pe career pages
    JOB_LINK_PATTERNS = [
        r"/jobs?/",
        r"/careers?/",
        r"/positions?/",
        r"/openings?/",
        r"/vacancies/",
        r"/stellenangebote/",
        r"/arbeiten-bei/",
        r"greenhouse\.io",
        r"lever\.co",
        r"workable\.com",
        r"bamboohr\.com",
        r"recruitee\.com",
    ]

    # Keywords care indică joburi relevante
    RELEVANT_KEYWORDS = [
        "python", "data", "backend", "engineer", "developer",
        "ai", "machine learning", "automation", "devops",
        "analytics", "tools", "pipeline", "infrastructure",
    ]

    # Extragere email de contact
    EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")
    # Prefixe preferate pentru un email de aplicare (în ordine)
    PREFERRED_PREFIXES = (
        "bewerbung", "karriere", "career", "jobs", "job", "recruiting",
        "recruitment", "hr", "personal", "kontakt", "contact", "info",
    )
    _EMAIL_JUNK_SUFFIX = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg")

    def __init__(self, config: dict, project_root: Path):
        self.config = config
        self.project_root = project_root
        self.studios = self._load_studios()

    def _load_studios(self) -> list[dict]:
        """Încarcă lista de studiouri din YAML."""
        studios_path = self.project_root / "config" / "game_studios.yaml"
        if not studios_path.exists():
            return []
        with open(studios_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return data.get("studios", [])

    def _extract_email(self, soup: "BeautifulSoup", url: str, headers: dict) -> str:
        """Extrage un email de contact din pagină; dacă lipsește, încearcă Impressum."""
        candidates = self._find_emails_in_soup(soup)

        if not candidates:
            impressum_url = self._find_impressum_link(soup, url)
            if impressum_url:
                try:
                    resp = httpx.get(impressum_url, headers=headers, timeout=15, follow_redirects=True)
                    resp.raise_for_status()
                    candidates = self._find_emails_in_soup(BeautifulSoup(resp.text, "html.parser"))
                except Exception:
                    candidates = []

        if not candidates:
            return ""

        for prefix in self.PREFERRED_PREFIXES:
            for email in candidates:
                if email.lower().split("@", 1)[0].startswith(prefix):
                    return email
        return candidates[0]

    def _find_emails_in_soup(self, soup: "BeautifulSoup") -> list[str]:
        """Caută adrese email în link-uri mailto: și în textul vizibil al paginii."""
        found: list[str] = []
        for a in soup.find_all("a", href=True):
            if a["href"].lower().startswith("mailto:"):
                addr = a["href"][7:].split("?", 1)[0].strip()
                if addr and addr not in found:
                    found.append(addr)
        for match in self.EMAIL_RE.finditer(soup.get_text(" ")):
            addr = match.group(0)
            if addr.lower().endswith(self._EMAIL_JUNK_SUFFIX) or addr in found:
                continue
            found.append(addr)
        return found

    def _find_impressum_link(self, soup: "BeautifulSoup", base_url: str) -> str:
        """Găsește link-ul spre pagina de Impressum, dacă există."""
        from urllib.parse import urljoin
        for a in soup.find_all("a", href=True):
            text = a.get_text(strip=True).lower()
            if "impressum" in text or "impressum" in a["href"].lower() or "imprint" in text:
                return urljoin(base_url, a["href"])
        return ""

    def scrape(self, studio_names: list[str] | None = None) -> list[dict]:
        """
        Scrape career pages pentru studiourile specificate (sau toate).

        Returns:
            Lista de joburi găsite.
        """
        if not HAS_HTTPX:
            raise ImportError("httpx nu este instalat. Rulează: pip install httpx")
        if not HAS_BS4:
            raise ImportError("beautifulsoup4 nu este instalat. Rulează: pip install beautifulsoup4")

        studios = self.studios
        if studio_names:
            studios = [s for s in studios if s["name"] in studio_names]

        all_jobs = []
        for studio in studios:
            career_url = studio.get("career_url")
            if not career_url:
                continue

            try:
                jobs = self._scrape_studio(studio)
                all_jobs.extend(jobs)
            except Exception as e:
                print(f"  ⚠ Error scraping {studio['name']}: {e}")
                continue

        return all_jobs

    def _scrape_studio(self, studio: dict) -> list[dict]:
        """Scrape o singură pagină de career."""
        url = studio["career_url"]
        name = studio["name"]

        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "en-US,en;q=0.9,de;q=0.8",
        }

        response = httpx.get(url, headers=headers, timeout=15, follow_redirects=True)
        response.raise_for_status()

        soup = BeautifulSoup(response.text, "html.parser")
        jobs = []

        # Email de contact: override din YAML, altfel extras din pagină/Impressum
        email = studio.get("email_hr") or self._extract_email(soup, url, headers)

        # Caută link-uri care par a fi joburi
        for link in soup.find_all("a", href=True):
            href = link["href"]
            text = link.get_text(strip=True)

            if not text or len(text) < 5:
                continue

            # Verifică dacă link-ul pare a fi un job
            is_job_link = any(re.search(p, href, re.I) for p in self.JOB_LINK_PATTERNS)
            has_relevant_text = any(kw in text.lower() for kw in self.RELEVANT_KEYWORDS)

            if is_job_link or has_relevant_text:
                # Construiește URL complet
                if href.startswith("/"):
                    from urllib.parse import urljoin
                    href = urljoin(url, href)

                job = {
                    "id": f"career_{name}_{hash(href)}",
                    "source": "career_page",
                    "platform": name,
                    "title": text[:200],
                    "company": name,
                    "location": studio.get("location", ""),
                    "url": href,
                    "description": "",
                    "email_hr": email,
                    "language": studio.get("language", "en"),
                    "scraped_at": datetime.now().isoformat(),
                    "applied": False,
                    "status": "new",
                }

                # Evită duplicatele în aceeași sesiune
                if not any(j["url"] == href for j in jobs):
                    jobs.append(job)

        return jobs

    def save_results(self, jobs: list[dict], output_path: Path | None = None) -> Path:
        """Salvează rezultatele în JSON."""
        if output_path is None:
            output_path = self.project_root / "data" / "jobs_career_pages.json"

        output_path.parent.mkdir(parents=True, exist_ok=True)

        existing = []
        if output_path.exists():
            with open(output_path, "r", encoding="utf-8") as f:
                existing = json.load(f)

        new_count = 0
        for job in jobs:
            if not any(j["url"] == job["url"] for j in existing):
                existing.append(job)
                new_count += 1

        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(existing, f, indent=2, ensure_ascii=False, default=str)

        return output_path
