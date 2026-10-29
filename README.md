# PIE-Agent v0.20

PIE has two separate parts. Each one runs on its own.

```text
backend/    API server (FastAPI, Python): chat, catalog, field knowledge, Qdrant, OpenAI   port 8000
frontend/   Web pages (HTML/JS, no build step): Home, Ask PIE, Catalog, Field Explorer     port 5500
            frontend/config.js holds the backend address, which is the only link between them
data/ outputs/ .env   shared by the backend (project root)
```

## Run it

One-time setup, from the project folder:

```powershell
python -m venv .venv
.\.venv\Scripts\pip install -r backend\requirements.txt
copy .env.example .env      # then fill in the Qdrant and OpenAI settings
```

Then open two PowerShell windows:

```powershell
# Window 1: backend (API)
.\start_backend.ps1          # http://localhost:8000   (API docs: /docs)

# Window 2: frontend (web pages)
.\start_frontend.ps1         # http://localhost:5500   <- open this in the browser
```

| Page | URL |
|---|---|
| Home | http://localhost:5500/ |
| Ask PIE (chat) | http://localhost:5500/chat.html |
| Feature Catalog | http://localhost:5500/catalog.html |
| Field Explorer | http://localhost:5500/fields.html |
| Relationships (screen → API → backend → tables) | http://localhost:5500/relations.html |
| Activity Log (runs, questions, errors, Qdrant status) | http://localhost:5500/logs.html |

**Modules.** Each page has a module picker: one module, any combination, or all. Links carry the choice, e.g. `chat.html?modules=inventory` (Inventory only) or `catalog.html?modules=ess,inventory`; use **Copy link** in the picker. The link sets the starting selection; it does not restrict what the reader can open.

**Chats.** Ask PIE keeps every conversation as a saved chat (left sidebar: **+ New chat**, search, rename, archive, delete). Each chat remembers its modules and audience and is answered only from its own history. Chats are stored on the backend (`outputs/sessions/pie_sessions.db`); on a shared server, pass the signed-in user from the web server (nginx: `proxy_set_header X-PIE-User $remote_user;`) so each person sees only their own chats.

Every Ask PIE answer has a **How PIE answered** panel: the steps, every knowledge item retrieved (cited or not) and the exact text given to the AI. See [docs/transparency.md](docs/transparency.md).

**Other PCs.** Run `.\start_backend.ps1 -Network` and `.\start_frontend.ps1 -Network -Backend http://<this-PC-IP>:8000`, then allow TCP 8000 and 5500 in Windows Firewall.

**Other hosting.** The `frontend/` folder is plain static files, so any web server works (IIS, nginx, or double-clicking `index.html`). Set `apiBase` in `frontend/config.js`. If the frontend runs on another address, add that address to `PIE_CORS_ORIGINS` for the backend.

**Command line:**
- `.\pie.ps1 <command>` runs a pipeline command, e.g. `.\pie.ps1 fields-build --enrich` or `.\pie.ps1 chat --question "..."`.
- `.\.venv\Scripts\python.exe -m pytest backend/tests -k "not real_erp"` runs the tests.

More detail is in `docs/phase20_fields.md` (field knowledge and the web app) and `docs/phase19_usage.md` (Usage Phase and chat). The sections below describe earlier phases. Where they say `python -m pie_agent.cli …`, use `.\pie.ps1 …` instead.

PIE v0.15 extends the cumulative ERP discovery pipeline through Backend → Database mapping, while retaining the earlier Generation, Discovery, Screen Identity, and API Mapping phases.

## What changed
- Canonical screen identity based on normalized `module + feature`, not component class names.
- Consolidates multiple artifacts that represent the same screen.
- Separates `CONFIRMED`, `LIKELY`, and `CANDIDATE` screen identities.
- Strong evidence is required for `CONFIRMED`.
- Generates a review queue for identities that need validation.
- Optimized ERP backend endpoint discovery and screen-to-API candidate mapping for large repositories.
- Keeps secrets in `.env`; only `.env.example` is packaged.

## Prerequisites
- Python 3.11+
- Dependencies from `requirements.txt`
- ERP repositories extracted locally:
  - `data/erp/frontend`
  - `data/erp/backend`
- Optional SQL Server metadata access configured in `.env`

## `.env`
Create `.env` beside `pyproject.toml`:

```env
DATABASE_URL="mssql+pyodbc://USERNAME:PASSWORD@HOST/DATABASE?driver=ODBC+Driver+18+for+SQL+Server"
ERP_FRONTEND_PATH="./data/erp/frontend"
ERP_BACKEND_PATH="./data/erp/backend"
PIE_LLM_PROVIDER="openai"
OPENAI_API_KEY="YOUR_KEY"
OPENAI_MODEL="gpt-4.1"
PIE_DISCOVERY_REPORT="outputs/phase12-discovery/erp_discovery_report.json"
PIE_IDENTITY_OUTPUT="outputs/phase13-identity"
```

Never commit `.env` or credentials.

## Run

Install:
```bash
pip install -r requirements.txt
```

Tests:
```bash
python -m pytest -q
```

Discover ERP sources:
```bash
python -m pie_agent.cli discover-erp
```

Build canonical Screen Registry:
```bash
python -m pie_agent.cli screen-identity --input outputs/phase12-discovery/erp_discovery_report.json
```

Expected outputs:
```text
outputs/phase13-identity/
├── screen_registry_v13.json
├── screen_identity_report.json
└── screen_identity_review_queue.json
```

## v0.13 design principle
Discovery finds candidates. Identity determines whether multiple artifacts refer to one canonical screen. Evidence determines confidence. PIE does not silently invent production screens.

## Next phase
v0.14 will build stronger **Screen → API → Backend Service** mapping using the canonical Screen Registry from this phase.

## Phase 14 — Screen → API → Backend Service Mapping

Adds deterministic mapping from the Phase 14 Screen Registry to frontend HTTP evidence, FastEndpoints routes, injected BLL/service dependencies, and implementation/method evidence.


## Phase 15 — Backend → Database Mapping

Phase 15 extends the complete PIE codebase from Phase 14. It maps backend/API evidence to database objects using live SQL Server metadata when available, with SQL DDL/migration parsing as an offline fallback.

### `.env`
```env
DATABASE_URL="mssql+pyodbc://USERNAME:PASSWORD@HOST/DATABASE?driver=ODBC+Driver+18+for+SQL+Server"
ERP_FRONTEND_PATH="./data/erp/frontend"
ERP_BACKEND_PATH="./data/erp/backend"
PIE_LLM_PROVIDER="openai"
OPENAI_API_KEY="YOUR_KEY"
OPENAI_MODEL="gpt-4.1"
PIE_API_MAPPING_REGISTRY="outputs/phase14-api-mapping/screen_api_backend_mapping_v14.json"
```

### Run
```bash
python -m pytest -q
python -m pie_agent.cli map-db
```

Windows CMD:
```cmd
python -m pytest -q
python -m pie_agent.cli map-db
```

### Outputs
```text
outputs/phase15-db-mapping/
├── database_object_registry_v15.json
├── backend_database_reference_registry_v15.json
├── screen_api_database_mapping_v15.json
└── db_mapping_report_v15.json
```

### Evidence policy
Live SQL Server metadata is authoritative when connected. If the database is unavailable, PIE can parse SQL DDL/migrations as an explicitly labelled fallback. Exact source references are high-confidence; naming/token relationships remain candidate evidence and are not silently promoted to facts.

## v0.16.0 — Evidence Graph

Build a connected evidence graph from the cumulative v0.13/v0.14/v0.15 artifacts:

```bash
python -m pie_agent.cli build-evidence-graph
```

Outputs are written to `outputs/phase16-evidence-graph/`.

## v0.17.0 — ERP-wide AI Knowledge Generation

Phase 17 consumes the v0.16 Evidence Graph and creates evidence-grounded knowledge packages for ERP screens.

```bash
python -m pie_agent.cli generate-ai-knowledge --provider offline --limit 1
```

OpenAI:
```env
PIE_LLM_PROVIDER="openai"
OPENAI_API_KEY="YOUR_KEY"
OPENAI_MODEL="gpt-4.1"
PIE_AI_WORKERS="1"
```

```bash
python -m pie_agent.cli generate-ai-knowledge
```

Outputs are written to `outputs/phase17-ai-knowledge/`. Every AI record must reference evidence IDs from the Evidence Graph; unsupported/inferred claims are sent to review.


## v0.18.0 — Capability Model + Qdrant KMS

Consumes existing v0.17 per-screen knowledge without re-running OpenAI. Qdrant `pie_knowledge` is the authoritative KMS; JSON remains audit/input only. See `docs/phase18_capability_qdrant_kms.md`.

### Production Qdrant KMS

PIE v0.18 uses the existing Qdrant server configured in `.env` as the authoritative KMS.

```powershell
python -m pie_agent.cli kms-init
python -m pie_agent.cli kms-publish
python -m pie_agent.cli kms-stats
python -m pie_agent.cli kms-search --query "leave application approval workflow"
```

Default production configuration:

```env
QDRANT_URL=http://217.217.249.121:6333
QDRANT_COLLECTION=pie_knowledge
QDRANT_API_KEY=
```

The existing v0.17 knowledge is ingested locally and embedded without another OpenAI generation call. JSON remains generation/audit input; Qdrant is the KMS source of truth. The bundled `docker-compose.qdrant.yml` is for local development only and is not required when using the production Qdrant server.

### v0.18.1 KMS publication behavior
Capability mapping may remain `capability_needs_review=true` while evidence-approved knowledge is still published. Only the knowledge record's `needsReview=true` suppresses default production retrieval. This prevents capability uncertainty from making the entire KMS empty.
