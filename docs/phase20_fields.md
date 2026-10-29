# v0.20 — Field-level intelligence (Item Master) and the PIE web app

## The web app: separate frontend and backend

The two parts run separately:
- **Backend** (`backend/pie_agent`, FastAPI) serves the API only, on port 8000.
- **Frontend** (`frontend/`, static HTML/JS with no build step) serves the pages, on port 5500.

The frontend finds the backend through `frontend/config.js` (`apiBase`). The backend lets the frontend call it through the origins listed in `PIE_CORS_ORIGINS`; `start_backend.ps1` sets these for you.

```powershell
.\start_backend.ps1                    # API        http://localhost:8000  (docs: /docs)
.\start_frontend.ps1                   # pages      http://localhost:5500
# for other PCs:
.\start_backend.ps1 -Network
.\start_frontend.ps1 -Network -Backend http://<this-PC-IP>:8000
```

| Page (frontend) | URL | What it is for |
|---|---|---|
| Home | `http://localhost:5500/` | Knowledge-base status and entry points |
| Ask PIE | `/chat.html` | Chat that answers from the Qdrant KMS, with sources |
| Feature Catalog | `/catalog.html` | Marketing feature catalog per module |
| Field Explorer | `/fields.html` | Every field of a screen, with all attributes and evidence |

| Backend | URL |
|---|---|
| Service info | `http://localhost:8000/` |
| API docs (Swagger) | `http://localhost:8000/docs` |
| Endpoints | `/usage/api/chat`, `/usage/api/modules`, `/usage/api/catalog/{module}`, `/usage/api/fields/...`, `/kms/...` |

Shareable single files are written to `outputs/usage/`:
- `usage-export` writes the catalog.
- `fields-export-html` writes the Field Explorer.
- `usage-export-chat --server URL` writes the chat, which needs the server running.

## Field-level knowledge

```powershell
python -m pie_agent.cli fields-build --enrich          # Item Master (defaults)
python -m pie_agent.cli kms-publish-fields --dictionary outputs/modules/inventory/phase20-fields/itemmaster_fields_v20.json
python -m pie_agent.cli fields-export-html
```

For another GB5 form screen, pass its names: `--form <formjson name> --module <frontend project> --entity <backend entity> --screen-id ... --screen-name ...`.

Outputs are in `outputs/modules/<module>/phase20-fields/`:
- `<form>_fields_v20.json`: the field dictionary, one record per field definition.
- `<form>_fields_v20.xlsx`: every attribute as a column, with an About sheet.
- `<form>_explanations_cache.json`: cached AI explanations, so re-runs don't pay again.

### Where each attribute comes from

| # | Attribute | Source |
|---|---|---|
| 1-3 | Field name, label, technical name | formjson `ObjectFields`, `i18n/English.json`. Hidden ID fields take the label of the lookup that fills them. |
| 4-5 | Purpose, what it represents | AI-derived from the facts below (labelled) |
| 6 | Business usage | AI-derived from ERP-wide usage evidence (labelled, checked) |
| 7 | Field type | component HTML control, formjson type, DTO property type, DB column type |
| 8 | Allowed values | formjson `PickLists`, metaform quick actions |
| 9 | Default | formjson `DefaultValue` |
| 10 | Mandatory | formjson `Required` and `RequireBasedOn…`, BLL `_Validation.NotEmpty`, DB `NOT NULL` |
| 11 | Validations | formjson (required, max length, regex, percentage), BLL, DB length / NOT NULL / foreign key |
| 12 | Dependencies | conditional-required rules, `*ngIf` visibility, read-only bindings, lookup link fields, change handlers that update other fields |
| 13 | Calculation | metaform `rules` (type `calculate`) and component TypeScript |
| 14 | Data source | picklist definition (`allpicklist.json`) and lookup joins |
| 15 | Database mapping | `INSERT`/`UPDATE` parameter positions, `GET` query aliases and joins, live DB metadata snapshot. Grids map to their child table. |
| 16 | Backend mapping | DTO property and file:line, SAVE/UPDATE/GET queries, endpoints (Phase 14), BLL |
| 17 | Frontend mapping | component control, template line, events and handlers, formjson / metaform |
| 18 | Related masters / modules | foreign keys, lookup joins, picklist sources, ERP-wide column references per module |
| 19 | Impact on transactions | AI-derived from usage and dependency facts. Withheld from the chat when it isn't verified. |
| 20 | Example | AI-derived, consistent with type, length and allowed values |
| 21 | Business rules | AI summary of validations, dependencies and calculations |
| 22 | Messages | frontend save alert (`<Label> Should not be Empty`, percentage message), backend `Validation.cs` messages |
| 23 | Configuration dependency | Code Define generation type, read-only switches |
| 24 | Reports / documents | report query builders and report paths that reference the column |
| 25 | Related fields | every field named in the dependencies |

Every record carries an `evidence` list: file, line and fact.

### Quality controls

- **Unverified module mentions.** AI text is checked after generation. If it names a module that the field's usage evidence doesn't show, it gets a review note and its confidence is lowered. The chat never receives that usage or impact text; the Field Explorer shows it with the warning.
- **Source defects are reported, not hidden.**
  - A field defined twice (`ItemShortName`, entries 58 and 63) keeps both records and flags the conflict.
  - Labels borrowed from another screen's translations (for example `AccountMaster.Category` shown as "Account Category" on Item Master) carry a label note.
- **"All fields" questions in the chat are answered without an LLM,** straight from the dictionary, so counts and lists are exact.

### Item Master results

| Measure | Count |
|---|---|
| Field definitions | 237 (236 unique) |
| With a real label | 224 |
| Stored in `MITEM` | 168 |
| Shown from related masters | 50 |
| Grid rows (`MITEMSTOCK`) | 1 |
| Lookups | 53 |
| With fixed options | 45 |
| Mandatory | 16 |
| Calculated | 3 |
| With dependencies | 96 |
| Used by other modules | 70 |
| AI explanations with a review note | 89 of 237 |

## Any module, every screen (ESS done the same way)

These are the steps for a whole module. ESS used exactly these commands:

```powershell
$M = "outputs/modules/ess"
# identity (shared ERP-wide registry) -> screen/API -> API/DB -> evidence graph
.\pie.ps1 map-api --registry $M/phase13-identity/screen_registry_v13.json --module ess --output $M/phase14-api-mapping
.\pie.ps1 map-db --api-mapping $M/phase14-api-mapping/screen_api_backend_mapping_v14.json --output $M/phase15-db-mapping
#   SQL Server unreachable? add: --db-snapshot outputs/modules/inventory/phase15-db-mapping/database_object_registry_v15.json
#   (a registry from an earlier LIVE scan; the SQL-script fallback has no column types)
.\pie.ps1 build-evidence-graph --api-mapping $M/phase14-api-mapping/screen_api_backend_mapping_v14.json --db-mapping $M/phase15-db-mapping/screen_api_database_mapping_v15.json --screen-registry $M/phase13-identity/screen_registry_v13.json --output $M/phase16-evidence-graph
# module / capability / screen knowledge (Usage catalog)
.\pie.ps1 usage-build --module ess
.\pie.ps1 kms-publish-usage --module ess
# section + field knowledge for EVERY form screen of the module
.\pie.ps1 fields-build-module --module ess --enrich
.\pie.ps1 kms-publish-fields --module ess
.\pie.ps1 fields-export-html
```

What `fields-build-module` adds on top of the single-screen build:
- **Entity discovery.** The form-action URLs give the backend entity (`leaverequest` → `/prs/TLeave.svc/` → `TLeave`). Screens without their own URL use the component's main form, or the one form they share. For example, on-duty hour-wise uses `permissionrequest`, which saves to `TimeSlip`. When no entity exists, the dictionary reports "backend not found" rather than guessing.
- **Labels.** Translation keys are resolved through `English.json`. Literal label text in the form definition (ESS style, e.g. `"Employee Name*"`) is used as written.
- **Sections.** They come from screen tabs, `section-header` divs, headings, legends, expansion panels and `<!-- … Section -->` comments.
- **One usage scan.** A single ERP-wide scan covers all tables, columns and field names of the module, instead of one scan per screen.
- **Knowledge levels in Qdrant:**

  | Level | Knowledge types |
  |---|---|
  | Module | `UsageModule` |
  | Capability | `UsageCapability` |
  | Screen | `UsageFeature` |
  | Section | `SectionKnowledge` (new) |
  | Field | `FieldKnowledge` |

Outputs in `outputs/modules/<module>/phase20-fields/`:
- `<form>_fields_v20.json` and `.xlsx` for each form screen
- `module_fields_summary_v20.json` with a row per screen and module totals

### Chat questions it answers

- "What is the Saleable Item field?" / "Why is Item Code required?"
- "What are the allowed values for …?" / "Which database column is mapped to …?"
- "What happens if I change the Fixed Lead Time?" (it also brings in the fields the change affects)
- "Which modules depend on …?" / "What validation is applied to …?"
- "I need all the features of each field in Item Master." (a structured table for all 237 fields, plus a pointer to the Field Explorer and Excel file for every attribute)

API: `GET /usage/api/fields`, `GET /usage/api/fields/{screen}`, `GET /usage/api/fields/{screen}/excel`.
