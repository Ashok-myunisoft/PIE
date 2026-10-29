import json
from pathlib import Path

from pie_agent.api_mapping import (
    discover_backend_service_catalog, discover_frontend_api_calls, legacy_url_entity,
    load_form_action_urls, map_screen_to_api_backend, discover_service_implementations,
)
from pie_agent.ai_knowledge import build_screen_bundles
from pie_agent.db_mapping import discover_backend_db_references, resolve_persistence_chain
from pie_agent.erp_source import ScreenCandidate
from pie_agent.screen_identity import consolidate
from pie_agent.usage import build_usage_catalog, export_static_ui, humanize


def _w(p: Path, text: str):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def _gb5_backend(root: Path):
    _w(root / "GB5Solution/MM/MMSL/EndPoints/ItemCategory/GetItemCategory.cs", """
namespace MMSL.EndPoints.ItemCategory {
public class GetItemCategory : BaseEndpoint<P, R> {
    private readonly IItemCategoryBLL _ItemCategoryBLL;
    public GetItemCategory(IItemCategoryBLL IItemCategoryBLL) { _ItemCategoryBLL = IItemCategoryBLL; }
    public override void Configure() { Get("/ItemCategory/GetItemCategory"); }
    protected override async Task<R> ExecuteAsync(P p) { return await _ItemCategoryBLL.GetItemCategory(p.Id); }
}}""")
    _w(root / "GB5Solution/MM/MMSL/EndPoints/ItemCategory/SaveItemCategory.cs", """
public class SaveItemCategory : BaseEndpoint<P, R> {
    private readonly IItemCategoryBLL _bll;
    public SaveItemCategory(IItemCategoryBLL bll) => _bll = bll;
    public override void Configure() { Post("/ItemCategory/SaveItemCategory"); }
    protected override async Task<R> ExecuteAsync(P p) { return await _bll.SaveItemCategory(p.Dto); }
}""")
    _w(root / "GB5Solution/MM/MMBLL/ItemCategory/ItemCategoryBLL.cs", """
public class ItemCategoryBLL : IItemCategoryBLL
{
    private readonly IItemCategoryDAL _ItemCategoryDAL;
    private readonly AutoNumber _AutoNumber;
    public async Task<string> GetItemCategory(int id) { return await _ItemCategoryDAL.GetItemCategory(id); }
    public async Task<string> SaveItemCategory(ItemCategoryDTO dto)
    {
        await _Validation.NotEmpty(dto.ItemCategoryCode, nameof(dto.ItemCategoryCode));
        await _Validation.NotEmpty(dto.ItemCategoryName, nameof(dto.ItemCategoryName));
        if (dto.Rate < 0) throw new InvalidOperationException("Rate cannot be negative.");
        if (dto == null) throw new Exception("Error in GetItemCategoryReport.");
        var n = await _AutoNumber.GetNumberAsync(dto);
        return await _ItemCategoryDAL.SaveItemCategory(dto);
    }
}""")
    _w(root / "GB5Solution/MM/MMDAL/CustomCode/ItemCategory/ItemCategoryDAL.cs", """
public class ItemCategoryDAL : IItemCategoryDAL
{
    private readonly IQueryExecutor _QueryExecutor;
    public async Task<string> GetItemCategory(int id) { string sql = ItemCategoryQB.GET_ITEMCATEGORY; return sql; }
}""")
    _w(root / "GB5Solution/MM/MMDAL/Query/ItemCategory/ItemCategoryQB.cs", '''
public class ItemCategoryQB
{
    public const string GET_ITEMCATEGORY = @"SELECT * FROM MITEMCATEGORY IC INNER JOIN MUSER U ON IC.CREATEDBYID = U.USERID";
}''')


def _gb5_frontend(root: Path):
    _w(root / "projects/gbhost/public/formactionurls/geturl.ts",
       "export class GetUrl {\n  public static itemcategory = \"/mms/ItemCategory.svc/?ItemCategoryId=\"\n  public static batchdetail = '/mms/Lot.svc/TLot/Info/From/FE/?LotId='\n}")
    _w(root / "projects/gbhost/public/formactionurls/saveurl.ts",
       "export class SaveUrl {\n  public static itemcategory = '/mms/ItemCategory.svc/'\n}")
    _w(root / "projects/inventory/master/itemmenu/itemcategory/itemcategory.component.ts",
       "export class ItemCategoryComponent { init(){ this.form = new GBBaseFormGroup(this.injector, 'itemcategory', this.MenuId, 'inventory'); this.form.get('ItemCategoryName'); } }")


def test_field_injected_dependencies_are_detected(tmp_path):
    _gb5_backend(tmp_path)
    rows = {r["route"]: r for r in discover_backend_service_catalog(tmp_path)}
    save = rows["/ItemCategory/SaveItemCategory"]
    assert save["dependencies"] == [{"type": "IItemCategoryBLL", "variable": "_bll"}]
    assert save["serviceCalls"][0]["method"] == "SaveItemCategory"
    assert save["module"] == "MM" and save["entity"] == "ItemCategory"


def test_form_control_get_is_not_an_api_call(tmp_path):
    _gb5_frontend(tmp_path)
    calls = discover_frontend_api_calls(tmp_path)
    assert not any(c["url"] == "ItemCategoryName" for c in calls)


def test_legacy_url_entity():
    assert legacy_url_entity("/mms/ItemCategory.svc/?ItemCategoryId=") == ("ItemCategory", "")
    assert legacy_url_entity("/mms/PackNumber.svc/SavePutAwayBinLedger") == ("PackNumber", "SavePutAwayBinLedger")


def test_form_action_urls_map_screen_to_endpoints(tmp_path):
    fe, be = tmp_path / "fe", tmp_path / "be"
    _gb5_frontend(fe); _gb5_backend(be)
    screen = {"screenId": "INVENTORY_ITEM_CATEGORY", "screenName": "Item Category", "module": "inventory",
              "feature": "Item Category", "componentPath": "projects/inventory/master/itemmenu/itemcategory/itemcategory.component.ts"}
    out, stats = map_screen_to_api_backend([screen], discover_backend_service_catalog(be), discover_frontend_api_calls(fe),
                                           discover_service_implementations(be), load_form_action_urls(fe), fe)
    routes = {a["route"]: a for a in out[0]["apiMappings"]}
    assert routes["/ItemCategory/GetItemCategory"]["confidenceLevel"] == "HIGH"
    assert routes["/ItemCategory/SaveItemCategory"]["serviceImplementations"][0]["file"].endswith("ItemCategoryBLL.cs")
    assert stats["formActionMappings"] == 2


def test_legacy_service_without_endpoint_is_reported_not_guessed(tmp_path):
    fe, be = tmp_path / "fe", tmp_path / "be"
    _gb5_frontend(fe); _gb5_backend(be)
    _w(fe / "projects/inventory/transaction/batchdetail/batchdetail.component.ts",
       "export class BatchDetailComponent { x(){ this.service.formloadservice('batchdetail', 1); } }")
    screen = {"screenId": "INVENTORY_BATCH_DETAIL", "screenName": "Batch Detail", "module": "inventory", "feature": "Batch Detail",
              "componentPath": "projects/inventory/transaction/batchdetail/batchdetail.component.ts"}
    out, stats = map_screen_to_api_backend([screen], discover_backend_service_catalog(be), [], [], load_form_action_urls(fe), fe)
    assert out[0]["unresolvedFormActions"][0]["entity"] == "Lot"
    assert stats["unresolvedFormActions"] == 1


def test_persistence_chain_reaches_query_builder(tmp_path):
    _gb5_backend(tmp_path)
    index = {"classes": {}, "interfaces": {}, "files": {}}
    discover_backend_db_references(tmp_path, index)
    chain = resolve_persistence_chain(["GB5Solution/MM/MMSL/EndPoints/ItemCategory/GetItemCategory.cs"], index)
    files = {c["file"] for c in chain}
    assert "GB5Solution/MM/MMDAL/Query/ItemCategory/ItemCategoryQB.cs" in files
    # Infrastructure dependencies (IQueryExecutor) are not followed.
    assert all("QueryExecutor" not in f for f in files)


def test_identity_merges_spacing_and_module_prefix_variants():
    a = ScreenCandidate("A", "Assembly Bom", "inventory", "Inventory", "Assembly Bom", "inventory",
                        componentPath="projects/inventory/master/assemblybom/assemblybom.component.ts", evidence=[])
    b = ScreenCandidate("B", "Assemblybom", "inventory", "Inventory", "Assemblybom", "inventory",
                        formDefinitionPath="projects/inventory/public/formjson/assemblybom.json", evidence=[])
    c = ScreenCandidate("C", "Inventory Assembly Bom", "inventory", "Inventory", "Inventory Assembly Bom", "inventory",
                        evidence=[{"source": "features/gbformviewer/gbformviewer.registry.json", "fact": "registered"}])
    rows, report = consolidate([b, c, a])
    assert len(rows) == 1 and report["duplicatesConsolidated"] == 2
    assert rows[0]["screenId"] == "INVENTORY_ASSEMBLY_BOM"
    assert set(rows[0]["aliasScreenIds"]) == {"INVENTORY_ASSEMBLYBOM", "INVENTORY_INVENTORY_ASSEMBLY_BOM"}
    assert rows[0]["status"] == "CONFIRMED"


def test_component_alone_is_candidate_not_likely():
    a = ScreenCandidate("A", "Stock Ledger", "inventory", "Inventory", "Stock Ledger", "inventory",
                        componentPath="projects/inventory/report/stockledger/stockledger.component.ts", confidence=.78, evidence=[])
    rows, _ = consolidate([a])
    assert rows[0]["status"] == "CANDIDATE"


def test_bundle_does_not_leak_through_shared_nodes():
    graph = {"nodes": [
        {"id": "screen:A", "type": "Screen", "label": "A", "properties": {"screenId": "A"}},
        {"id": "screen:B", "type": "Screen", "label": "B", "properties": {"screenId": "B"}},
        {"id": "api:a", "type": "API", "label": "/a", "properties": {}},
        {"id": "api:b", "type": "API", "label": "/b", "properties": {}},
        {"id": "backend:a", "type": "BackendEndpoint", "label": "EA", "properties": {}},
        {"id": "backend:b", "type": "BackendEndpoint", "label": "EB", "properties": {}},
        {"id": "db:dbo.shared", "type": "DatabaseObject", "label": "dbo.SHARED", "properties": {}},
    ], "edges": [
        {"source": "screen:A", "relationship": "CALLS", "target": "api:a"},
        {"source": "api:a", "relationship": "IMPLEMENTED_BY", "target": "backend:a"},
        {"source": "backend:a", "relationship": "READS_OR_WRITES", "target": "db:dbo.shared"},
        {"source": "screen:B", "relationship": "CALLS", "target": "api:b"},
        {"source": "api:b", "relationship": "IMPLEMENTED_BY", "target": "backend:b"},
        {"source": "backend:b", "relationship": "READS_OR_WRITES", "target": "db:dbo.shared"},
    ]}
    a = next(b for b in build_screen_bundles(graph) if b["screen"]["screenId"] == "A")
    ids = {n["id"] for n in a["nodes"]}
    assert "db:dbo.shared" in ids and "backend:b" not in ids and "api:b" not in ids


def test_humanize():
    assert humanize("ItemCategoryCode") == "Item Category Code"
    assert humanize("Assembly_Bom") == "Assembly BOM"


def _module_dir(tmp_path: Path) -> Path:
    m = tmp_path / "modules" / "inventory"
    reg = [{"screenId": "INVENTORY_ITEM_CATEGORY", "screenName": "Item Category", "feature": "Item Category", "module": "inventory",
            "status": "CONFIRMED", "aliasScreenIds": ["INVENTORY_ITEMCATEGORY"],
            "componentPath": "projects/inventory/master/itemmenu/itemcategory/itemcategory.component.ts",
            "formDefinitionPath": "projects/inventory/public/formjson/itemcategory.json",
            "fields": [{"Name": "ItemCategoryCode", "Required": True}, {"Name": "ItemCategoryName", "Required": True}, {"Name": "Remarks", "Required": False}]},
           {"screenId": "INVENTORY_STOCK_LEDGER", "screenName": "Stock Ledger", "feature": "Stock Ledger", "module": "inventory",
            "status": "CANDIDATE", "aliasScreenIds": [], "componentPath": "projects/inventory/report/stockledger/stockledger.component.ts"}]
    api = {"method": "POST", "route": "/ItemCategory/SaveItemCategory", "endpointClass": "SaveItemCategory", "confidence": .92,
           "confidenceLevel": "HIGH", "endpointFile": "GB5Solution/MM/MMSL/EndPoints/ItemCategory/SaveItemCategory.cs",
           "frontendEvidence": [{"source": "form-action:save:itemcategory"}],
           "serviceImplementations": [{"file": "GB5Solution/MM/MMBLL/ItemCategory/ItemCategoryBLL.cs"}],
           "databaseMappings": [{"schema": "dbo", "name": "MITEMCATEGORY", "confidence": 1.0, "evidenceFiles": ["GB5Solution/MM/MMDAL/Query/ItemCategory/ItemCategoryQB.cs"],
                                 "operations": ["READ", "WRITE"], "writeFiles": ["GB5Solution/MM/MMDAL/Query/ItemCategory/ItemCategoryQB.cs"], "backendEvidence": [{"kind": "TABLE_OR_VIEW", "evidence": "INTO MITEMCATEGORY"}]},
                                {"schema": "dbo", "name": "MITEM", "confidence": 1.0, "evidenceFiles": [], "operations": ["READ"], "backendEvidence": [{"kind": "TABLE_OR_VIEW", "evidence": "JOIN MITEM"}]},
                                {"schema": "dbo", "name": "MUSER", "confidence": 1.0, "evidenceFiles": [], "operations": ["READ"], "backendEvidence": [{"kind": "TABLE_OR_VIEW", "evidence": "JOIN MUSER"}]},
                                {"schema": "dbo", "name": "MSTOCKLEDGER", "confidence": 1.0, "operations": ["WRITE"],
                                 "writeFiles": ["GB5Solution/MM/MMDAL/Query/StockLedger/StockLedgerQB.cs"], "evidenceFiles": []},
                                {"schema": "dbo", "name": "MITEMCATEGORYLOG", "confidence": .6, "evidenceFiles": []}]}
    _w(m / "phase13-identity/screen_registry_v13.json", json.dumps(reg))
    _w(m / "phase14-api-mapping/screen_api_backend_mapping_v14.json", json.dumps([
        {"screenId": "INVENTORY_ITEM_CATEGORY", "apiMappings": [api], "unresolvedFormActions": []},
        {"screenId": "INVENTORY_STOCK_LEDGER", "apiMappings": [], "unresolvedFormActions": [{"formName": "stockledger", "kind": "get", "url": "/mms/Ledger.svc/", "entity": "Ledger"}]}]))
    _w(m / "phase15-db-mapping/screen_api_database_mapping_v15.json", json.dumps([
        {"screenId": "INVENTORY_ITEM_CATEGORY", "apiMappings": [api]}, {"screenId": "INVENTORY_STOCK_LEDGER", "apiMappings": []}]))
    return m


def test_usage_catalog_separates_verified_and_review_claims(tmp_path):
    be = tmp_path / "be"; _gb5_backend(be)
    m = _module_dir(tmp_path)
    knowledge = {"INVENTORY_ITEMCATEGORY": {"records": [{"knowledgeType": "BusinessSummary", "content": "Manage item categories.", "needsReview": False}], "source": "test"}}
    cat = build_usage_catalog("inventory", m, be, knowledge, None, m / "phase19-usage")
    f = {x["screenId"]: x for x in cat["features"]}
    ic = f["INVENTORY_ITEM_CATEGORY"]
    texts = {c["kind"]: c for c in ic["claims"]}
    assert texts["form"]["status"] == "VERIFIED" and "3 data fields, 2 of them mandatory" in texts["form"]["text"]
    assert "Item Category Code and Item Category Name are filled in" in texts["validation"]["text"]
    assert any(c["kind"] == "business_rule" and "Rate cannot be negative" in c["text"] for c in ic["claims"])
    assert not any("GetItemCategoryReport" in c["text"] for c in ic["claims"])
    assert texts["automation"]["status"] == "VERIFIED"
    assert texts["data"]["tables"] == ["MITEMCATEGORY"]
    assert texts["integration"]["tables"] == ["MITEM"]  # MUSER is a platform table
    # A write reached through another entity's logic is related, not owned.
    assert texts["related_data"]["status"] == "REVIEW" and texts["related_data"]["tables"] == ["MSTOCKLEDGER"]
    assert cat["statistics"]["aiDraftsToReview"] == 1
    assert any(c["status"] == "REVIEW" and "MITEMCATEGORYLOG" in c["text"] for c in ic["claims"])
    # Knowledge keyed by an alias ID is found; AI copy is always a draft.
    assert ic["summary"]["text"] == "Manage item categories." and ic["summary"]["status"] == "REVIEW"
    assert ic["readiness"] == "READY"
    assert ic["capability"]["name"] == "Item & Catalogue Management" and ic["capability"]["needsReview"] is True
    sl = f["INVENTORY_STOCK_LEDGER"]
    assert sl["readiness"] == "NEEDS_EVIDENCE" and sl["capability"]["name"] == "Reports & Analytics"
    assert sl["migrationGaps"][0]["entity"] == "Ledger"
    assert (m / "phase19-usage/usage_catalog_v19.json").exists()
    assert (m / "phase19-usage/usage_feature_sheet_v19.csv").read_text(encoding="utf-8").startswith("Capability,Feature")


def test_capability_map_override_is_confirmed(tmp_path):
    be = tmp_path / "be"; _gb5_backend(be)
    m = _module_dir(tmp_path)
    cmap = tmp_path / "capability_map.json"
    cmap.write_text(json.dumps({"INVENTORY_ITEMCATEGORY": {"capability": "Product Catalogue", "basis": "human_confirmed"}}))
    cat = build_usage_catalog("inventory", m, be, {}, cmap)
    ic = next(x for x in cat["features"] if x["screenId"] == "INVENTORY_ITEM_CATEGORY")
    assert ic["capability"] == {"name": "Product Catalogue", "basis": "human_confirmed", "needsReview": False}


def test_static_export_embeds_catalog(tmp_path):
    be = tmp_path / "be"; _gb5_backend(be)
    cat = build_usage_catalog("inventory", _module_dir(tmp_path), be, {})
    out = export_static_ui([cat], tmp_path / "ui.html")
    html = out.read_text(encoding="utf-8")
    assert "window.__PIE_USAGE__=" in html and "INVENTORY_ITEM_CATEGORY" in html
    assert "<!--PIE_EMBEDDED_DATA-->" not in html


def test_usage_api_serves_ui_and_catalog(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    be = tmp_path / "be"; _gb5_backend(be)
    m = _module_dir(tmp_path)
    build_usage_catalog("inventory", m, be, {}, None, m / "phase19-usage")
    monkeypatch.setenv("PIE_MODULES_OUTPUT", str(tmp_path / "modules"))
    from pie_agent.api import app, _upload_path
    c = TestClient(app)
    info = c.get("/").json()
    assert info["service"] == "PIE backend API" and "/usage/api/chat" in info["api"]
    # A page on the frontend dev server may call the API; other sites may not.
    ok = c.get("/usage/api/modules", headers={"Origin": "http://localhost:5500"})
    assert ok.headers.get("access-control-allow-origin") == "http://localhost:5500"
    assert "access-control-allow-origin" not in c.get("/usage/api/modules", headers={"Origin": "https://evil.example"}).headers
    assert c.get("/usage/api/modules").json()[0]["module"] == "inventory"
    assert c.get("/usage/api/catalog/inventory").json()["statistics"]["features"] == 2
    assert c.get("/usage/api/catalog/nope").status_code == 404
    assert c.get("/usage/api/catalog/..%2F..").status_code in (400, 404)
    assert _upload_path(tmp_path, "../../evil.txt") == tmp_path / "evil.txt"


def test_sql_operations_are_all_recorded(tmp_path):
    from pie_agent.db_mapping import sql_operation
    assert sql_operation("INTO MITEMCATEGORY") == "WRITE" and sql_operation("JOIN MUSER") == "READ"
    _w(tmp_path / "MMDAL/Query/X/XQB.cs", 'public const string A = @"SELECT * FROM MX"; public const string B = @"INSERT INTO MX (A) VALUES (1)";')
    refs = discover_backend_db_references(tmp_path)
    mx = next(r for r in refs[0]["references"] if r["name"] == "MX" and r["kind"] == "TABLE_OR_VIEW")
    assert set(mx["operations"]) == {"READ", "WRITE"}


def test_run_together_names_are_segmented_with_erp_vocabulary():
    from pie_agent.usage import build_vocabulary, marketing_name
    vocab = build_vocabulary([{"screenName": "Item Sub Category"}, {"screenName": "Production Plan"}, {"fields": [{"Name": "DailyQty"}]}])
    assert marketing_name({"screenName": "Itemsubcategory", "module": "inventory"}, vocab) == "Item Sub Category"
    assert marketing_name({"screenName": "Inventoryproductionplandaily", "module": "inventory"}, vocab) == "Production Plan Daily"
    assert marketing_name({"screenName": "Xyzzyqwert", "module": "inventory"}, vocab) == "Xyzzyqwert"
