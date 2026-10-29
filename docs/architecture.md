# PIE Agent Architecture

```text
                           ┌───────────────────────┐
                           │       PIE Agent       │
                           └───────────┬───────────┘
                                       │
                         ┌─────────────▼─────────────┐
                         │      Source Ingestion     │
                         │ ZIP/PDF/XLSX/TXT/Code/API │
                         └─────────────┬─────────────┘
                                       │
                         ┌─────────────▼─────────────┐
                         │    Source Classification  │
                         └─────────────┬─────────────┘
                                       │
                         ┌─────────────▼─────────────┐
                         │ Target + Relevance Engine │
                         └─────────────┬─────────────┘
                                       │
                         ┌─────────────▼─────────────┐
                         │       Prompt Router       │
                         └─────────────┬─────────────┘
                                       │
                 ┌─────────────────────▼────────────────────┐
                 │          Structured Context Builder      │
                 │ target / primary / supporting / conflict │
                 └─────────────────────┬────────────────────┘
                                       │
                 ┌─────────────────────▼────────────────────┐
                 │     Discovery + Synthesis AI Layer       │
                 │ screen / code / DB / API / document      │
                 └─────────────────────┬────────────────────┘
                                       │
                 ┌─────────────────────▼────────────────────┐
                 │       Evidence + Conflict Validator      │
                 └─────────────────────┬────────────────────┘
                                       │
                 ┌─────────────────────▼────────────────────┐
                 │           PIE Entity Builder             │
                 │ Config / Variant / Training / UAT / KPI │
                 └─────────────────────┬────────────────────┘
                                       │
                 ┌───────────────┬─────▼─────┬──────────────┐
                 │               │           │              │
             Summary         Quality      Traceability    Review
                 │               │           │              │
                 └───────────────┴─────┬─────┴──────────────┘
                                       │
                         ┌─────────────▼─────────────┐
                         │     JSON / Excel / MD     │
                         └───────────────────────────┘
```

## Separation of responsibilities

### Deterministic code
- File ingestion
- File typing
- source inventory
- text extraction
- source classification
- relevance scoring
- schema validation
- evidence IDs
- Excel generation
- traceability persistence
- quality gates

### AI
- semantic target detection when the target is not explicit
- semantic source relevance for large repositories
- business meaning extraction
- cross-source synthesis
- conflict interpretation
- plain-language descriptions
- candidate variants and implications
- document-to-training/UAT extraction

### Human
- approve/reject entities
- resolve ambiguous business rules
- confirm mandatory/default/effort values when evidence is incomplete
- publish final PIE truth

The Playbook explicitly treats AI output as a first-pass draft and requires domain-expert validation.
