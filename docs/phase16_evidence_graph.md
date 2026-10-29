# PIE v0.16.0 — Evidence Graph

## Goal
Connect the outputs of v0.13 Screen Identity, v0.14 Screen→API→Backend mapping and v0.15 Backend→Database mapping into one provenance-preserving graph.

## Design
`Screen -> API -> BackendEndpoint -> BackendService -> DatabaseObject` plus `SUPPORTED_BY -> Evidence` nodes.

The graph is additive and evidence-first. It does not promote weak/candidate mappings to authoritative facts. Confidence and mapping status are preserved on edges.

## CLI
```bash
python -m pie_agent.cli build-evidence-graph \
  --api-mapping outputs/phase14-api-mapping/screen_api_backend_mapping_v14.json \
  --db-mapping outputs/phase15-db-mapping/screen_api_database_mapping_v15.json \
  --screen-registry outputs/phase13-identity/screen_registry_v13.json \
  --output outputs/phase16-evidence-graph
```

## Outputs
- `evidence_graph_v16.json` — canonical graph
- `evidence_graph_index_v16.json` — adjacency index for retrieval/KMS
- `evidence_graph_report_v16.json` — graph statistics and provenance

## Principle
Discovery evidence remains separate from inference. A graph edge is a relationship with confidence and evidence, not a claim that bypasses validation/review.
