# Job_app — Multi-Agent Bewerbung Pipeline

Pipeline automatizat pentru aplicarea la joburi in industria gaming/tech din Germania.

## Rulare

```bash
# Activare venv
source .venv/bin/activate

# Pipeline complet (dry-run, implicit)
python3 main.py --step full

# Doar un pas specific
python3 main.py --step scrape          # Scraping joburi
python3 main.py --step analyze         # Analiza + Match Score
python3 main.py --step generate        # Generare Anschreiben (cover letter)
python3 main.py --step organize        # Organizare documente
python3 main.py --step verify          # Verificare documente
python3 main.py --step send            # Trimitere email (dry-run)
python3 main.py --step send --live     # Trimitere email LIVE
python3 main.py --step report          # Raport status

# Optiuni aditionale
python3 main.py --min-score 50         # Filtru scor minim (default: 30)
python3 main.py --provider brevo       # SMTP provider: gmx (default) sau brevo
python3 main.py --no-ai                # Dezactiveaza AI pt analiza
```

## Structura proiect

```
Job_app/
  main.py                  # Orchestrator principal
  test_pipeline.py         # Teste
  requirements.txt         # Dependente Python
  config/
    settings.yaml          # Configurare generala (profil, scraping, AI, email)
    game_studios.yaml      # Lista studiouri gaming pt career page scraping
    email_templates/       # Template-uri email
  agents/                  # Agenti pipeline (multi-agent pattern)
    base_agent.py          # Clasa de baza: AgentResult, AgentStatus, load_config, setup_logger
    scraper_agent.py       # Scraping joburi (JobSpy + Career Pages)
    analyzer_agent.py      # Analiza cerinte + Match Score (AI)
    cover_letter_agent.py  # Generare Anschreiben personalizate (AI)
    document_agent.py      # Organizare si verificare documente
    email_agent.py         # Trimitere emailuri SMTP
    tracker_agent.py       # Sync cu Excel Tracker
  generators/
    cover_letter_gen.py    # Generator Anschreiben (Anthropic API)
    email_body_gen.py      # Generator body email
  scrapers/
    jobspy_scraper.py      # Scraper via python-jobspy
    career_page_scraper.py # Scraper career pages custom
  sender/
    smtp_sender.py         # SMTP sender (GMX / Brevo)
  tracker/
    excel_tracker.py       # Excel tracker (openpyxl)
  documents/               # Documente aplicant (CV, certificate, scrisori)
  archive/                 # Scripturi vechi, nefolosite
  data/                    # Date runtime (Excel tracker, backups) - gitignored
  output/                  # Output runtime (logs, reports, sent_emails) - gitignored
```

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
mkdir -p data output/logs output/reports output/sent_emails
cp .env.example .env  # Completeaza cheile API
```

## Dependente principale

- `pyyaml` - configurare YAML
- `openpyxl` - Excel tracker
- `python-jobspy` - scraping joburi
- `httpx`, `beautifulsoup4`, `lxml` - web scraping
- `anthropic` - Claude API pt generare cover letter si analiza

## Configurare

- `config/settings.yaml` - configurare centrala (profil, skills, scraping, AI, email, tracker)
- `.env` - secrete: `ANTHROPIC_API_KEY`, `GMX_PASSWORD`, `BREVO_SMTP_KEY`

## Conventii

- Python 3.12+
- Comentarii si mesaje in limba romana
- Fiecare agent mosteneste din `agents/base_agent.py` si implementeaza `run()`
- `AgentResult` cu `AgentStatus` (SUCCESS, PARTIAL, FAILED, RUNNING, IDLE)
- Dry-run implicit pt trimitere email (flag `--live` pt productie)
- Config centralizata in `settings.yaml`, secrete in `.env`
