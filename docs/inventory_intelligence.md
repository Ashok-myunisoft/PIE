# Shared department intelligence in Qdrant

The new Inventory departmental package is persisted only in the configured Qdrant collection. The ERP sources and older pipeline artifacts remain unchanged; they are outside this migration's scope.

## Use it

Start the backend and frontend with the project's normal launchers. Open `http://localhost:5500/intelligence.html` or choose **Department Knowledge** from Home or Ask PIE. Marketing can select **Working briefs** and search **brochure**. Other departments can select their role and request task-specific context.

The page reads from the shared backend, which reads Qdrant. It does not embed knowledge, create downloads, use browser storage, or fall back to local files. API responses specify `Cache-Control: no-store`. Normal transient in-memory rendering is necessary to display the records.

For other machines, use the existing network-hosting configuration and set `frontend/config.js` to the reachable backend address. This change does not add sign-in or deployment access controls.

## Stored content

The `pie_department_intelligence` namespace uses `DepartmentKnowledge` points and one `DepartmentManifest` point per module in the configured collection. The manifest retains the active generation, package metadata, editorial input, source-review notes and approval metadata. Source provenance can retain historical paths; those references do not mean that the removed package still exists locally.

Publication writes a complete generation, reads back and hashes every record, then switches the manifest. The committed package and configuration are verified again before local originals can be removed. Interrupted writes do not switch readers to a partial generation. Prior remote generations are retained for recovery; no unrelated Qdrant records are deleted.

Source evidence, drafts and missing business inputs keep their original review status. Storing a record in Qdrant does not approve it for external publication. The shared page allows internal drafting; public context includes only explicitly approved public claims/briefs with approved dependencies. It will return no eligible records until the required approvals exist.

## API

- `GET /usage/api/intelligence/inventory/summary`
- `GET /usage/api/intelligence/inventory/records?department=marketing&kind=brief&q=brochure`
- `GET /usage/api/intelligence/inventory/context?department=marketing&q=brochure&audience=internal`

The new page is the supported route to these department records. Existing Ask PIE keeps its previous review filtering and is not guaranteed to use unpublished drafts by default. No private knowledge is sent to an external AI provider by this page or these routes.

## Commands

```powershell
$env:PYTHONPATH = "backend"
.\.venv\Scripts\python.exe -m pie_agent.intelligence intelligence-context --module inventory --department marketing --query "brochure"
```

Context is read from Qdrant and printed to the terminal. There is no local package or output-file argument. To rebuild from the existing Inventory source artifacts while retaining the editorial inputs and approvals from Qdrant:

```powershell
.\.venv\Scripts\python.exe -m pie_agent.intelligence intelligence-build --module inventory
```

The rebuilt package exists only in memory and is published/verified in Qdrant. This replaces the former local build/export workflow. Human business-input editing is not a browser feature in this change; maintain the Qdrant editorial/approval payload through controlled maintenance, and preserve source/review integrity when republishing.

## Migration audit

`outputs/verification/inventory-qdrant-migration.json` contains only migration counts, generation identity and hashes, not knowledge content. The cleanup receipt likewise records paths/counts only. The new local Inventory package, editorial/approval files, reader exports and screenshots were removed after verification. The shared reader's HTML/JavaScript are application code, not local knowledge storage.
