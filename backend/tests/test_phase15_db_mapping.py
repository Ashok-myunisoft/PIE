import json
from pathlib import Path
from pie_agent.db_mapping import parse_sql_schema_sources, discover_backend_db_references, map_backend_files_to_database, map_api_mappings_to_database


def test_parse_sql_schema_sources(tmp_path):
    root=tmp_path/'be'; root.mkdir()
    (root/'schema.sql').write_text('CREATE TABLE dbo.Leave (LeaveId int);\nCREATE VIEW dbo.LeaveView AS SELECT * FROM dbo.Leave;\nCREATE PROCEDURE dbo.GetLeave AS SELECT * FROM dbo.Leave;', encoding='utf-8')
    r=parse_sql_schema_sources(root)
    names={o['name'] for o in r['objects']}
    assert {'Leave','LeaveView','GetLeave'} <= names


def test_backend_reference_maps_to_db_object(tmp_path):
    root=tmp_path/'be'; root.mkdir()
    (root/'LeaveService.cs').write_text('public class LeaveService { public void X(){ var x = db.Leave; db.Database.ExecuteSqlRaw("SELECT * FROM dbo.LeaveDetail"); } }', encoding='utf-8')
    refs=discover_backend_db_references(root)
    assert refs and any(r['references'] for r in refs)
    objs=[{'schema':'dbo','name':'Leave','type':'BASE TABLE','evidence':[]},{'schema':'dbo','name':'LeaveDetail','type':'BASE TABLE','evidence':[]}]
    mapped=map_backend_files_to_database(refs, objs)
    assert mapped[0]['databaseObjects']


def test_api_to_database_mapping_prefers_endpoint_evidence(tmp_path):
    api=tmp_path/'api.json'
    api.write_text(json.dumps([{'screenId':'LEAVE.LEAVE_APPLICATION','screenName':'Leave Application','module':'Leave','feature':'Leave Application','apiMappings':[{'method':'POST','route':'/leave/apply','endpointClass':'ApplyLeaveEndpoint','endpointFile':'Endpoints/ApplyLeaveEndpoint.cs','serviceCalls':[],'serviceImplementations':[]}]}]), encoding='utf-8')
    file_map=[{'file':'Endpoints/ApplyLeaveEndpoint.cs','databaseObjects':[{'schema':'dbo','name':'Leave','type':'BASE TABLE','confidence':1.0,'confidenceLevel':'HIGH','reason':'exact schema/object name match','backendEvidence':[],'databaseEvidence':[]}]}]
    out,stats=map_api_mappings_to_database(str(api), tmp_path, file_map[0]['databaseObjects'], file_map)
    assert out[0]['apiMappings'][0]['databaseMappings'][0]['name']=='Leave'
    assert stats['apiMappingsWithDatabase']==1
