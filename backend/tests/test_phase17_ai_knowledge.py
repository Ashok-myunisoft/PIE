import json
from pathlib import Path
from pie_agent.ai_knowledge import build_screen_bundles, generate_ai_knowledge


def _graph(tmp_path):
    graph={
      "graphVersion":"0.16.0",
      "nodes":[
        {"id":"screen:S1","type":"Screen","label":"Leave Application","properties":{"screenId":"S1","module":"Leave","functionality":"Leave Management","feature":"Leave Application","discoveryConfidence":0.95}},
        {"id":"api:A1","type":"API","label":"/leave/apply","properties":{"method":"POST","route":"/leave/apply"}},
        {"id":"backend:B1","type":"BackendEndpoint","label":"ApplyLeaveEndpoint","properties":{}},
        {"id":"db:dbo.leave","type":"DatabaseObject","label":"dbo.Leave","properties":{"schema":"dbo","name":"Leave","objectType":"TABLE"}},
        {"id":"evidence:E1","type":"Evidence","label":"leave.component.ts","properties":{"source":"leave.component.ts"}}
      ],
      "edges":[
        {"source":"screen:S1","relationship":"CALLS","target":"api:A1","confidence":0.98,"status":"OBSERVED","evidence":[{"source":"leave.component.ts","fact":"POST call"}]},
        {"source":"api:A1","relationship":"IMPLEMENTED_BY","target":"backend:B1","confidence":0.9,"status":"OBSERVED"},
        {"source":"screen:S1","relationship":"USES_DATABASE_OBJECT","target":"db:dbo.leave","confidence":0.7,"status":"CANDIDATE","evidence":[{"source":"Leave.sql","fact":"table reference"}]},
        {"source":"screen:S1","relationship":"SUPPORTED_BY","target":"evidence:E1","confidence":0.95,"status":"OBSERVED"}
      ]
    }
    p=tmp_path/'graph.json'; p.write_text(json.dumps(graph)); return p


def test_bundle_and_offline_generation(tmp_path):
    p=_graph(tmp_path)
    g=json.loads(p.read_text())
    bundles=build_screen_bundles(g)
    assert len(bundles)==1
    assert any(n['type']=='API' for n in bundles[0]['nodes'])
    out=tmp_path/'out'
    r=generate_ai_knowledge(p,out,'offline')
    assert r['packagesGenerated']==1
    assert r['deterministicPackages']==1
    manifest=json.loads((out/'knowledge_manifest_v17.json').read_text())
    assert manifest['knowledgePackages'][0]['screen']['screenId']=='S1'
    assert len(manifest['knowledgePackages'][0]['records']) >= 2
