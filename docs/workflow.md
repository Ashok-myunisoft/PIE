# Agent workflow

1. **Ingest** all files.
2. **Inventory** each file with type, category, module/screen hints, confidence and reason.
3. **Detect target** from explicit request, filename signals and semantic content.
4. **Score relevance**; do not send every file to every prompt.
5. **Select discovery methods**:
   - Screen → Screen Harvesting
   - Backend → Code Mining
   - DB → DB Schema Mining
   - API → Response Mining
   - Manual/training → Document Mining
   - Ticket → Ticket Analysis
   - Competitor → Competitor Intelligence
6. **Build context** with primary source, supporting evidence, conflicting evidence, existing PIE and Playbook rules.
7. **Run source-specific extraction**.
8. **Cross-validate** entities across sources.
9. **Build PIE entities** with evidence arrays.
10. **Flag inference** and unresolved conflicts with `needsReview=true`.
11. **Generate business summary**.
12. **Run quality gates**.
13. **Export** JSON, Excel and human-readable report.
14. **Human review** before publication.
