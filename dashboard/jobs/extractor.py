"""Extragere câmpuri canonice dintr-un URL de job, cu Claude.

Funcție pură (fără Django), ușor de testat. Întoarce mereu un dict;
codează eșecul în `extraction_status`, nu ridică excepții spre view.
"""
import json
import os
import re
from pathlib import Path

import anthropic
import httpx
import yaml
from bs4 import BeautifulSoup

# dashboard/jobs/extractor.py → parents[2] = rădăcina repo-ului
_REPO_ROOT = Path(__file__).resolve().parents[2]
_SETTINGS = _REPO_ROOT / "config" / "settings.yaml"
_DEFAULT_MODEL = "claude-sonnet-4-6"

_MAX_DESCRIPTION_CHARS = 5000
_MAX_PAGE_TEXT_CHARS = 15000

_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

_STRING_FIELDS = ("title", "company", "location", "email_hr", "language", "date_posted")

_PROMPT = """Ești un extractor de date pentru anunțuri de job. Mai jos e textul unei pagini de job.
Extrage câmpurile și răspunde DOAR cu un obiect JSON valid (fără markdown, fără text în plus):

- title: titlul postului (string)
- company: numele companiei (string)
- location: localitate/regiune (string)
- description: descrierea postului, rezumată la esențial (string, max ~2000 caractere)
- email_hr: email de contact/aplicare dacă apare, altfel null
- salary_min: salariul minim ca număr întreg (fără simboluri), altfel null
- salary_max: salariul maxim ca număr întreg, altfel null
- language: limba anunțului ca cod ISO ("de", "en", "ro"), altfel null
- date_posted: data publicării dacă apare (string), altfel null

Folosește null unde informația lipsește. Nu inventa date.

TEXT PAGINĂ:
{page}
"""


def _ai_model() -> str:
    """Modelul AI din config/settings.yaml, cu fallback."""
    try:
        with open(_SETTINGS, encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        return (cfg.get("ai") or {}).get("model") or _DEFAULT_MODEL
    except Exception:
        return _DEFAULT_MODEL


def _blank_result(url: str, status: str, raw) -> dict:
    """Dict canonic gol, cu statusul dat."""
    return {
        "url": url,
        "title": "", "company": "", "location": "", "description": "",
        "email_hr": "", "salary_min": None, "salary_max": None,
        "language": "", "date_posted": "",
        "source": "dashboard",
        "extraction_status": status,
        "raw_json": raw if isinstance(raw, dict) else {"raw": raw},
    }


def _fetch_text(url: str) -> str:
    """Descarcă pagina și o reduce la text curat, trunchiat."""
    headers = {"User-Agent": _USER_AGENT, "Accept-Language": "en-US,en;q=0.9,de;q=0.8"}
    resp = httpx.get(url, headers=headers, timeout=15, follow_redirects=True)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    return soup.get_text(" ", strip=True)[:_MAX_PAGE_TEXT_CHARS]


def _call_ai(page_text: str) -> str:
    """Întoarce textul brut al răspunsului AI (posibil non-JSON)."""
    client = anthropic.Anthropic()
    response = client.messages.create(
        model=_ai_model(),
        max_tokens=1000,
        temperature=0,
        messages=[{"role": "user", "content": _PROMPT.format(page=page_text)}],
    )
    return response.content[0].text.strip()


def _coerce_int(value):
    """Convertește '60.000 €' / 60000.0 / 60000 → int; altfel None."""
    if value is None or isinstance(value, bool):
        return None
    # float-ul NU trece prin regex: str(60000.0)='60000.0' ar pierde punctul → 600000
    if isinstance(value, (int, float)):
        return int(value)
    digits = re.sub(r"[^\d]", "", str(value))
    return int(digits) if digits else None


def extract_job(url: str) -> dict:
    """Fetch → text → Claude → dict canonic. Nu ridică excepții."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return _blank_result(url, "error", {"error": "ANTHROPIC_API_KEY lipsă din mediu"})

    try:
        page_text = _fetch_text(url)
    except Exception as e:
        return _blank_result(url, "error", {"error": f"fetch eșuat: {e}"})

    try:
        raw_text = _call_ai(page_text)
    except Exception as e:
        return _blank_result(url, "error", {"error": f"AI eșuat: {e}"})

    cleaned = re.sub(r"^```json\s*", "", raw_text)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        return _blank_result(url, "partial", {"raw": raw_text})

    # JSON valid dar non-obiect (listă/număr) = neutilizabil → partial, nu ok
    status = "ok" if isinstance(data, dict) else "partial"
    result = _blank_result(url, status, data if isinstance(data, dict) else {"raw": data})
    if isinstance(data, dict):
        for field in _STRING_FIELDS:
            if data.get(field) is not None:
                result[field] = str(data[field]).strip()
        if data.get("description") is not None:
            result["description"] = str(data["description"])[:_MAX_DESCRIPTION_CHARS]
        result["salary_min"] = _coerce_int(data.get("salary_min"))
        result["salary_max"] = _coerce_int(data.get("salary_max"))
    return result
