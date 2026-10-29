from pathlib import Path
from pie_agent.ingestion import ingest
from pie_agent.classification import classify_all
from pie_agent.relevance import rank_sources
from pie_agent.offline_synthesis import build_offline_pie
from pie_agent.validation import validate

def test_golden_offline():
    z=Path("data/PIE_Data.zip")
    assert z.exists()
    ing=ingest(z)
    classify_all(ing.records,ing.text)
    rel=rank_sources(ing.records,ing.text)
    assert any(r.sourceCategory=="Screen" for r in ing.records)
    assert any(x.selected for x in rel)
    pie=build_offline_pie(ing.text,ing.records)
    report=validate(pie)
    assert report["passed"]
    assert len(pie.configItems) >= 5
    assert len(pie.trainingTopics) >= 3
