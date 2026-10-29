# Golden evaluation

The Leave Application / Leave Request dataset is the first benchmark.

Compare semantically against:
- screen-harvesting output
- screen-harvesting summary
- frontend output
- backend output
- DB schema output
- API/service output
- document mining/UAT/training output

Metrics:
- coverage
- entity recall
- classification accuracy
- source attribution
- summary completeness
- prompt correctness
- unsupported claim rate
- needsReview accuracy

Do not use exact string equality as the main metric. The supplied manual outputs contain claims that are
not always confirmed by all primary sources; the Agent should prefer source-backed truth and flag unresolved
claims rather than reproduce them.
