"""Qdrant is the persistent store for departmental intelligence.

Publish a generation, verify every record, then switch the manifest. Readers never
fall back to files or write knowledge caches. No external embedding/AI service.
"""
from __future__ import annotations

import os
import uuid
from copy import deepcopy
from typing import Any

from .intelligence import digest, module_name
from .qdrant_kms import QdrantHTTP, QdrantKMS

NAMESPACE='pie_department_intelligence'


def point_id(*parts: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL,'pie://'+NAMESPACE+'/'+'/'.join(parts)))


def configured_store() -> 'IntelligenceStore':
    from .db_discovery import load_dotenv
    load_dotenv()
    return IntelligenceStore(QdrantKMS(QdrantHTTP(os.getenv('QDRANT_URL','http://localhost:6333'),
                                                 os.getenv('QDRANT_API_KEY','')),
                                      os.getenv('QDRANT_COLLECTION','pie_knowledge')))


class IntelligenceStore:
    def __init__(self,kms: QdrantKMS):
        self.kms=kms

    def _manifest(self,module: str) -> dict:
        module_name(module)
        rows=self.kms.client.retrieve(self.kms.collection,point_id(module,'manifest')).get('result',[])
        if not rows:
            raise LookupError(f'No departmental intelligence published for {module}')
        p=rows[0]['payload']
        if p.get('namespace')!=NAMESPACE or p.get('module')!=module:
            raise RuntimeError('Unexpected intelligence manifest identity')
        return p

    def _filter(self,module: str,generation: str) -> dict:
        return {'must':[{'key':k,'match':{'value':v}} for k,v in
                        [('namespace',NAMESPACE),('module',module),('generation',generation),('knowledge_type','DepartmentKnowledge')]]}

    def _records(self,module: str,generation: str) -> list[dict]:
        points=self.kms.scroll_all(self._filter(module,generation),page_size=256)
        return [p['payload']['record'] for p in points]

    def publish(self,package: dict,editorial: dict | None = None,approvals: dict | None = None) -> dict:
        module=module_name(package['module'])
        records=package['records']
        if any(r['module']!=module for r in records) or len({r['id'] for r in records})!=len(records):
            raise ValueError('Duplicate IDs or mixed modules in package')
        for overlay in (editorial,approvals):
            if overlay is not None and overlay.get('module')!=module:
                raise ValueError('Overlay module mismatch')
        # Full-payload digest, not merely the approval hash: all fields must survive migration.
        expected={r['id']:digest(r) for r in records}
        generation=digest({'package':package,'editorial':editorial,'approvals':approvals})[:24]
        self.kms.ensure_collection()
        vector=self.kms.embedder.embed
        from .embeddings import prime
        prime(self.kms.embedder,[f"[{r['status']}; {r['visibility']}] {r['title']}\n{r['content']}" for r in records])
        for start in range(0,len(records),64):
            batch=[]
            for r in records[start:start+64]:
                text=f"[{r['status']}; {r['visibility']}] {r['title']}\n{r['content']}"
                payload={'namespace':NAMESPACE,'module':module,'generation':generation,
                         'knowledge_type':'DepartmentKnowledge','knowledge_id':r['id'],
                         'product':package.get('product','GoodBooks ERP'),'title':r['title'],'content':text,
                         'departments':r['departments'],'category':r['category'],'approval_status':r['status'],
                         'visibility':r['visibility'],'publication_status':'PUBLISHED' if r['status']=='APPROVED' and r['visibility']=='public' else 'REVIEW_REQUIRED',
                         'record_hash':expected[r['id']],'record':deepcopy(r)}
                batch.append({'id':point_id(module,generation,r['id']),'vector':vector(text),'payload':payload})
            self.kms.client.upsert(self.kms.collection,batch)
        actual=self._records(module,generation)
        if len(actual)!=len(records) or {r['id']:digest(r) for r in actual}!=expected:
            raise RuntimeError('Qdrant record verification failed; manifest was not changed')
        metadata={k:deepcopy(v) for k,v in package.items() if k!='records'}
        manifest={'namespace':NAMESPACE,'module':module,'knowledge_type':'DepartmentManifest',
                  'title':module+' department intelligence manifest','publication_status':'INTERNAL_ONLY',
                  'active_generation':generation,'record_order':[r['id'] for r in records],
                  'record_hashes':expected,'metadata':metadata,'editorial':editorial,'approvals':approvals,
                  'package_hash':digest(package)}
        self.kms.client.upsert(self.kms.collection,[{'id':point_id(module,'manifest'),
                                                   'vector':vector(module+' department intelligence manifest'),
                                                   'payload':manifest}])
        # Verify the committed view before giving callers permission to remove local copies.
        loaded=self.load(module)
        if digest(loaded)!=digest(package):
            raise RuntimeError('Committed package verification failed; retain local copies')
        committed=self._manifest(module)
        if committed.get('editorial')!=editorial or committed.get('approvals')!=approvals:
            raise RuntimeError('Qdrant overlay verification failed; retain local copies')
        return {'status':'VERIFIED_IN_QDRANT','module':module,'collection':self.kms.collection,
                'records':len(records),'generation':generation,'package_hash':digest(package),
                'editorial_verified':True,'approvals_verified':True}

    def load(self,module: str) -> dict:
        manifest=self._manifest(module)
        rows=self._records(module,manifest['active_generation'])
        by_id={r['id']:r for r in rows}
        expected=manifest['record_hashes']
        if len(rows)!=len(expected) or {k:digest(v) for k,v in by_id.items()}!=expected:
            raise RuntimeError('Qdrant intelligence generation is incomplete or has changed')
        package={**manifest['metadata'],'records':[by_id[k] for k in manifest['record_order']]}
        if digest(package)!=manifest['package_hash']:
            raise RuntimeError('Qdrant intelligence package hash does not match')
        return package

    def overlays(self,module: str) -> tuple[dict,dict]:
        m=self._manifest(module)
        return m.get('editorial') or {'module':module,'briefs':[]},m.get('approvals') or {'module':module,'approvals':[]}

    def summary(self,module: str) -> dict:
        m=self._manifest(module)
        return {**m['metadata'],'storage':'qdrant','collection':self.kms.collection,'generation':m['active_generation'],
                'record_count':len(m['record_order'])}
