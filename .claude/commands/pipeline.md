---
description: Rulează un pas din pipeline (dry-run implicit, --live interzis din comandă)
argument-hint: <scrape|analyze|generate|organize|verify|send|poll|report|full> [opțiuni]
allowed-tools: Bash(python3 main.py:*)
---

Rulează pasul cerut din pipeline:

```
python3 main.py --step $ARGUMENTS
```

Reguli stricte:
1. **NU adăuga niciodată `--live` din proprie inițiativă.** Dry-run e implicit și rămâne implicit. Trimiterea LIVE de emailuri se face doar dacă utilizatorul o cere explicit în conversație, și chiar și atunci confirmă încă o dată înainte.
2. Activează mai întâi venv-ul dacă nu e activ (`source .venv/bin/activate`).
3. `--step full` și pașii scrape/analyze/generate trec prin `validate_environment()` — dacă pică pe secrete lipsă (`ANTHROPIC_API_KEY`, parolă SMTP), raportează ce lipsește, nu improviza workaround-uri.
4. `--step poll` NU trece prin garda de mediu și rulează ca proces separat (contorul de rate limit e pe disc în `output/logs/email_rate_state.json`).
5. La final, rezumă pe scurt: ce s-a rulat, câte joburi procesate, erori relevante.
