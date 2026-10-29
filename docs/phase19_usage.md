# v0.19 — Usage Phase (Marketing Feature Catalog)

The Usage Phase is the first consumer of PIE knowledge. It turns the evidence
chain for one module into a feature catalog that the Marketing team can browse,
search, copy from and export. No LLM is called.

```text
v0.13 identity ─┐
v0.14 Screen→API ├─► usage-build ─► usage_catalog_v19.json ─► Marketing UI (/usage/)
v0.15 API→DB    ─┤                  usage_feature_sheet_v19.csv   or standalone HTML
v0.17 knowledge ─┘  (local JSON or Qdrant KMS)
```

## Hierarchy

`Product → Module → Capability → Feature (screen)`

## Claim policy

Every statement shown to Marketing carries a status.

| Status | Source | Marketing use |
|---|---|---|
| **Verified** | Form definition fields, `_Validation.NotEmpty` rules, business-rule exception messages, AutoNumber usage, form-action-mapped endpoints (≥ 0.80), exact database matches (≥ 0.85) | OK for copy with normal review |
| **Needs review** | Name-based table matches, AI-drafted descriptions (v0.17) | Product sign-off before publishing |

Other rules:
- **Tables.** A table the feature writes to (INSERT/UPDATE/DELETE) counts as its own records. A table it only reads counts as shared master data. Platform tables are left out of claims: `MCLIENT`, `MUSER`, `MENTITY`, and any table used by more than 40% of mapped screens.
- **Readiness.** `READY` means a CONFIRMED screen with at least 2 verified highlights. `PARTIAL` means at least 1 verified highlight. `NEEDS_EVIDENCE` means none.
- **Capabilities** come from a per-module keyword taxonomy and are labelled *candidate*. To confirm one, add it to `capability_map.json`. The map can be keyed by the screen ID or any of its aliases:

```json
{ "INVENTORY_ITEM_CATEGORY": { "capability": "Item & Catalogue Management", "basis": "human_confirmed" },
  "INVENTORY_DASHBAORD": { "capability": "Reports & Analytics", "basis": "human_confirmed", "name": "Inventory Dashboard" } }
```

The optional `name` corrects a display name, for example a typo in the ERP source.

- **Migration gaps.** Some form actions still point to legacy `.svc` services that have no FastEndpoints equivalent. These are listed as migration gaps instead of being guessed at.

## Run for one module (Inventory)

The module pipeline writes to `outputs/modules/<module>/` and leaves the ERP-wide outputs untouched.

```powershell
$M="outputs/modules/inventory"
python -m pie_agent.cli screen-identity --input outputs/phase12-discovery/erp_discovery_report.json --output $M/phase13-identity
python -m pie_agent.cli map-api --registry $M/phase13-identity/screen_registry_v13.json --module inventory --output $M/phase14-api-mapping
python -m pie_agent.cli map-db --api-mapping $M/phase14-api-mapping/screen_api_backend_mapping_v14.json --output $M/phase15-db-mapping
python -m pie_agent.cli build-evidence-graph --api-mapping $M/phase14-api-mapping/screen_api_backend_mapping_v14.json --db-mapping $M/phase15-db-mapping/screen_api_database_mapping_v15.json --screen-registry $M/phase13-identity/screen_registry_v13.json --output $M/phase16-evidence-graph
python -m pie_agent.cli usage-build --module inventory
```

To read the knowledge from the Qdrant KMS instead of the local v0.17 JSON, add `--knowledge-source qdrant`.

## Marketing UI

Served by the PIE API:

```powershell
uvicorn pie_agent.api:app --port 8000
# open http://localhost:8000/usage/catalog   (Home page: http://localhost:8000/)
```

Standalone file to share, with no server needed:

```powershell
python -m pie_agent.cli usage-export --output outputs/usage/pie_usage_marketing.html
```

What the UI offers:
- A capability overview showing how ready each capability is.
- Feature cards with filters by capability, readiness and screen type, plus search.
- A detail panel showing each claim with its source evidence.
- **Copy verified blurb**, which copies verified claims only. The AI-drafted description is added only if you tick the box.
- CSV feature sheet export.
- Shareable deep links, e.g. `?module=inventory&tab=features&feature=INVENTORY_BOM`. The **Copy link** button in the detail panel builds one.
- Light and dark themes, and a layout that works on mobile.

API: `GET /usage/api/modules`, `GET /usage/api/catalog/{module}`.

## Ask PIE: the Marketing chat

The Marketing team asks questions in plain English. PIE answers only from the Qdrant KMS and shows the sources it used.

```text
Chat UI (/usage/chat) → POST /usage/api/chat → PIE chat engine → Qdrant → OpenAI → answer + sources
```

How each question is handled ([pie_agent/chat.py](../pie_agent/chat.py)):

1. **Understand.** PIE detects the intent (feature discovery, industry targeting, demo preparation, differentiation, market gaps or tickets), plus the module, industry and persona named in the question.
2. **Plan.** For industry, demo and positioning questions, the LLM turns the question into ERP search terms. For example, "pump industry" becomes bill of materials, quality inspection, production planning. For an IT Manager, it adds configuration, user roles and integration.
3. **Retrieve.** PIE searches Qdrant, preferring verified Usage features, and adds aggregates computed over the whole KMS: module and capability lists, ticket statistics and internal coverage gaps.
4. **Check coverage.** PIE records what the KMS cannot answer, such as industry-specific, competitor, market or ticket knowledge. The answer then says so.
5. **Answer.** The LLM writes marketing copy from numbered knowledge items only, and cites them as `[K3]`.
6. **Validate.** Citations that don't match a supplied item are removed. Risky claim words that appear in none of the items, such as "real-time" or "dashboard", are flagged with *Check before use*.

Guarantees:
- **Ticket questions without ticket data never reach the LLM.** The answer explains how to import tickets.
- **No knowledge means a "not found" answer.** The model never answers from its own knowledge.
- **Every answer shows a grounding badge:** *Grounded*, *Partly grounded* (some knowledge is missing), *No citations* or *Not found*.

### Setup

```powershell
# 1. Publish the module's usage knowledge to Qdrant (idempotent)
python -m pie_agent.cli kms-publish-usage --module inventory

# 2. Start PIE and open the chat
uvicorn pie_agent.api:app --port 8000
# http://localhost:8000/usage/chat
```

The chat uses `PIE_CHAT_LLM_PROVIDER`, or `PIE_LLM_PROVIDER` if that isn't set (`openai`, `azure_openai` or `offline`). In `offline` mode, answers are grouped lists of the retrieved knowledge, with no LLM involved.

### Shareable chat file

The chat can also be handed out as one HTML file, like the Feature Catalog. The file holds only the PIE server address; the OpenAI key and Qdrant access stay on the server.

```powershell
# On the machine that runs PIE (listen on the network, not only localhost)
uvicorn pie_agent.api:app --host 0.0.0.0 --port 8000

# Create the file with an address users' browsers can reach
python -m pie_agent.cli usage-export-chat --server http://192.168.0.90:8000
# -> outputs/usage/pie_ask_marketing.html
```

Things to know:
- **Windows Firewall** must allow inbound TCP on port 8000 on the PIE machine.
- **Cross-origin access.** The server accepts calls from pages opened from disk and from itself. Set `PIE_CORS_ORIGINS` (comma-separated) to allow other origins, for example an intranet site that hosts the file.
- **No login yet.** The chat has no access control, so anyone who can reach the server can ask questions, and each one uses OpenAI credit. Keep it on the internal network.

You can also ask from the command line:

```powershell
python -m pie_agent.cli chat --question "What features should I highlight for the pump industry?"
```

### Tickets

Import tickets from an export file, or from any read-only SQL `SELECT`:

```powershell
python -m pie_agent.cli kms-import-tickets --file tickets.xlsx --source redmine --dry-run
python -m pie_agent.cli kms-import-tickets --sql-file config/tickets_gb5_crm_calls.sql --source gb5-crm --dry-run
```

- **Column detection.** Columns are detected by name: ticket id, subject/title, description, module, status, priority, category, created date and resolution. Use `--mapping map.json` (e.g. `{"title": "Issue Summary"}`) for other names.
- **Personal data.** E-mail addresses and phone numbers are redacted, and reporter names or contacts are never stored.
- **Read-only SQL.** The SQL importer accepts one `SELECT`/`WITH` statement only, and connects with read-only intent.
- **Try it first.** Drop `--dry-run` to publish. Re-importing the same tickets updates them in place.

`config/tickets_gb5_crm_calls.sql` reads GoodBooks CRM Service calls (`TCALL`). Status, priority and nature come out as numeric IDs, so join their master tables for readable values before a real import. The `BASICTEST` database holds only 3 test calls, which is why none were imported.

API: `POST /usage/api/chat` with `{"message", "history", "module", "includeReview"}`, and `GET /usage/api/chat/status`.
