## v0.25.0 — All-in-one knowledge: semantic search, source code, business knowledge, documents
- **Semantic search.** Points carry two named vectors: `dense` (OpenAI `text-embedding-3-small`, meaning) and `lexical` (the v0.18 hashing vector, exact identifiers such as `MITEM`). Searches fuse both (0.7 / 0.3). `kms-migrate --target pie_knowledge_v2` copies a collection with new vectors (ids and payloads unchanged); a collection with named vectors is detected automatically, and `PIE_EMBEDDINGS=openai` creates new ones that way. Producers embed in batches, one request per ~256 texts.
- **Source code for developers** (`code_index.py`, collection `PIE_CODE_COLLECTION`, default `pie_code_v2`): every C#, TypeScript, HTML and SQL file of `data/erp` in 40-150-line chunks cut at member boundaries, with file, line range, module, layer (endpoint/bll/dal/query/dto/component/service/sql) and class. `code-index` is incremental (`outputs/code-index/code_index_manifest.json`). The Developer audience gets the real numbered lines of the files behind the screens in question plus semantic matches; answers cite `path:line`, explain the logic and propose fixes. Other audiences never receive source code.
- **Business knowledge** (`business_knowledge.py`): `business-build --module m | --all` drafts, from each feature's verified claims and field dictionary, `BusinessValue` (overview, users, benefits, use cases, industries), `HowTo` steps, `FAQ`s and `MarketingCopy`; per module a `ModuleBrochure` and end-to-end `Process`es (steps must name real screens of the module; others are dropped). Drafts are cached by fact fingerprint (`phase21-business/business_cache.json`); claim words absent from the evidence get a review note. `kms-publish-business` publishes them; the chat marks them unconfirmed.
- **Documents** (`docs_ingest.py`): `docs-ingest --path data/docs` reads PDF, Word, PowerPoint, Excel, Markdown, text and HTML (also inside .zip), takes the module from the folder name (`data/docs/<module>/`) or the content, and stores `Document` passages with page/slide numbers. Re-ingesting a file replaces its passages.
- **Chat**: new intents `content` (brochures, posts, emails, web copy; ends with "To confirm before publishing"), `howto`, `support`, `process` and `code`, each with its own retrieval route and answer guide. A screen's how-to, FAQs and business value are no longer collapsed into one item. `/usage/api/chat/status` reports the search mode and the code collection.

## v0.24.0 — Chat sessions (New chat, previous chats)
- Ask PIE has a chat sidebar: **+ New chat**, search (titles and message text), chats grouped Today / Yesterday / Previous 7 and 30 days / Older, each with its title, modules, audience and last update. Click a chat to reopen the whole conversation and continue it; rename (✎ or click the title), archive (🗄, "Show archived chats" to restore) or delete (🗑).
- Every chat is a stored session (`backend/pie_agent/sessions.py`, SQLite `outputs/sessions/pie_sessions.db` or `PIE_SESSION_DB`): session id, owner, title, modules, audience (incl. client type), created/updated times and the full messages with each answer's sources and trace.
- **Isolation:** a question in a chat is answered with that chat's own stored history only; the browser's copy is ignored, so switching chats, modules or audiences never mixes conversations. A continued chat keeps its audience unless a new one is chosen; opening a chat restores its modules and audience in the selectors.
- **Titles** are generated from the first question as "<module or audience> – <topic>" (one short AI call, else from the question's words), e.g. "ESS – Leave Request Process".
- **Owner:** the signed-in user passed by the web server in `X-PIE-User` (e.g. nginx `proxy_set_header X-PIE-User $remote_user;`), else a per-browser id. Users only see and open their own chats. A conversation kept in the browser by earlier versions is imported once as a saved chat.
- API: `POST /usage/api/chat` takes `sessionId` or `newSession: true` (+ `userId`) and returns `session`; `GET/POST /usage/api/sessions`, `GET/PATCH/DELETE /usage/api/sessions/{id}`.

## v0.23.1 — All ERP modules built like ESS and Inventory
- Phases 14–20 for every module in the screen registry (46 modules, 1,310 screens): screen → API mapping, API → database mapping (live SQL Server), evidence graph, Usage catalog with a capability taxonomy per module (`MODULE_TAXONOMY`, readable `MODULE_NAMES`), and section + field knowledge with AI explanations for every form screen (587 forms), all published to Qdrant. Inventory now has all 52 form screens.
- `map-api --modules a,b,…` and `map-db --modules …` scan the ERP source and read the database once for many modules (65 s + 394 s for all 46, instead of ~10 min per module); results are identical per module except ties between equally scored low-confidence API candidates.
- `build_modules.ps1 -Modules … [-SkipMapping] [-FieldsOnly] [-Forms a,b] [-NoEnrich]` runs the pipeline per module; `PIE_LOG_AUTO_REFRESH=0` skips the per-command inventory/Qdrant snapshot during batch builds; AI field explanations run `PIE_AI_WORKERS` (default 4) batches at once.
- Field extraction handles more form-definition shapes (fields without a name, grids given as lists/objects/`true`); the module field summary is rebuilt from every dictionary in the folder, so partial rebuilds keep totals complete. Relationship lookups are cached (0.1 s per screen with all 46 graphs).

## v0.23.0 — Answers written for the reader (user context / persona)
- Ask PIE has a **For** selector: Marketing Team (default, unchanged behaviour), Client (Non-technical or Technical), Developer, Stakeholder. It stays for the whole conversation until changed, is shown on every answer ("for: Developer") and in the trace, and can be set by link (`chat.html?audience=developer`, `client_technical`, `client_nontechnical`, `stakeholder`).
- The audience changes only presentation: the answer's introduction, the rule on technical names, the style/length and what is prioritised. The grounding rules (use only the numbered knowledge items, cite every fact, never invent) are identical for every audience, and the knowledge retrieved is the same.
- Technical audiences (Technical client, Developer) additionally get the screen → API → backend class → table chain from the relationship graph, plus the file/query evidence of each item, so technical answers cite real code facts.
- For non-Marketing audiences, feature questions ("What does Inventory provide?") are written for the reader instead of returning the fixed Marketing feature list; an explicit "list" request still gets the list.
- API: `POST /usage/api/chat` takes `audience`; the response has `audience`. CLI: `chat --audience developer`. The Activity Log shows each question's audience.

## v0.22.2 — Complete business context on every knowledge point
- Field and section points now carry their screen's `functionality` (screen registry, Phase 13) and `capability`, `capability_basis`, `capability_needs_review` (Usage catalog, Phase 19), so every point holds the whole chain product → module → functionality → capability → feature → screen → section → field. Usage points (module, capability, feature) also get `functionality`. `kms-publish-fields` and `kms-publish-usage` join this automatically (`screen_contexts`).
- Display fields are linked to the ID field that is stored: e.g. Department Name → `DepartmentId` → `MEMPLOYEE.DEPARTMENTID` (FK `MDEPARTMENT`). The link (`database.storedVia`, payload `field_column` / `field_stored_via`) comes from the picklist link field or the ID field of the same name; 192 ESS/Inventory fields linked, 40 of them to a stored column (the rest are filter pickers on list/report screens).
- ESS (966 fields, 29 sections, 67 usage points) and Inventory (237 fields, 24 sections, 96 usage points) republished; no field point has an empty functionality or capability.

## v0.22.1 — Choose one, several or all modules
- A shared module picker (`frontend/modules.js`) on Ask PIE, Feature Catalog, Field Explorer and Relationships: tick one module, any combination, or none for all modules. The selection is kept in the address (`?modules=ess,inventory`); **Copy link** gives a link that opens the page with the same modules.
- The chat takes `modules: [...]` (the single `module` still works; the CLI accepts `--module ess,inventory`). The selection is the answer's scope: retrieval, field matching and named screens stay inside it, and a question naming some of the selected modules narrows to those.
- The Feature Catalog merges the selected modules' catalogs into one view (features show their module).
- Exported single-file pages carry the picker inline.

## v0.22.0 — See how PIE answers: traces, relationships, activity log
- **How PIE answered.** Every chat response carries a `trace`: the steps (understand, plan, retrieve, coverage, answer, validate) with what each decided, every knowledge item the answer could use (score, retrieval route, cited or not), and the exact text given to the AI. Ask PIE shows it under each answer.
- **Relationships page** (`relations.html`, `/usage/api/relations/...`): for each screen, the APIs it calls, the backend endpoints and services behind them, the tables they read or write, the evidence for every link (form-action URL, SQL, DDL file), which of the screen's fields are stored in which table, other screens using the same tables, and the Qdrant knowledge held for the screen. Built from the Phase 16 evidence graphs.
- **Activity log** (`activity_log.py`, `logs_api.py`, `logs.html`): every CLI command and chat/search request is a run with status, timestamps, duration, record counts, masked parameters and errors with tracebacks; chat runs keep their answer trace. Also API request stats, Qdrant storage status (per module and knowledge type) and a module/screen/field knowledge inventory. SQLite plus a rotating JSON-lines file; `PIE_LOG_REMOTE` sends runs from another PC to the server. CLI: `python -m pie_agent.activity_log summary|tail|show|qdrant-status|inventory|prune|flush-remote`.

## v0.21.0 — ESS module (module → screen → section → field knowledge)
- The ESS module now goes through the same pipeline as Inventory: module mapping (Phases 14–16), a Usage catalog with an ESS capability taxonomy, and field-level knowledge for all 27 ESS form screens.
- Added `fields-build-module`, which builds field dictionaries for every form screen of a module with one ERP-wide usage scan. `kms-publish-fields --module` publishes them.
- The field extractor now discovers the backend entity (form-action URL, main form, or shared form), reads literal label text, detects sections (headers, headings, legends, panels, `<!-- … Section -->` comments), accepts flexible query and DAL naming (`CustomeCode`), and finds report queries of any entity.
- Added `SectionKnowledge` points (one per screen tab/section) to Qdrant.
- The chat recognises the screen named in a question. It spreads a field that exists on several screens, answers section questions, and gives an overview of every screen for "all fields" questions when no screen is named.
- The Field Explorer groups screens by module. Its filter lists tabs or sections (ESS screens use sections), then "Main form" or "Not shown on screen".
- `map-db --db-snapshot <registry>`: when SQL Server cannot be reached, Phase 15 uses a registry saved from an earlier live scan, which keeps column types, nullability and keys, before falling back to SQL scripts (names only). ESS was mapped this way from the Inventory live scan of 2026-09-26, so all 318 ESS fields mapped to a column carry database type information.

## v0.20.1 — Separate frontend and backend
- The Python code moved to `backend/` (`backend/pie_agent`, `backend/tests`, `backend/requirements.txt`). The backend serves the API only, and `/` returns service info.
- The web pages in `frontend/` form a standalone static project. The backend address is set in `frontend/config.js`, and pages link as `chat.html`, `catalog.html` and `fields.html`.
- Added `start_backend.ps1` (API on port 8000, CORS set for the frontend), `start_frontend.ps1` (pages on port 5500, `-Backend` writes `config.js`) and `pie.ps1` (CLI wrapper). `start_pie.ps1` was removed.
- `.env`, `outputs/` and the frontend templates are found from the project root, whatever the working directory. Tests run from any folder.

## v0.20.0 — Field-level intelligence + PIE web app
- Added field-level extraction (`fields-build`) for GB5 form screens, starting with Item Master: 237 field definitions (236 unique). It covers labels, types, allowed values, defaults, mandatory rules (including conditional ones), validations and messages, dependencies, formulas, lookups, database and backend/frontend mapping, ERP-wide usage and reports. Every fact has file and line evidence.
- Added AI explanations (`--enrich`: purpose, meaning, usage, impact, example, business rules). They use only the extracted facts, are cached by fact fingerprint, and are checked afterwards: explanations that name modules absent from the evidence get a review note and lower confidence.
- Source defects are reported: a field defined twice (`ItemShortName`), and labels borrowed from other screens' translations.
- Added `kms-publish-fields`, which publishes one `FieldKnowledge` point per field to Qdrant. Ask PIE now answers field questions, brings in the fields a change affects, answers "all fields" questions deterministically, and never passes unverified usage or impact text to the model.
- Web app: a `frontend/` folder (Home, Ask PIE, Feature Catalog, Field Explorer) served by the FastAPI backend, with shared navigation, `start_pie.ps1`, a field API with Excel download, and a standalone Field Explorer export (`fields-export-html`). The catalog moved from `/usage/` to `/usage/catalog`.

## v0.19.0 — Ask PIE: Marketing Intelligence chat
- Added a chat UI (`/usage/chat`) and API (`POST /usage/api/chat`) that answer Marketing questions from the Qdrant KMS with OpenAI. It covers feature discovery, industry targeting, demo preparation, differentiation, market gaps and ticket analysis.
- Answers are grounded: numbered knowledge items, validated `[K#]` citations, a grounding badge, explicit "not in the knowledge base" notes, and a warning for risky claim words not found in the knowledge. Ticket questions without ticket data never call the LLM.
- Added `kms-publish-usage`, which publishes Usage catalogs (module, capability and feature points with verified and review claims) to Qdrant. Inventory is published: 96 points.
- Added `kms-import-tickets`, which imports from CSV/Excel/JSON or a read-only SQL `SELECT`, with column auto-detection and e-mail/phone redaction. Added a `config/tickets_gb5_crm_calls.sql` preset.
- Added a `chat` CLI command. The LLM providers gained `chat()` and request timeouts.

## v0.19.0 — Usage Phase (Marketing) + mapping fixes
- Added the Usage Phase (`usage-build`, `usage-export`, `/usage/` UI, `/usage/api/*`): an evidence-backed feature catalog per module for Marketing, with Verified vs Needs-review claims, readiness and candidate capabilities.
- Phase 14: endpoint dependencies are read from `private readonly` fields. Before, 5 of 3,596 endpoints had dependencies; now 3,588 do.
- Phase 14: GB5 form-action URL maps (`GetUrl`/`SaveUrl`/`DeleteUrl`) now map form screens to endpoints. Legacy `.svc` actions with no endpoint are reported as `unresolvedFormActions`.
- Phase 14: Angular `form.get('Field')` is no longer mistaken for an HTTP call. Endpoints now record `module` and `entity`, and module aliases were added (`inventory` → `MM`).
- Phase 13: spacing and module-prefix variants of one screen now merge, with `aliasScreenIds` kept. Status comes from evidence only: the fixed 0.78 prior was removed, so a component alone is CANDIDATE.
- Phase 15: follows the endpoint → BLL → DAL → query-builder chain to reach SQL.
- Phase 16: backend nodes are keyed by endpoint file. Phase 17: evidence bundles follow outgoing edges only, so they no longer leak across screens.
- API: fixed a missing `import os`, added `python-multipart`, and sanitized upload filenames. Fixed Python 3.11 compatibility and enabled the Azure OpenAI provider in Phase 17. Qdrant collection checks are cached, and `scroll` was added.

## v0.18.1 — KMS publication/search correction
- Capability uncertainty no longer suppresses evidence-approved knowledge from Qdrant.
- `publication_status` is now driven by the knowledge record's `needsReview` flag, not by derived capability review or package status.
- Added `knowledge_review_required` to distinguish claim review from capability review.
- Existing deterministic point IDs are preserved, so rerunning `kms-publish` updates the existing v0.18.0 points instead of creating duplicates.

## v0.18.1 — External Qdrant HTTP fix
- Fixed `kms-init` failure caused by an invalid `QdrantHTTP.client` attribute reference.
- Payload indexes now call the Qdrant HTTP wrapper directly when supported.

# PIE Changelog

## 0.15.0
- Added Backend → Database mapping.
- Added SQL Server live metadata authority with SQL DDL fallback.
- Added C#/SQL database reference discovery for tables, views, procedures, EF DbSet/entity references, and embedded SQL.
- Added API → Database and Screen → API → Database evidence mappings.
- Added database mapping confidence levels and explicit mapped/unmapped states.
- Added `map-db` CLI and `/erp/map-db` FastAPI endpoint.
- Preserved all Phase 14 and earlier capabilities and regression tests.

# PIE Changelog

## 0.14.0
- Added Screen → API → Backend Service mapping.
- Added FastEndpoints route discovery with constructor-injected BLL/service dependency extraction.
- Added Angular/TypeScript HTTP call discovery.
- Added BLL/service implementation cataloging.
- Added evidence-scored screen/API mappings and explicit confidence levels.
- Added API mapping report and JSON registries.
- Added `/erp/map-api` FastAPI endpoint.
- Preserved all Phase 13 and earlier capabilities and regression tests.

## 0.13.0
- Screen Identity & Consolidation.

## 0.12.0
- ERP Source Discovery & Registry.

## v0.16.0 — Evidence Graph
- Added provenance-preserving Evidence Graph builder.
- Connected Screen → API → Backend Endpoint → Backend Service → Database Object.
- Added evidence/source nodes and confidence/status-preserving edges.
- Added adjacency index and graph report for downstream retrieval/KMS.
- Added `build-evidence-graph` CLI command and regression tests.

## 0.17.0
- Added ERP-wide evidence-grounded AI knowledge generation from the Phase 16 Evidence Graph.
- Added per-screen evidence bundles and deterministic fallback generation.
- Added OpenAI generation path with strict evidence ID validation.
- Added review gating for inferred, unsupported, or fallback-generated knowledge.
- Added `generate-ai-knowledge` CLI and `/erp/generate-ai-knowledge` API.


## 0.18.0
- Added explicit Functionality → Capability → Feature → Screen model.
- Added capability candidate/review semantics without an LLM rerun.
- Added Qdrant authoritative KMS over HTTP with idempotent versioned point IDs.
- Added local zero-API-cost deterministic embeddings for v0.18 ingestion.
- Added `kms-init`, `kms-publish`, `kms-search`, `kms-stats`, and `capability-model` CLI commands.
- Added FastAPI Qdrant KMS health, stats, get and search endpoints.
- Existing v0.17 knowledge is consumed from `outputs/phase17-ai-knowledge/knowledge/`.
