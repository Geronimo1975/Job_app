---
description: Raport rapid de stare — git, teste, rate limit email, launchd poll
allowed-tools: Bash(git status:*), Bash(git log:*), Bash(python -m pytest:*), Bash(cat output/logs/email_rate_state.json), Bash(launchctl list:*), Read, Glob
---

Generează un raport scurt de stare a proiectului:

1. **Git** — branch curent, fișiere modificate, ultimele 5 commit-uri (`git log --oneline -5`).
2. **Teste** — rulează `python -m pytest -q` și raportează passed/failed.
3. **Rate limit email** — citește `output/logs/email_rate_state.json` (dacă există) și spune câte emailuri s-au trimis azi din limita `email.max_emails_per_day`.
4. **Poll launchd** — `launchctl list | grep jobapp` pentru a verifica dacă jobul `com.jobapp.poll` e încărcat.

Prezintă totul compact, în română, cu problemele (dacă există) evidențiate primele.
