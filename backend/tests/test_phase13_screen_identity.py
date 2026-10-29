import json
import pytest
from pathlib import Path
from pie_agent.erp_discovery import run_erp_discovery
from pie_agent.screen_identity import consolidate, run_screen_identity
from pie_agent.erp_source import ScreenCandidate

def test_consolidates_same_module_feature():
    a=ScreenCandidate('A','Leave Application','ESS','ESS','Leave Application','ess',componentPath='a.component.ts',confidence=.8,evidence=[{'source':'registry.json','fact':'registered'}])
    b=ScreenCandidate('B','Leave Application','ESS','ESS','Leave Application','ess',formDefinitionPath='leave.json',confidence=.9,evidence=[{'source':'leave.json','fact':'form definition'}])
    rows,report=consolidate([a,b])
    assert len(rows)==1
    assert report['duplicatesConsolidated']==1
    assert rows[0]['status']=='CONFIRMED'
    assert rows[0]['fieldCount']==0

def test_real_erp_discovery_and_identity(tmp_path):
    fe=Path('data/erp/frontend'); be=Path('data/erp/backend')
    if not (fe.exists() and be.exists()): pytest.skip('ERP sources not present under data/erp')
    d=tmp_path/'discovery'
    r=run_erp_discovery(str(fe),str(be),str(d),database_url='')
    out=tmp_path/'identity'
    x=run_screen_identity(str(d/'erp_discovery_report.json'),str(out))
    assert x['uniqueScreens'] > 100
    assert x['uniqueScreens'] <= x['inputCandidates']
    rows=json.loads((out/'screen_registry_v13.json').read_text())
    assert len({r['screenId'] for r in rows}) == len(rows)
    assert x['statusCounts']['CONFIRMED'] > 0
