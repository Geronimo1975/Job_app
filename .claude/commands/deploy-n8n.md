---
description: Push/pull workflow-uri n8n prin API-ul public (app.n8n-studio.com)
argument-hint: [push|pull] [nume-workflow opțional]
allowed-tools: Bash(python3 scripts/deploy_n8n_workflows.py:*), Read, Grep, Glob
---

Deploy workflow-uri n8n din `n8n_workflows/*.json` prin `scripts/deploy_n8n_workflows.py` (folosește `N8N_API_KEY` din `.env`).

Reguli critice (vezi CLAUDE.md, secțiunea n8n):
1. Instanța proiectului = **`app.n8n-studio.com`** (config `n8n.base_url`). **NU folosi tool-urile MCP n8n** (`mcp__n8n-mcp__*`, `mcp__claude_ai_n8n__*`) — acelea sunt conectate la altă instanță (`n8n.rotocon.world`).
2. `$env` e blocat în nodurile Code pe această instanță — secretele pe webhook merg prin credential nativ Header Auth (`X-JobApp-Secret`), nu prin `$env`.
3. Înainte de push, arată diff-ul dintre JSON-ul local și ce e pe instanță (dacă scriptul suportă pull) și cere confirmare.
4. După deploy, verifică că workflow-urile vizate sunt tot active și raportează rezultatul.
