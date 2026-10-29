# Phase 17 — ERP-wide AI Knowledge Generation

Phase 17 consumes the Phase 16 Evidence Graph and generates screen-level, evidence-grounded knowledge packages across the ERP.

## Flow

`Evidence Graph -> Screen Evidence Bundle -> deterministic facts -> OpenAI structured knowledge -> evidence validation -> review gate -> KMS-ready manifest`

## Knowledge types

- Functionality
- Feature
- BusinessRule
- Configuration
- Variant
- Workflow
- Training
- UAT
- BusinessSummary

Every generated record must cite one or more evidence IDs from the graph. Inferred or incomplete claims are marked `needsReview=true`. If OpenAI is unavailable or returns invalid output, PIE falls back to deterministic evidence-derived records and marks the package `REVIEW_REQUIRED`.

## Offline validation

```bash
python -m pytest -q
python -m pie_agent.cli generate-ai-knowledge --provider offline --limit 1
```

## OpenAI run

Set locally in `.env`:

```env
PIE_LLM_PROVIDER="openai"
OPENAI_API_KEY="YOUR_KEY"
OPENAI_MODEL="gpt-4.1"
PIE_AI_WORKERS="1"
```

Then:

```bash
python -m pie_agent.cli generate-ai-knowledge
```

For a single screen:

```bash
python -m pie_agent.cli generate-ai-knowledge --screen-id LEAVE.LEAVE_APPLICATION
```

For a controlled pilot:

```bash
python -m pie_agent.cli generate-ai-knowledge --limit 10 --workers 2
```

## Outputs

```text
outputs/phase17-ai-knowledge/
├── screen_evidence_bundles_v17.json
├── knowledge_manifest_v17.json
├── generation_report_v17.json
└── knowledge/
    └── <screenId>.json
```

## Important boundary

Phase 17 generates knowledge. It does not yet publish directly into a production KMS, perform incremental change detection, or execute ERP actions. Those remain later phases.
