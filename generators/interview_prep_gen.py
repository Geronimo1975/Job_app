"""
Generator de pregătire pentru interviu — DETERMINIST, fără AI.

Strategia „AI Mock Interview / interview prep" de la JobCopilot & LoopCV, dar
adaptată formei acestui proiect: nu un chat interactiv, ci o foaie de pregătire
generată dintr-un anunț + skill-urile candidatului. Determinist înseamnă zero
cost, zero latență, rezultat reproductibil și testabil fără să mock-uim un LLM.

Fachfragen vin din skill-urile care apar TEXTUAL în anunț (reutilizăm
`skills_present` din prefiltru — o singură implementare de potrivire). Restul
(Verhaltensfragen, întrebări inverse) sunt liste stabile, DE/EN pe limba
jobului, cu numele firmei injectat.
"""

from __future__ import annotations

from filters.relevance import skills_present

# Întrebări comportamentale standard — valabile pentru orice rol.
_BEHAVIORAL = {
    "de": [
        "Erzählen Sie von einer schwierigen Situation im Projekt und wie Sie sie gelöst haben.",
        "Wie priorisieren Sie, wenn mehrere Aufgaben gleichzeitig dringend sind?",
        "Beschreiben Sie einen Fehler, aus dem Sie etwas gelernt haben.",
        "Wie arbeiten Sie mit Teams zusammen, die remote oder in anderen Zeitzonen sind?",
    ],
    "en": [
        "Tell me about a difficult situation in a project and how you solved it.",
        "How do you prioritise when several tasks are urgent at once?",
        "Describe a mistake you made and what you learned from it.",
        "How do you collaborate with remote or cross-timezone teams?",
    ],
}

# Întrebări pe care CANDIDATUL ar trebui să le pună — semnalează interes real.
_REVERSE = {
    "de": [
        "Wie sieht Erfolg in dieser Rolle nach den ersten sechs Monaten aus?",
        "Wie ist das Team aufgebaut und wie werden Entscheidungen getroffen?",
        "Welche technischen Herausforderungen stehen bei {company} aktuell an?",
    ],
    "en": [
        "What does success look like in this role after the first six months?",
        "How is the team structured and how are decisions made?",
        "What technical challenges is {company} currently facing?",
    ],
}

_LABELS = {
    "de": {
        "title": "Interview-Vorbereitung",
        "fach": "Fachfragen (auf Basis der Anzeige)",
        "fach_q": "Erzählen Sie von Ihrer Erfahrung mit {skill}.",
        "fach_empty": "Keine Ihrer Kernkompetenzen explizit in der Anzeige gefunden — "
                      "bereiten Sie allgemeine Beispiele zu Ihren Hauptskills vor.",
        "behav": "Verhaltensfragen",
        "reverse": "Fragen, die SIE stellen sollten",
        "tips": "Vorbereitungstipps",
        "tip_lines": [
            "Für jede Fachfrage ein konkretes Projektbeispiel (Situation → Aufgabe → Aktion → Ergebnis).",
            "Zahlen bereithalten: Teamgröße, Durchsatz, gesparte Zeit.",
            "{company} vorab recherchieren: Produkt, letzte News, Tech-Stack.",
        ],
    },
    "en": {
        "title": "Interview Preparation",
        "fach": "Technical questions (based on the posting)",
        "fach_q": "Tell me about your experience with {skill}.",
        "fach_empty": "None of your core skills were found explicitly in the posting — "
                      "prepare general examples around your main skills.",
        "behav": "Behavioural questions",
        "reverse": "Questions YOU should ask",
        "tips": "Preparation tips",
        "tip_lines": [
            "For each technical question, one concrete project example (Situation → Task → Action → Result).",
            "Have numbers ready: team size, throughput, time saved.",
            "Research {company} beforehand: product, recent news, tech stack.",
        ],
    },
}


def generate_prep(job_description: str, position: str, company: str,
                  skills: list[str], language: str = "de") -> str:
    """Foaie de pregătire pentru interviu, text structurat.

    `skills` e lista din config (primary + secondary); Fachfragen se construiesc
    doar din cele prezente în `job_description`. Limba necunoscută cade pe „de".
    """
    lang = language if language in _LABELS else "de"
    L = _LABELS[lang]
    firma = company or ("das Unternehmen" if lang == "de" else "the company")
    stelle = position or ("die Position" if lang == "de" else "the role")

    out: list[str] = [f"# {L['title']}: {stelle} @ {firma}", ""]

    matched = skills_present(job_description or "", skills or [])
    out.append(f"## {L['fach']}")
    if matched:
        for skill in matched:
            out.append(f"- {L['fach_q'].format(skill=skill)}")
    else:
        out.append(f"- {L['fach_empty']}")
    out.append("")

    out.append(f"## {L['behav']}")
    out += [f"- {q}" for q in _BEHAVIORAL[lang]]
    out.append("")

    out.append(f"## {L['reverse']}")
    out += [f"- {q.format(company=firma)}" for q in _REVERSE[lang]]
    out.append("")

    out.append(f"## {L['tips']}")
    out += [f"- {t.format(company=firma)}" for t in L["tip_lines"]]

    return "\n".join(out)
