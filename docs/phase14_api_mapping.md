# PIE v0.14.0 — Screen → API → Backend Service Mapping

## Purpose
Phase 14 extends the complete PIE codebase from Phase 13. It maps the canonical Screen Registry to backend APIs/endpoints and then to the service/BLL implementation layer.

## Input
- Phase 13 `screen_registry_v13.json`
- ERP Angular frontend source directory
- ERP C# backend source directory

## Processing
```text
Screen Registry
      ↓
Frontend HTTP/API evidence
      ↓
FastEndpoints route
      ↓
Endpoint class
      ↓
Injected BLL/service
      ↓
Implementation + method
```

## ERP-specific support
The backend mapper understands the FastEndpoints pattern used by the supplied ERP source, including `Get/Post/Put/Patch/Delete("/route")`, constructor-injected `IBLL`/service dependencies, and calls such as `_bll.SomeMethod(...)`.

The frontend scanner captures Angular/TypeScript HTTP calls such as `http.get/post/put/patch/delete(...)` and service-based HTTP calls.

## Evidence policy
- Exact frontend URL ↔ backend route matches are high-confidence.
- Module/name/token matches are candidate evidence and remain below exact confidence.
- Service implementation matches are separately recorded.
- Unmapped screens are retained rather than removed.
- PIE does not execute ERP APIs during discovery.

## Command

```bash
python -m pie_agent.cli map-api \
  --registry outputs/phase13-identity/screen_registry_v13.json
```

Windows CMD:

```cmd
python -m pie_agent.cli map-api --registry outputs/phase13-identity/screen_registry_v13.json
```

## Outputs
- `api_endpoint_registry_v14.json` — endpoint and injected service catalog
- `frontend_api_call_registry_v14.json` — frontend HTTP evidence
- `backend_service_registry_v14.json` — service/BLL implementation catalog
- `screen_api_backend_mapping_v14.json` — screen-level mappings
- `api_mapping_report_v14.json` — counts and quality statistics

## Interpretation
`MAPPED` does not mean every mapping is exact. Always inspect `confidence`, `confidenceLevel`, `reasons`, `frontendEvidence`, and `serviceImplementations` before publishing the mapping as authoritative knowledge.
