# PIE v0.12.0 — ERP Source Discovery & Registry

## Purpose
Connect PIE to the real ERP source workspace and automatically discover screens, frontend components/routes, backend FastEndpoints and SQL Server metadata.

## ERP-specific observations
- Frontend is Angular 20 and uses Native Federation.
- GB5 forms are heavily menu-driven; `features/gbformviewer/gbformviewer.registry.json` is therefore a high-confidence component registry.
- Backend contains FastEndpoints under `*SL/EndPoints`.
- Database metadata is read from SQL Server; no production rows are required.

## Inputs
- `ERP_FRONTEND_PATH` — extracted frontend repository.
- `ERP_BACKEND_PATH` — extracted backend repository.
- `DATABASE_URL` or `DB_*` — schema metadata connection.

## Outputs
- `erp_discovery_report.json`
- `screen_registry.json`
- `frontend_inventory.json`
- `backend_inventory.json`
- `backend_endpoint_registry.json`
- `database_registry.json`
- `screen_source_mapping.json`
- `knowledge_manifest.json`

## Commands
```bash
python -m pie_agent.cli discover-erp
python -m pie_agent.cli generate-erp
python -m pie_agent.cli kms-publish --input outputs/phase12-generation/knowledge_manifest.json --kms outputs/kms
```

## Important behavior
Static discovery runs before LLM analysis. A component, route, endpoint or database object is evidence; AI is not allowed to invent source objects. Ambiguous API/DB mappings are marked for review.
