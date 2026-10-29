# Phase 11 — Knowledge Generation Foundation

## Goal

Move PIE from a manually prepared single-screen input toward an automated Generation Phase:

```text
ERP sources
  -> screen discovery
  -> screen registry
  -> source mapping
  -> evidence-backed knowledge generation
  -> functionality / feature organization
  -> KMS-ready packages
```

## What Phase 11 does

- Accepts a ZIP, directory, or file as input.
- Normalizes source paths so Windows and Linux paths behave consistently.
- Discovers likely ERP screens from screen artifacts, frontend source, and explicit route/component declarations.
- Avoids treating prompts, generated summaries, reports, and training text as screens unless they contain explicit discovery signals.
- Creates a stable screen ID based on functionality + feature so multiple artifacts can merge into one screen.
- Maps relevant source files to each discovered screen.
- Generates a KMS-ready knowledge package per screen.
- Preserves evidence and review flags from the existing Leave intelligence pipeline.
- Creates a functionality -> feature -> screen index for KMS organization.
- Provides a local JSON KMS adapter for development. This is a storage boundary, not a claim that it is the production KMS.

## Commands

```powershell
# Discover screens and generate the registry
python -m pie_agent.cli discover --input data/PIE_Data.zip --output outputs/phase11-discovery

# Run Generation Phase
python -m pie_agent.cli generate --input data/PIE_Data.zip --output outputs/phase11-generation

# Publish the generated packages to the local KMS adapter
python -m pie_agent.cli kms-publish --input outputs/phase11-generation/knowledge_manifest.json --kms outputs/phase11-kms
```

## Outputs

```text
outputs/phase11-generation/
├── screen_registry.json
├── source_inventory.json
├── source_mappings/
│   └── <screen_id>.json
├── knowledge/
│   └── <screen_id>.json
├── kms_index.json
└── knowledge_manifest.json
```

## Important limitation

Phase 11 is the generic foundation, not the final ERP connector. A production 4,000+ screen deployment still needs an ERP-specific discovery connector that can read the real frontend repository, backend repository, API metadata, database metadata, configuration and documentation sources. The generic discovery engine is deliberately conservative where source evidence is insufficient.
