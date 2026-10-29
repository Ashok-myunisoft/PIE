# Phase 15 — Backend → Database Mapping

## Goal
Connect the Phase 14 Screen → API → Backend chain to actual database objects.

```text
Screen → API → Endpoint → Service/BLL → DB reference → SQL Server object
```

## Sources
1. Live SQL Server metadata via `DATABASE_URL` (authoritative when connected).
2. Backend C#/SQL source references.
3. SQL DDL/migrations as an offline fallback when live DB metadata is unavailable.

## What is discovered
- Tables/views/procedures from live metadata.
- `FROM`, `JOIN`, `UPDATE`, `INSERT`, `DELETE`, `EXEC` references.
- EF `DbSet<TEntity>` and entity/table attributes.
- Embedded SQL in C# strings.
- Screen/API database mappings.

## Commands
```bash
python -m pie_agent.cli map-db
```

Use an explicit API mapping file if required:
```bash
python -m pie_agent.cli map-db --api-mapping outputs/phase14-api-mapping/screen_api_backend_mapping_v14.json
```

## Safety
PIE does not execute ERP application queries. The DB connector reads metadata only. Production data is not required for Generation Phase.
