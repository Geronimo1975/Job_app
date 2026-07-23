---
description: Rulează suita pytest (opțional un fișier sau un test anume)
argument-hint: [cale-test opțională, ex. tests/test_n8n_client.py]
allowed-tools: Bash(python -m pytest:*), Bash(python3 -m pytest:*)
---

Rulează testele proiectului:

- Fără argument: `python -m pytest -q` (toată suita din `tests/`, conform `pytest.ini`).
- Cu argument: `python -m pytest $ARGUMENTS -q`.

Reguli:
- Testele folosesc config-ul real (`load_config`) + mock-uri pentru I/O extern — NU au voie să lovească rețeaua.
- Dacă apar eșecuri, raportează output-ul complet al testelor picate și analizează cauza înainte de a propune un fix (folosește superpowers:systematic-debugging).
- Nu marca task-ul ca terminat cu teste roșii.
