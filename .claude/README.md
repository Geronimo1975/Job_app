# .claude/ — configurație Claude Code pentru Job_app

Structură:

- `settings.json` — permisiuni partajate de echipă (trackuit în git):
  - **allow**: pytest, git read-only, `--step report`;
  - **ask**: orice rulare `main.py` sau scripturi care lovesc servicii reale (deploy n8n, E2E manual);
  - **deny**: citirea `.env` (secrete) și `rm -rf`.
- `settings.local.json` — override-uri personale (gitignored, nu se comite).
- `commands/` — comenzi slash de proiect:
  - `/test [cale]` — suita pytest;
  - `/pipeline <pas>` — rulează un pas din pipeline (dry-run, fără `--live`);
  - `/deploy-n8n` — push workflow-uri prin API-ul public n8n;
  - `/status` — raport rapid: git, teste, rate limit email, launchd poll.
- `agents/` — subagenți de proiect:
  - `n8n-sheet-reviewer` — review specializat pe integrarea n8n/Sheet (FIELD_MAP, headere germane, webhook-uri, rate limit).

Convenții: instrucțiunile de arhitectură stau în `CLAUDE.md` (rădăcina repo-ului); acest director conține doar tooling. Commit-uri în română cu tag `JOB-NN`.
