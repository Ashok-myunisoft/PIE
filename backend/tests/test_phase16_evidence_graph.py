import json
from pathlib import Path
from pie_agent.evidence_graph import build_evidence_graph

def test_phase16_builds_graph(tmp_path):
    api=tmp_path/'api.json'
    db=tmp_path/'db.json'
    reg=tmp_path/'reg.json'
    api.write_text(json.dumps({"mappings":[{"screenId":"LEAVE.LEAVE_APPLICATION","apis":[{"method":"POST","route":"/leave/apply","confidence":0.95,"backend":{"class":"ApplyLeaveEndpoint","service":"LeaveBLL"}}]}]}))
    db.write_text(json.dumps({"mappings":[{"screenId":"LEAVE.LEAVE_APPLICATION","databaseObjects":[{"schema":"dbo","name":"Leave","type":"TABLE","confidence":0.91,"evidence":[{"source":"LeaveBLL.cs","fact":"SQL reference"}]}]}]}))
    reg.write_text(json.dumps({"screens":[{"screenId":"LEAVE.LEAVE_APPLICATION","screenName":"Leave Application","module":"Leave","feature":"Leave Application"}]}))
    r=build_evidence_graph(api,db,reg,tmp_path/'out')
    assert r['screens']==1
    assert r['apis']==1
    assert r['databaseObjects']==1
    assert (tmp_path/'out/evidence_graph_v16.json').exists()
