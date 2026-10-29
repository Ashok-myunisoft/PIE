# Prompt registry

The prompt files in this directory are copied from the supplied PIE golden dataset.

Runtime routing uses:
- Screen → screen harvesting prompt
- Backend → back-end mining prompt
- Frontend → front-end mining prompt
- DB → DB schema mining prompt
- API → response/service mining prompt
- Manual/training → document mining prompt

The runtime then wraps these source-specific instructions inside a dynamic multi-source synthesis context.
Do not replace this with a single mega-prompt.
