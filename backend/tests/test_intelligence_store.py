from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

from pie_agent.intelligence import build
from pie_agent.intelligence_store import IntelligenceStore, point_id
from pie_agent.qdrant_kms import QdrantKMS
from test_department_intelligence import inputs
from test_phase19_chat import FakeQdrant


class Client(FakeQdrant):
    def upsert(self,name,points):
        return super().upsert(name,deepcopy(points))

    def retrieve(self,name,pid):
        return {'result':[deepcopy(self.points[pid])] if pid in self.points else []}


def test_complete_roundtrip_and_idempotence(inputs):
    root,editorial,_=inputs
    package=build('inventory',root,editorial)
    client=Client()
    store=IntelligenceStore(QdrantKMS(client,'test'))
    config={'module':'inventory','briefs':[]}
    approval={'module':'inventory','approvals':[]}
    result=store.publish(package,config,approval)
    assert result['status']=='VERIFIED_IN_QDRANT'
    assert store.load('inventory')==package
    assert store.overlays('inventory')==(config,approval)
    count=len(client.points)
    store.publish(package,config,approval)
    assert len(client.points)==count==len(package['records'])+1
    assert store.summary('inventory')['storage']=='qdrant'
    with pytest.raises(LookupError): store.load('finance')


def test_corruption_is_detected_without_disk_fallback(inputs):
    root,editorial,_=inputs
    package=build('inventory',root,editorial)
    client=Client()
    store=IntelligenceStore(QdrantKMS(client,'test'))
    result=store.publish(package)
    pid=point_id('inventory',result['generation'],package['records'][0]['id'])
    client.points[pid]['payload']['record']['content']='Corrupted'
    with pytest.raises(RuntimeError,match='incomplete or has changed'): store.load('inventory')


def test_failed_generation_does_not_replace_active_manifest(inputs,monkeypatch):
    root,editorial,_=inputs
    original=build('inventory',root,editorial)
    client=Client()
    store=IntelligenceStore(QdrantKMS(client,'test'))
    store.publish(original)
    changed=deepcopy(original)
    changed['records'][0]['content']='New content'
    original_read=store._records
    monkeypatch.setattr(store,'_records',lambda *args:[])
    with pytest.raises(RuntimeError,match='manifest was not changed'): store.publish(changed)
    monkeypatch.setattr(store,'_records',original_read)
    assert store.load('inventory')==original


def test_shared_api_uses_qdrant_and_disables_response_caching(inputs,monkeypatch):
    from pie_agent import api,intelligence_api
    root,editorial,_=inputs
    store=IntelligenceStore(QdrantKMS(Client(),'test'))
    store.publish(build('inventory',root,editorial))
    monkeypatch.setattr(intelligence_api,'configured_store',lambda:store)
    c=TestClient(api.app)
    summary=c.get('/usage/api/intelligence/inventory/summary')
    assert summary.status_code==200 and summary.headers['cache-control']=='no-store'
    response=c.get('/usage/api/intelligence/inventory/records',params={'department':'marketing','kind':'brief','q':'brochure'})
    assert response.json()['total']==1
    assert response.json()['storage']=='qdrant'
    assert c.get('/usage/api/intelligence/inventory/context',params={'audience':'public'}).json()['records']==[]
    assert c.get('/usage/api/intelligence/finance/summary').status_code==404
    assert c.get('/usage/api/intelligence/inventory/records',params={'department':'unknown'}).status_code==400
