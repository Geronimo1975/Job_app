#!/usr/bin/env python3
"""
Job URL Analyzer — Extrage cerințe, date de contact și face match cu profilul.

Utilizare:
    python3 job_analyzer.py <URL>
    python3 job_analyzer.py --file job_description.txt
    python3 job_analyzer.py --paste  (lipești textul manual)

Output:
    - Cerințe extrase (must-have / nice-to-have)
    - Date de contact (email, telefon, persoană)
    - Match scoring vs profilul tău
    - Raport salvat ca JSON + text
"""

import re
import json
import sys
import os
import argparse
from datetime import datetime
from urllib.parse import urlparse, parse_qs
from dataclasses import dataclass, field, asdict
from typing import Optional

# ============================================================
# PROFIL — actualizează aici dacă se schimbă CV-ul
# ============================================================
PROFILE = {
    "name": "George Sebastian Cucuiet",
    "skills": {
        "languages": ["Python", "HTML", "CSS", "SQL"],
        "frameworks": ["Django", "HTMX", "REST APIs", "FastAPI"],
        "ai_ml": [
            "Claude Code", "OpenAI API", "LLMs", "RAG",
            "Prompt Engineering", "Gemini", "Grok",
            "Machine Learning", "TensorFlow", "AI Agents",
            "LangChain", "n8n", "Make.com", "AI Workflows"
        ],
        "databases": ["PostgreSQL", "Supabase", "Redis"],
        "infrastructure": [
            "Docker", "Nginx", "Traefik", "Cloudflare",
            "GCP", "Google Cloud Platform", "GitHub Actions",
            "CI/CD", "Linux", "Cisco Meraki"
        ],
        "tools": [
            "Git", "GitHub", "Jira", "Asana",
            "MS Project", "Primavera", "Agile", "Scrum"
        ],
        "web_seo": [
            "WordPress", "Joomla", "Technical SEO",
            "SEO Automation", "DataforSEO", "Mautic",
            "Sendy", "Amazon SES", "Brevo"
        ],
        "building_automation": [
            "KNX", "DALI", "Crestron", "BMS",
            "Building Management Systems"
        ],
    },
    "experience_years": {
        "Python": 2,
        "Django": 2,
        "IT Infrastructure": 6,
        "SEO": 7,
        "Project Management": 6,
        "AI/ML": 2,
        "Docker": 2,
    },
    "languages": ["Deutsch", "English", "French", "Romanian"],
    "certifications": [
        "DCI Python Backend/Full-Stack (Django, PostgreSQL)",
        "EITC AI/ML Certification",
        "Project Management Certificate (IBMI Berlin)",
        "Google Cloud Platform",
        "TensorFlow Fundamentals",
        "Safety Management Instructor",
    ],
    "education": [
        "Master - Hospital & Healthcare Administration",
        "Master - European Constitution Law (Université de Lorraine)",
        "Bachelor - European Studies",
    ]
}


# ============================================================
# DATA CLASSES
# ============================================================
@dataclass
class ContactInfo:
    company: str = ""
    contact_person: str = ""
    email: str = ""
    phone: str = ""
    website: str = ""
    address: str = ""
    apply_url: str = ""
    apply_method: str = ""  # email, portal, indeed, etc.

@dataclass
class JobRequirement:
    text: str
    category: str = "general"  # technical, language, education, experience, soft_skill
    is_must_have: bool = True
    matched: bool = False
    match_detail: str = ""

@dataclass
class JobAnalysis:
    url: str = ""
    title: str = ""
    company: str = ""
    location: str = ""
    job_type: str = ""
    salary: str = ""
    posted_date: str = ""
    contact: ContactInfo = field(default_factory=ContactInfo)
    requirements_must: list = field(default_factory=list)
    requirements_nice: list = field(default_factory=list)
    responsibilities: list = field(default_factory=list)
    benefits: list = field(default_factory=list)
    match_score: float = 0.0
    match_summary: str = ""
    raw_text: str = ""
    analyzed_at: str = ""


# ============================================================
# EXTRACTORS
# ============================================================

def extract_emails(text: str) -> list:
    """Extract email addresses from text."""
    pattern = r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}'
    emails = re.findall(pattern, text)
    # Filter out common false positives
    filtered = [e for e in emails if not any(x in e.lower() for x in [
        'example.com', 'test.com', 'placeholder', '.png', '.jpg', '.svg'
    ])]
    return list(set(filtered))


def extract_phones(text: str) -> list:
    """Extract phone numbers from text."""
    patterns = [
        r'\+49[\s\-]?\d{2,4}[\s\-]?\d{3,8}[\s\-]?\d{0,6}',
        r'\+\d{1,3}[\s\-]?\(?\d{1,4}\)?[\s\-]?\d{3,10}',
        r'0\d{2,4}[\s/\-]\d{4,10}',
        r'Tel\.?:?\s*[\+\d\s\-\(\)]{8,20}',
        r'Telefon:?\s*[\+\d\s\-\(\)]{8,20}',
    ]
    phones = []
    for p in patterns:
        phones.extend(re.findall(p, text))
    return list(set([ph.strip() for ph in phones]))


def extract_contact_persons(text: str) -> list:
    """Extract contact person names from text."""
    patterns = [
        r'(?:Ansprechpartner(?:in)?|Kontakt|Contact|Bewirb dich bei|Melde dich bei|Fragen an)\s*:?\s*([A-ZÄÖÜ][a-zäöüß]+(?:\s+[A-ZÄÖÜ][a-zäöüß]+){1,3})',
        r'(?:Frau|Herr|Mrs?\.?|Mr\.?)\s+([A-ZÄÖÜ][a-zäöüß]+(?:\s+[A-ZÄÖÜ][a-zäöüß]+){0,2})',
        r'(?:z\.\s*Hd\.|z\.Hd\.)\s+([A-ZÄÖÜ][a-zäöüß]+(?:\s+[A-ZÄÖÜ][a-zäöüß]+){0,2})',
    ]
    persons = []
    for p in patterns:
        matches = re.findall(p, text)
        persons.extend(matches)
    return list(set(persons))


def extract_urls(text: str) -> list:
    """Extract URLs from text."""
    pattern = r'https?://[^\s<>"\'\)]+|www\.[^\s<>"\'\)]+'
    return list(set(re.findall(pattern, text)))


def extract_contact_info(text: str, url: str = "") -> ContactInfo:
    """Extract all contact information from job text."""
    contact = ContactInfo()
    contact.apply_url = url

    # Emails
    emails = extract_emails(text)
    if emails:
        # Prefer HR/job-related emails
        hr_emails = [e for e in emails if any(x in e.lower() for x in [
            'job', 'career', 'hr', 'bewerbung', 'recruit', 'hiring', 'personal', 'people'
        ])]
        contact.email = hr_emails[0] if hr_emails else emails[0]

    # Phones
    phones = extract_phones(text)
    if phones:
        contact.phone = phones[0]

    # Contact persons
    persons = extract_contact_persons(text)
    if persons:
        contact.contact_person = persons[0]

    # Company name extraction
    company_patterns = [
        r'(?:Firma|Company|Unternehmen|Arbeitgeber|Über uns bei)\s*:?\s*(.+?)(?:\n|$)',
        r'^(.+?)(?:\s*GmbH|\s*AG|\s*SE|\s*UG|\s*KG|\s*e\.V\.)',
    ]
    for p in company_patterns:
        match = re.search(p, text, re.MULTILINE)
        if match:
            contact.company = match.group(1).strip()
            break

    # Apply method
    if 'bewerbung' in text.lower() and '@' in text:
        contact.apply_method = 'email'
    elif 'apply' in text.lower() or 'bewerben' in text.lower():
        contact.apply_method = 'portal'
    if 'indeed' in url.lower():
        contact.apply_method = contact.apply_method or 'indeed'

    # Website
    urls = extract_urls(text)
    company_urls = [u for u in urls if not any(x in u.lower() for x in [
        'indeed', 'linkedin', 'glassdoor', 'stepstone', 'xing'
    ])]
    if company_urls:
        contact.website = company_urls[0]

    return contact


# ============================================================
# REQUIREMENTS PARSER
# ============================================================

REQUIREMENT_SECTIONS_MUST = [
    r'(?:must.?have|anforderung|voraussetzung|was du mitbringst|dein profil|'
    r'what you bring|required|qualifikation|skills?\s*(?:and|&|und)\s*experience|'
    r'was wir erwarten|danach suchen wir|essential)',
]

REQUIREMENT_SECTIONS_NICE = [
    r'(?:nice.?to.?have|wünschenswert|von vorteil|bonus|plus|ideal|preferred|optional)',
]

RESPONSIBILITY_SECTIONS = [
    r'(?:aufgabe|responsibilit|was du tust|what you.?ll do|deine aufgaben|'
    r'your impact|your role|deine rolle|tätigkeiten)',
]

BENEFIT_SECTIONS = [
    r'(?:was wir bieten|benefits|vorteile|we offer|what.?s in it for you|'
    r'das bieten wir|perks|leistungen)',
]


def categorize_requirement(text: str) -> str:
    """Categorize a requirement line."""
    text_lower = text.lower()

    tech_keywords = [
        'python', 'django', 'javascript', 'typescript', 'react', 'docker',
        'kubernetes', 'aws', 'gcp', 'azure', 'git', 'ci/cd', 'api',
        'sql', 'postgresql', 'mongodb', 'redis', 'linux', 'terraform',
        'llm', 'ai', 'ml', 'machine learning', 'deep learning',
        'langchain', 'openai', 'rag', 'vector', 'embedding',
        'n8n', 'make.com', 'automation', 'workflow', 'nginx',
        'html', 'css', 'htmx', 'fastapi', 'flask', 'celery',
    ]
    lang_keywords = ['deutsch', 'english', 'german', 'french', 'sprache', 'language']
    edu_keywords = ['studium', 'degree', 'bachelor', 'master', 'ausbildung', 'university', 'informatik']
    exp_keywords = ['erfahrung', 'experience', 'jahre', 'years', 'senior', 'track record']

    if any(kw in text_lower for kw in tech_keywords):
        return 'technical'
    elif any(kw in text_lower for kw in lang_keywords):
        return 'language'
    elif any(kw in text_lower for kw in edu_keywords):
        return 'education'
    elif any(kw in text_lower for kw in exp_keywords):
        return 'experience'
    else:
        return 'soft_skill'


def parse_requirements(text: str) -> tuple:
    """Parse requirements from job text into must-have and nice-to-have lists."""
    must_haves = []
    nice_to_haves = []

    lines = text.split('\n')
    current_section = None  # 'must', 'nice', 'responsibility', 'benefit'

    for line in lines:
        line = line.strip()
        if not line or len(line) < 5:
            continue

        # Clean bullet points
        line_clean = re.sub(r'^[\*\-•·→⁠\d+\.\)]+\s*', '', line).strip()
        if not line_clean or len(line_clean) < 5:
            continue

        line_lower = line.lower()

        # Detect section headers
        for pattern in REQUIREMENT_SECTIONS_MUST:
            if re.search(pattern, line_lower):
                current_section = 'must'
                break
        for pattern in REQUIREMENT_SECTIONS_NICE:
            if re.search(pattern, line_lower):
                current_section = 'nice'
                break
        for pattern in RESPONSIBILITY_SECTIONS:
            if re.search(pattern, line_lower):
                current_section = 'responsibility'
                break
        for pattern in BENEFIT_SECTIONS:
            if re.search(pattern, line_lower):
                current_section = 'benefit'
                break

        # Extract bullet-point requirements
        is_bullet = bool(re.match(r'^[\*\-•·→⁠]\s', line)) or bool(re.match(r'^\d+[\.\)]\s', line))

        if is_bullet and current_section in ('must', 'nice', None):
            cat = categorize_requirement(line_clean)
            is_must = current_section != 'nice'
            req = JobRequirement(text=line_clean, category=cat, is_must_have=is_must)
            if is_must:
                must_haves.append(req)
            else:
                nice_to_haves.append(req)

    return must_haves, nice_to_haves


# ============================================================
# PROFILE MATCHING
# ============================================================

def flatten_skills(profile: dict) -> set:
    """Get all skills as a flat set of lowercase strings."""
    all_skills = set()
    for category, skills in profile.get('skills', {}).items():
        for skill in skills:
            all_skills.add(skill.lower())
            # Also add individual words for multi-word skills
            for word in skill.lower().split():
                if len(word) > 2:
                    all_skills.add(word)
    return all_skills


def match_requirement(req: JobRequirement, profile: dict) -> tuple:
    """Check if a requirement matches the profile. Returns (matched, detail)."""
    text_lower = req.text.lower()
    all_skills = flatten_skills(profile)

    # Direct skill match
    for skill in all_skills:
        if skill in text_lower and len(skill) > 2:
            req.matched = True
            req.match_detail = f"Skill match: {skill}"
            return True, f"Skill match: {skill}"

    # Experience years check
    year_match = re.search(r'(\d+)\+?\s*(?:jahre|years|year|jahr)', text_lower)
    if year_match:
        required_years = int(year_match.group(1))
        for exp_name, exp_years in profile.get('experience_years', {}).items():
            if exp_name.lower() in text_lower:
                if exp_years >= required_years:
                    req.matched = True
                    req.match_detail = f"{exp_name}: {exp_years}J (gefordert: {required_years}J)"
                    return True, req.match_detail
                else:
                    req.matched = False
                    req.match_detail = f"{exp_name}: {exp_years}J < gefordert {required_years}J"
                    return False, req.match_detail

    # Language match
    for lang in profile.get('languages', []):
        if lang.lower() in text_lower:
            req.matched = True
            req.match_detail = f"Sprache: {lang}"
            return True, req.match_detail

    # Education match
    if req.category == 'education':
        for edu in profile.get('education', []):
            for word in edu.lower().split():
                if len(word) > 4 and word in text_lower:
                    req.matched = True
                    req.match_detail = f"Bildung: {edu}"
                    return True, req.match_detail

    # Certification match
    for cert in profile.get('certifications', []):
        for word in cert.lower().split():
            if len(word) > 4 and word in text_lower:
                req.matched = True
                req.match_detail = f"Zertifikat: {cert}"
                return True, req.match_detail

    return False, ""


def calculate_match_score(must_haves: list, nice_to_haves: list, profile: dict) -> tuple:
    """Calculate overall match score and summary."""
    total_must = len(must_haves)
    total_nice = len(nice_to_haves)
    matched_must = 0
    matched_nice = 0

    for req in must_haves:
        matched, detail = match_requirement(req, profile)
        if matched:
            matched_must += 1

    for req in nice_to_haves:
        matched, detail = match_requirement(req, profile)
        if matched:
            matched_nice += 1

    # Weighted score: must-haves count 70%, nice-to-haves 30%
    if total_must > 0:
        must_score = matched_must / total_must
    else:
        must_score = 0.5  # Neutral if no must-haves detected

    if total_nice > 0:
        nice_score = matched_nice / total_nice
    else:
        nice_score = 0.5

    overall = (must_score * 0.7 + nice_score * 0.3) * 100

    # Build summary
    summary_parts = []
    summary_parts.append(f"Must-Have: {matched_must}/{total_must} erfüllt ({must_score*100:.0f}%)")
    summary_parts.append(f"Nice-to-Have: {matched_nice}/{total_nice} erfüllt ({nice_score*100:.0f}%)")

    if overall >= 70:
        summary_parts.append("EMPFEHLUNG: Sehr gute Passung — sofort bewerben!")
    elif overall >= 50:
        summary_parts.append("EMPFEHLUNG: Gute Passung — Bewerbung lohnt sich")
    elif overall >= 30:
        summary_parts.append("EMPFEHLUNG: Teilweise Passung — Anschreiben stark personalisieren")
    else:
        summary_parts.append("EMPFEHLUNG: Geringe Passung — überlege gut ob sich Bewerbung lohnt")

    # Missing skills
    unmatched_must = [r for r in must_haves if not r.matched]
    if unmatched_must:
        summary_parts.append("\nFehlende Must-Haves:")
        for r in unmatched_must:
            summary_parts.append(f"  - {r.text}")

    return overall, "\n".join(summary_parts)


# ============================================================
# MAIN ANALYSIS
# ============================================================

def analyze_job_text(text: str, url: str = "") -> JobAnalysis:
    """Perform full analysis on job description text."""
    analysis = JobAnalysis()
    analysis.url = url
    analysis.raw_text = text
    analysis.analyzed_at = datetime.now().isoformat()

    # Extract contact info
    analysis.contact = extract_contact_info(text, url)

    # Parse title/company from text
    lines = [l.strip() for l in text.split('\n') if l.strip()]
    if lines:
        analysis.title = lines[0][:100]

    # Extract salary
    salary_patterns = [
        r'(\d[\d\.]+\s*(?:€|EUR)\s*[-–]\s*\d[\d\.]+\s*(?:€|EUR)(?:\s*(?:pro Jahr|per year|p\.a\.|\/Jahr|\/year))?)',
        r'((?:Gehalt|Salary|Pay|Vergütung|Compensation)\s*:?\s*.+?)(?:\n|$)',
        r'(\d+[\.,]?\d*\s*€\s*[-–]\s*\d+[\.,]?\d*\s*€\s*(?:pro Stunde|per hour|\/h))',
    ]
    for p in salary_patterns:
        match = re.search(p, text, re.IGNORECASE)
        if match:
            analysis.salary = match.group(1).strip()
            break

    # Extract job type
    type_patterns = [
        r'(Vollzeit|Teilzeit|Full.?time|Part.?time|Contract|Freelance|Freie Mitarbeit|Internship|Remote)',
    ]
    types_found = []
    for p in type_patterns:
        types_found.extend(re.findall(p, text, re.IGNORECASE))
    analysis.job_type = ", ".join(set(types_found)) if types_found else ""

    # Extract location
    loc_patterns = [
        r'(?:Standort|Location|Arbeitsort|Work Location)\s*:?\s*(.+?)(?:\n|$)',
        r'(Home\s*Office|Remote|Homeoffice)',
    ]
    for p in loc_patterns:
        match = re.search(p, text, re.IGNORECASE)
        if match:
            analysis.location = match.group(1).strip()
            break

    # Parse requirements
    analysis.requirements_must, analysis.requirements_nice = parse_requirements(text)

    # Calculate match
    analysis.match_score, analysis.match_summary = calculate_match_score(
        analysis.requirements_must, analysis.requirements_nice, PROFILE
    )

    return analysis


def format_report(analysis: JobAnalysis) -> str:
    """Format analysis as readable text report."""
    sep = "=" * 60
    lines = []

    lines.append(sep)
    lines.append("  JOB ANALYSE REPORT")
    lines.append(sep)
    lines.append(f"  Datum: {analysis.analyzed_at[:10]}")
    lines.append(f"  URL:   {analysis.url}")
    lines.append(sep)

    lines.append(f"\n  POSITION: {analysis.title}")
    if analysis.contact.company:
        lines.append(f"  FIRMA:    {analysis.contact.company}")
    if analysis.location:
        lines.append(f"  STANDORT: {analysis.location}")
    if analysis.job_type:
        lines.append(f"  TYP:      {analysis.job_type}")
    if analysis.salary:
        lines.append(f"  GEHALT:   {analysis.salary}")

    # Contact info
    lines.append(f"\n{sep}")
    lines.append("  KONTAKTDATEN")
    lines.append(sep)
    if analysis.contact.contact_person:
        lines.append(f"  Person:   {analysis.contact.contact_person}")
    if analysis.contact.email:
        lines.append(f"  Email:    {analysis.contact.email}")
    if analysis.contact.phone:
        lines.append(f"  Telefon:  {analysis.contact.phone}")
    if analysis.contact.website:
        lines.append(f"  Website:  {analysis.contact.website}")
    if analysis.contact.apply_method:
        lines.append(f"  Methode:  {analysis.contact.apply_method}")
    if analysis.contact.apply_url:
        lines.append(f"  Apply:    {analysis.contact.apply_url}")

    # Match Score
    lines.append(f"\n{sep}")
    lines.append(f"  MATCH SCORE: {analysis.match_score:.0f}%")
    lines.append(sep)
    lines.append(f"\n{analysis.match_summary}")

    # Must-Have Requirements
    if analysis.requirements_must:
        lines.append(f"\n{sep}")
        lines.append("  MUST-HAVE ANFORDERUNGEN")
        lines.append(sep)
        for req in analysis.requirements_must:
            icon = "✅" if req.matched else "❌"
            detail = f" → {req.match_detail}" if req.match_detail else ""
            lines.append(f"  {icon} [{req.category}] {req.text}{detail}")

    # Nice-to-Have Requirements
    if analysis.requirements_nice:
        lines.append(f"\n{sep}")
        lines.append("  NICE-TO-HAVE")
        lines.append(sep)
        for req in analysis.requirements_nice:
            icon = "✅" if req.matched else "⬜"
            detail = f" → {req.match_detail}" if req.match_detail else ""
            lines.append(f"  {icon} [{req.category}] {req.text}{detail}")

    lines.append(f"\n{sep}")
    return "\n".join(lines)


def save_results(analysis: JobAnalysis, output_dir: str = "."):
    """Save analysis as both text report and JSON."""
    # Create safe filename
    safe_name = re.sub(r'[^\w\-]', '_', analysis.title[:50]).strip('_')
    if not safe_name:
        safe_name = "job_analysis"
    timestamp = datetime.now().strftime("%Y%m%d_%H%M")

    # Text report
    report = format_report(analysis)
    txt_path = os.path.join(output_dir, f"Analyse_{safe_name}_{timestamp}.txt")
    with open(txt_path, 'w', encoding='utf-8') as f:
        f.write(report)

    # JSON (for programmatic use)
    json_data = {
        "url": analysis.url,
        "title": analysis.title,
        "company": analysis.contact.company,
        "location": analysis.location,
        "job_type": analysis.job_type,
        "salary": analysis.salary,
        "contact": asdict(analysis.contact),
        "match_score": analysis.match_score,
        "match_summary": analysis.match_summary,
        "requirements_must": [asdict(r) for r in analysis.requirements_must],
        "requirements_nice": [asdict(r) for r in analysis.requirements_nice],
        "analyzed_at": analysis.analyzed_at,
    }
    json_path = os.path.join(output_dir, f"Analyse_{safe_name}_{timestamp}.json")
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(json_data, f, ensure_ascii=False, indent=2)

    return txt_path, json_path, report


# ============================================================
# CLI
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        description="Job URL Analyzer — Extrage cerințe și date de contact",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Exemple:
  python3 job_analyzer.py --text "Senior Python Developer..."
  python3 job_analyzer.py --file job_description.txt
  python3 job_analyzer.py --paste
        """
    )
    parser.add_argument('url', nargs='?', help='Job URL (Indeed, LinkedIn, etc.)')
    parser.add_argument('--file', '-f', help='Read job description from file')
    parser.add_argument('--text', '-t', help='Job description text directly')
    parser.add_argument('--paste', '-p', action='store_true', help='Paste job description from stdin')
    parser.add_argument('--output', '-o', default='.', help='Output directory (default: current)')

    args = parser.parse_args()

    text = ""
    url = args.url or ""

    if args.text:
        text = args.text
    elif args.file:
        with open(args.file, 'r', encoding='utf-8') as f:
            text = f.read()
    elif args.paste:
        print("Lipiți descrierea jobului (Ctrl+D pentru a termina):")
        text = sys.stdin.read()
    elif args.url:
        print(f"URL primit: {args.url}")
        print("NOTA: Pentru a extrage textul de pe URL, folosește Chrome sau copiază textul manual.")
        print("Poți rula: python3 job_analyzer.py --file descriere.txt")
        print("Sau:       python3 job_analyzer.py --text 'descrierea jobului aici'")
        sys.exit(1)
    else:
        parser.print_help()
        sys.exit(1)

    if not text.strip():
        print("EROARE: Nu am primit text de analizat.")
        sys.exit(1)

    # Run analysis
    analysis = analyze_job_text(text, url)

    # Save results
    txt_path, json_path, report = save_results(analysis, args.output)

    # Print report
    print(report)
    print(f"\n  Raport salvat: {txt_path}")
    print(f"  JSON salvat:   {json_path}")


if __name__ == "__main__":
    main()
