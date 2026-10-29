# Seeing how PIE works (v0.22)

Three views show what PIE did, what it relied on, and why.

## 1. How PIE answered (Ask PIE)

Under every answer, open **How PIE answered**. It lists:

| Step | What you see |
|---|---|
| Understand | Intents, and the word that triggered each; modules (and whether they came from the selector or the question); industry; audience; whether a short follow-up was combined with the previous question. Fixed rules, no AI. |
| Plan | The keyword query, the AI planner's ERP search terms (the planner only sees the question and module/capability names), and all queries run. |
| Retrieve | Each retrieval route (vector search, field-name match, aggregates…) and how many items it found; duplicates removed; items left out over the 40-item limit. |
| Coverage | What the knowledge base cannot answer, and the notes given to the model. |
| Answer | Who wrote the answer: the AI, a structured rule (no AI), or nobody (stopped because the knowledge is missing). The answer guide used and the prompt size. |
| Validate | Citations kept and removed, claim words not found in the knowledge, final grounding. |

Below the steps is every knowledge item the answer could use, in the order the AI saw it, with its score, the route that found it and whether it was cited. **Exact text given to the AI** shows the full prompt. Items tied to a screen link to its Relationships page.

The model's own internal reasoning is not available (the OpenAI model does not return it); the trace shows everything PIE controls around it.

API: `POST /usage/api/chat` returns `trace` (`steps`, `retrieved`, `context`, `systemPrompt`) and `runId`.

## 2. Relationships (`relations.html`)

Pick a screen. The diagram shows **Screen → APIs → backend endpoints (and services) → database tables**; hover or click a box to follow its links. Solid lines are observed in code or configuration; dashed lines are candidates inferred from SQL text. The tables below list, per table: read/write, the APIs it is reached through, which of the screen's fields are stored in it (from the field dictionary), other screens using it, and the evidence (SQL statement, DDL file). The last panel shows why PIE knows the screen exists and the Qdrant knowledge held for it.

Source: `outputs/modules/<module>/phase16-evidence-graph/evidence_graph_v16.json`; rebuild with `build-evidence-graph` after mapping changes.
API: `GET /usage/api/relations/screens`, `GET /usage/api/relations/screen/{screenId}`.

## 3. Activity Log (`logs.html`)

Every CLI command and every chat or search request is recorded as a run: activity, status (SUCCESS / WARNING / FAILED / RUNNING), times, duration, record counts, parameters (secrets masked), and error with traceback. The **Questions** tab lists chat questions; opening one shows its full trace (without the prompt text). Other tabs: knowledge inventory per module and screen, Qdrant storage per module and knowledge type, API request statistics.

Storage: `outputs/logs/pie_activity.db` (SQLite) and `outputs/logs/pie_activity.jsonl` (rotating), or `PIE_LOG_DIR`. Set `PIE_LOG_TOKEN` on a shared server; the page asks for it. `PIE_LOG_REMOTE=https://<server>/logs/api/ingest` sends runs made on another PC to the server.

```powershell
$env:PYTHONPATH = "backend"
.\.venv\Scripts\python.exe -m pie_agent.activity_log tail -n 20       # recent runs
.\.venv\Scripts\python.exe -m pie_agent.activity_log show <run id>    # one run with its events and trace
.\.venv\Scripts\python.exe -m pie_agent.activity_log qdrant-status    # snapshot Qdrant storage
.\.venv\Scripts\python.exe -m pie_agent.activity_log inventory        # rebuild the knowledge inventory
.\.venv\Scripts\python.exe -m pie_agent.activity_log prune --days 90
```
