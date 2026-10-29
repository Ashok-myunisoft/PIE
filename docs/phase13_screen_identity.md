# PIE v0.13.0 — Screen Identity & Consolidation

## Goal
Convert ERP screen candidates discovered by v0.12 into a stable, deduplicated Screen Registry without treating every Angular component or development route as a confirmed ERP screen.

## Inputs
- `outputs/phase12-discovery/erp_discovery_report.json`
- Frontend evidence already captured by v0.12 (Angular components, GB form registry, form JSON, routes)
- Backend/DB evidence carried by the discovery report

## Identity rules
1. Canonical identity is based on normalized `module + feature`, not component class name.
2. Multiple artifacts for the same screen are consolidated into one registry entry.
3. Strong evidence (GB form registry, form definition, menu evidence, or equivalent) is required for `CONFIRMED`.
4. Route/component-only records remain `LIKELY` or `CANDIDATE` and enter the review queue.
5. Evidence is merged non-destructively; no source claim is silently deleted.

## Outputs
- `screen_registry_v13.json` — canonical registry
- `screen_identity_report.json` — counts and criteria
- `screen_identity_review_queue.json` — non-confirmed records requiring validation

## CLI
```bash
python -m pie_agent.cli discover-erp
python -m pie_agent.cli screen-identity --input outputs/phase12-discovery/erp_discovery_report.json
```

## Why this phase matters
Generation, source mapping, KMS and downstream agents depend on a trustworthy screen identity. v0.13 deliberately separates **discovery candidates** from **confirmed screens** before expanding technical mappings.
