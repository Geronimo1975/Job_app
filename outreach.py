"""
Helperi de outreach — „insider connections" fără scraping.

Strategia „recommended insider connections" de la Jobright, dar etic: NU
scrapăm LinkedIn (încălcare ToS, fragil, risc de ban), ci construim un link de
CĂUTARE pe care userul îl deschide manual. Zero cereri de rețea, zero date
colectate — doar un URL care deschide persoanele relevante de la firma țintă.
"""

from __future__ import annotations

from urllib.parse import quote_plus

# Roluri care decid de obicei la o candidatură spontană pe email: cine citește
# Anschreiben-ul. Căutarea le combină cu numele firmei.
_HIRING_ROLES = "recruiter OR Talent OR HR OR Personal OR Hiring"


def linkedin_people_search_url(company: str, roles: str = _HIRING_ROLES) -> str:
    """URL de căutare LinkedIn: oameni de la `company` în roluri de hiring.

    Întoarce "" pentru o firmă goală — fără firmă nu există cui să scrii, iar
    un link fără termen ar deschide toată rețeaua. Firma se citează, ca un nume
    cu spații ("Acme Media GmbH") să rămână un singur termen.
    """
    name = str(company or "").strip()
    if not name:
        return ""
    keywords = f'"{name}" ({roles})'
    return "https://www.linkedin.com/search/results/people/?keywords=" + quote_plus(keywords)
