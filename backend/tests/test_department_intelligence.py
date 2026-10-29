import json
from pathlib import Path

import pytest

from pie_agent.intelligence import build, context, export


@pytest.fixture
def inputs(tmp_path):
    root=tmp_path/'inventory'
    p=root/'phase19-usage/usage_catalog_v19.json'
    p.parent.mkdir(parents=True)
    catalog={'module':'inventory','statistics':{'features':1},'features':[
        {'screenId':'INVENTORY_ITEM_MASTER','name':'Item Master','category':'Master Data','readiness':'READY',
         'capability':{'name':'Item setup','needsReview':True},
         'claims':[{'text':'Item Code is required.','kind':'validation','status':'VERIFIED',
                    'evidence':[{'source':'ItemBLL.cs','detail':'NotEmpty(ItemCode)'}]},
                   {'text':'May integrate with finance.','kind':'integration','status':'REVIEW','evidence':[]}],
         'technical':{'apis':[{'route':'/Item/SaveItem','confidence':0.5}]},
         'summary':{'text':'SECRET_UNREVIEWED_AI_CLAIM'}}]}
    p.write_text(json.dumps(catalog),encoding='utf-8')
    f=root/'phase20-fields/itemmaster_fields_v20.json'
    f.parent.mkdir()
    f.write_text(json.dumps({'module':'inventory','screenId':'INVENTORY_ITEM_MASTER','screen':'Item Master',
                            'statistics':{'fields':1},'fields':[{'fieldName':'ItemCode','label':'Code',
                            'visibleOnScreen':True,'mandatory':{'required':True},'evidence':[{'source':'item.json'}],
                            'explanation':{'businessMeaning':'SECRET_UNREVIEWED_AI_CLAIM'},
                            'database':{'column':'','storage':'screen only (not stored)'}}]}),encoding='utf-8')
    editorial=tmp_path/'editorial.json'
    editorial.write_text(json.dumps({'module':'inventory','briefs':[{'key':'brochure','title':'Item brochure','category':'positioning',
                          'content':'Review item setup.','owner':'Product','departments':['marketing'],
                          'screen_ids':['INVENTORY_ITEM_MASTER']}]}),encoding='utf-8')
    return root,editorial,tmp_path


def test_scope_and_evidence_not_automatic_approval(inputs):
    root,editorial,_=inputs
    p=build('inventory',root,editorial)
    assert all(r['module']=='inventory' for r in p['records'])
    assert not any(r['status']=='APPROVED' for r in p['records'])
    assert 'SECRET_UNREVIEWED_AI_CLAIM' not in json.dumps(p)
    assert any(r['status']=='REVIEW_REQUIRED' for r in p['records'])
    field=next(r for r in p['records'] if r['kind']=='field')
    assert 'absence does not prove' in field['content']
    assert any(r['category']=='commercial' and r['status']=='MISSING' for r in p['records'])


def test_source_correction_quarantines_overbroad_claim(inputs):
    root,editorial,_=inputs
    d=json.loads(editorial.read_text())
    d['source_reviews']=[{'screen_id':'INVENTORY_ITEM_MASTER','claim_contains':'Item Code is required.',
                         'note':'Required only in one configured mode.','source_refs':['ItemBLL.cs#L20']}]
    editorial.write_text(json.dumps(d))
    p=build('inventory',root,editorial)
    r=next(r for r in p['records'] if r.get('original_extracted_content'))
    assert r['status']=='REVIEW_REQUIRED'
    assert r['content'].startswith('REVIEW REQUIRED: Required only')
    assert r['source_review']['source_refs']==['ItemBLL.cs#L20']
    d['source_reviews'][0]['claim_contains']='stale text no longer in catalog'
    editorial.write_text(json.dumps(d))
    with pytest.raises(ValueError,match='no longer matches'): build('inventory',root,editorial)


def test_department_query_and_public_boundary(inputs):
    root,editorial,_=inputs
    p=build('inventory',root,editorial)
    assert context(p,'marketing','brochure')['records'][0]['kind']=='brief'
    assert all(r['kind']!='field' for r in context(p,'marketing','Item',limit=100)['records'])
    assert any(r['kind']=='field' for r in context(p,'development','Item',limit=100)['records'])
    assert context(p,'marketing','brochure','public')['status']=='NO_ELIGIBLE_KNOWLEDGE'
    assert not context(p,'marketing','xyznonexistent')['records']
    with pytest.raises(ValueError): context(p,'unknown')


def approve(p, path, records, **overrides):
    items=[]
    for r in records:
        items.append({'record_id':r['id'],'content_hash':r['content_hash'],'owner':'Product owner','reviewed_by':'Reviewer',
                      'reviewed_on':'2026-01-01','erp_version':'ERP release 5.1','visibility':'public',**overrides})
    path.write_text(json.dumps({'module':p['module'],'approvals':items}),encoding='utf-8')


def test_approval_dependencies_and_public_projection(inputs):
    root,editorial,tmp=inputs
    p=build('inventory',root,editorial)
    brief=next(r for r in p['records'] if r['kind']=='brief')
    screen=next(r for r in p['records'] if r['kind']=='screen')
    path=tmp/'approvals.json'
    approve(p,path,[brief])
    assert not context(build('inventory',root,editorial,path),'marketing','brochure','public')['records']
    approve(p,path,[brief,screen])
    public=context(build('inventory',root,editorial,path),'marketing','brochure','public')
    assert len(public['records'])==1
    assert public['records'][0]['content']=='Review item setup.'
    assert 'provenance' not in public['records'][0]
    assert 'ItemBLL.cs' not in json.dumps(public)


def test_changed_source_invalidates_approval(inputs):
    root,editorial,tmp=inputs
    p=build('inventory',root,editorial)
    claim=next(r for r in p['records'] if r['kind']=='claim' and r['status']=='SOURCE_EVIDENCE')
    path=tmp/'approvals.json'
    approve(p,path,[claim])
    assert context(build('inventory',root,editorial,path),'marketing','Item Code','public')['records']
    cat=root/'phase19-usage/usage_catalog_v19.json'
    d=json.loads(cat.read_text());d['features'][0]['claims'][0]['text']='Item Code is optional.'
    cat.write_text(json.dumps(d),encoding='utf-8')
    rebuilt=build('inventory',root,editorial,path)
    assert claim['id'] in rebuilt['stale_approvals']
    assert not context(rebuilt,'marketing','Item','public')['records']


def test_changed_direct_reference_invalidates_editorial_approval(inputs):
    root,editorial,tmp=inputs
    source=tmp/'reviewed.cs'
    source.write_text('Original rule',encoding='utf-8')
    d=json.loads(editorial.read_text())
    d['briefs'][0]['source_refs']=[source.as_posix()+'#L1']
    editorial.write_text(json.dumps(d))
    p=build('inventory',root,editorial)
    brief=next(r for r in p['records'] if r['kind']=='brief')
    approvals=tmp/'approvals.json'
    approve(p,approvals,[brief])
    source.write_text('Changed rule',encoding='utf-8')
    assert brief['id'] in build('inventory',root,editorial,approvals)['stale_approvals']


def test_reject_module_mix_and_missing_versions(inputs):
    root,editorial,tmp=inputs
    with pytest.raises(ValueError): build('../inventory',root,editorial)
    with pytest.raises(ValueError): build('finance',root,editorial)
    p=build('inventory',root,editorial)
    path=tmp/'approvals.json'
    approve(p,path,[p['records'][0]],erp_version='TBD')
    with pytest.raises(ValueError): build('inventory',root,editorial,path)
    d=json.loads(editorial.read_text());d['briefs'][0]['screen_ids']=['FINANCE_VOUCHER']
    editorial.write_text(json.dumps(d))
    with pytest.raises(ValueError): build('inventory',root,editorial)


def test_local_exports_are_disabled(inputs):
    root,editorial,tmp=inputs
    with pytest.raises(RuntimeError,match='disabled'):
        export(build('inventory',root,editorial),tmp/'handoff')
    assert not (tmp/'handoff').exists()


def test_cli_reads_qdrant_without_logging_or_local_output(inputs,monkeypatch,capsys):
    import sys
    from pie_agent import cli, intelligence_store
    root,editorial,tmp=inputs
    package=build('inventory',root,editorial)
    class Store:
        def load(self,module):
            assert module=='inventory'
            return package
    monkeypatch.setattr(intelligence_store,'configured_store',lambda:Store())
    def forbidden(*args,**kw): raise AssertionError('Knowledge context must not be written to activity logs')
    monkeypatch.setattr(cli,'track',forbidden)
    monkeypatch.setattr(sys,'argv',['pie','intelligence-context','--module','inventory','--department','marketing','--audience','public'])
    cli.main()
    assert json.loads(capsys.readouterr().out)['status']=='NO_ELIGIBLE_KNOWLEDGE'
