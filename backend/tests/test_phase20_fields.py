import json
from pathlib import Path

import pytest

from pie_agent import chat as chat_mod
from pie_agent.chat import MarketingChat, all_fields_answer, detect_intents, load_snapshot, match_fields
from pie_agent.field_intel import (enrich_fields, export_fields_xlsx, extract_screen_fields, parse_html_controls, parse_insert,
                                   parse_select, parse_ts_methods, validate_explanations)
from pie_agent.knowledge_ingest import publish_field_dictionary
from pie_agent.llm import LLMProvider
from pie_agent.qdrant_kms import QdrantKMS
from test_phase19_chat import FakeLLM, FakeQdrant  # pytest puts tests/ on sys.path


def _w(p: Path, text: str):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def test_insert_mapping_is_positional_not_by_name():
    table, m = parse_insert("INSERT INTO MITEM\n(\n ITEMID,\n CATEGORYID, -- category\n ITEMCODE\n)\nVALUES\n(\n @ItemId,\n @ItemCategoryId,\n @ItemCode\n)")
    assert table == "MITEM" and m == {"ItemId": "ITEMID", "ItemCategoryId": "CATEGORYID", "ItemCode": "ITEMCODE"}


def test_select_aliases_and_lookup_joins():
    props, aliases = parse_select("SELECT MI.ITEMCODE AS ItemCode, CAT.ITEMCATEGORYNAME AS ItemCategoryName FROM MITEM MI "
                                  "LEFT JOIN MITEMCATEGORY CAT ON CAT.ITEMCATEGORYID = MI.CATEGORYID", "MITEM")
    assert aliases == {"MI": "MITEM", "CAT": "MITEMCATEGORY"}
    assert props["ItemCategoryName"] == {"alias": "CAT", "column": "ITEMCATEGORYNAME", "table": "MITEMCATEGORY"}


def test_html_controls_tabs_events_and_visibility():
    html = ('<mat-tab label="Sales"><gb-radiobutton formControlName=\'ItemIsSaleable\' (RadioButtonOutPut)="onSale($event)"></gb-radiobutton>'
            '<gb-input formControlName="ItemRate" *ngIf="form.get(\'ItemIsSaleable\')?.value == 0"></gb-input></mat-tab>')
    c = parse_html_controls(html)
    assert c["ItemIsSaleable"]["tab"] == "Sales" and c["ItemIsSaleable"]["events"] == [{"event": "RadioButtonOutPut", "handler": "onSale"}]
    assert "ItemIsSaleable" in c["ItemRate"]["visibleIf"]


def test_ts_methods_reads_and_patches():
    m = parse_ts_methods("public onSale(e: any) { if (this.form.get('ItemIsSaleable')?.value == 1) { this.form.get('ItemRate')?.patchValue(0) } }")
    assert m["onSale"]["reads"] == ["ItemIsSaleable"] and m["onSale"]["patches"] == ["ItemRate"]


@pytest.fixture
def erp(tmp_path):
    root = tmp_path / "erp"
    fe, be = root / "frontend" / "projects", root / "backend"
    _w(fe / "inventory/public/formjson/itemmaster.json", json.dumps({"ObjectFields": [
        {"Name": "ItemId", "Type": "number", "Required": False, "DefaultValue": 0},
        {"Name": "CodeDefineGenerationType", "Type": "number", "Required": True, "DefaultValue": 1},
        {"Name": "ItemCode", "Type": "string", "Label": "Common.code", "Required": True, "MaxLength": 60, "PicklistJSON": "itemmastercode",
         "LinkId": "ItemId", "RequireBasedOnFieldName": "CodeDefineGenerationType", "RequireBasedOnFieldValue": [1]},
        {"Name": "ItemGroupName", "Type": "string", "Label": "ItemMaster.Group", "Required": True, "PicklistJSON": "ItemGroup", "LinkId": "ItemGroupId"},
        {"Name": "ItemGroupId", "Type": "number", "Required": False, "DefaultValue": -1},
        {"Name": "ItemIsSaleableItem", "Type": "number", "Label": "ItemMaster.SaleableItem", "Required": False, "DefaultValue": 0,
         "PickLists": [{"key": 0, "value": "Yes"}, {"key": 1, "value": "No"}]},
        {"Name": "ItemFixedLeadTime", "Type": "number", "Label": "ItemMaster.SetUp", "Required": False, "DefaultValue": 0},
        {"Name": "ItemLotSize", "Type": "number", "Label": "ItemMaster.Lot", "Required": False, "DefaultValue": 1},
        {"Name": "ItemManufacturingLeadTime", "Type": "number", "Label": "ItemMaster.Mfr", "Required": False, "DefaultValue": 0},
        {"Name": "ItemShortName", "Type": "string", "Label": "Branch.ShortName", "Required": True},
        {"Name": "ItemShortName", "Type": "string", "Required": False},
        {"Name": "ItemStockDTO", "Type": "string", "Required": False, "Grid": [{"value": "StoreId"}, {"value": "ItemStockRol"}, {"value": "BinId"}]},
    ]}))
    _w(fe / "inventory/public/metaform/itemmaster.meta.json", json.dumps({
        "codeDefine": {"generationTypeFieldId": "CodeDefineGenerationType"},
        "pages": [{"pageName": "Classification", "sections": [{"sectionName": "Item Identity", "fields": [{"Name": "ItemCode"}]}]}],
        "rules": [{"type": "calculate", "trigger": ["ItemFixedLeadTime", "ItemLotSize"], "formula": "{ItemFixedLeadTime}/{ItemLotSize}",
                   "target": "ItemManufacturingLeadTime", "targetReadOnly": True}]}))
    _w(fe / "gbhost/public/i18n/English.json", json.dumps({"Common": {"code": "Code*"}, "ItemMaster": {
        "Group": "Group*", "SaleableItem": "Saleable Item", "SetUp": "Set-Up Time", "Lot": "Lot Size", "Mfr": "Mfr.Lead Time"}, "Branch": {"ShortName": "Short Name"}}))
    _w(fe / "gbhost/public/picklist/allpicklist.json", json.dumps({"itemmastercode": {"PicklistUrl": "Items", "PicklistDisplayLabel": " ,Code,Name"},
                                                                     "ItemGroup": {"PicklistUrl": "ItemGroup"}}))
    _w(fe / "inventory/master/itemmenu/itemmaster/itemmaster.component.html",
       '<mat-tab label="Classification*"><gb-newpicklist formControlName="ItemCode" (PicklistValue)="OnpicklistLoad($event)" [gbReadOnly]="isAuto"></gb-newpicklist>'
       '<gb-newpicklist formControlName="ItemGroupName"></gb-newpicklist></mat-tab><mat-tab label="Sales"><gb-radiobutton formControlName="ItemIsSaleableItem"></gb-radiobutton></mat-tab>'
       '<mat-tab label="Planning"><gb-input formControlName="ItemFixedLeadTime"></gb-input><gb-input formControlName="ItemManufacturingLeadTime"></gb-input></mat-tab>')
    _w(fe / "inventory/master/itemmenu/itemmaster/itemmaster.component.ts",
       "export class C { public OnpicklistLoad(e: any) { this.form.get('ItemStockDTO')?.patchValue([]) } }")
    _w(fe / "inventory/service/itemmaster.service.ts", 'alertdata.push(this.translate.translate("" + jsonvalue.Label) + " Should not be Empty")')
    _w(fe / "sales/transaction/salesorder/salesorder.component.ts", "x = row.ItemIsSaleableItem")
    _w(be / "GB5Shared/Validation/Validation.cs", 'public async Task NotEmpty(string value, string? fieldName = null) { throw new ValidationException($"{field} cannot be empty or whitespace."); }')
    _w(be / "GB5Solution/MM/MMDAL/DTO/Item/ItemDTO.cs", "public string ItemCode { get { return a; } }\npublic byte ItemIsSaleableItem { get { return b; } }\npublic int ItemGroupId { get { return c; } }")
    _w(be / "GB5Solution/MM/MMBLL/Item/ItemBLL.cs", "await _Validation.NotEmpty(ItemDTO.ItemCode, nameof(ItemDTO.ItemCode));")
    _w(be / "GB5Solution/MM/MMDAL/Query/Item/ItemQB.cs",
       'public const string SAVE_ITEM = @"INSERT INTO MITEM (ITEMID, ITEMCODE, ITEMGROUPID, ISSALEABLEITEM, FIXEDLEADTIME, MANUFACTURINGLEADTIME) '
       'VALUES (@ItemId, @ItemCode, @ItemGroupId, @ItemIsSaleableItem, @ItemFixedLeadTime, @ItemManufacturingLeadTime)";\n'
       'public const string GET_ITEM = @"SELECT MI.ITEMCODE AS ItemCode, IG.ITEMGROUPNAME AS ItemGroupName FROM MITEM MI LEFT JOIN MITEMGROUP IG ON IG.ITEMGROUPID = MI.ITEMGROUPID";')
    _w(be / "GB5Solution/MM/MMDAL/Query/ItemStock/ItemStockQB.cs",
       'public const string SAVE_ITEMSTOCK = @"INSERT INTO MITEMSTOCK (STOREID, ROL, BINID) VALUES (@StoreId, @ItemStockRol, @BinId)";')
    _w(be / "GB5Solution/Costing/CostingDAL/Query/Cost/CostQB.cs", 'const string Q = @"SELECT I.MANUFACTURINGLEADTIME FROM MITEM I";')
    _w(be / "GB5Solution/Admin/AdminDAL/Query/Cat/CatQB.cs", 'const string Q = @"SELECT ITEMCODE FROM MITEMCATEGORY";')
    reg = tmp_path / "db.json"
    reg.write_text(json.dumps([{"name": "MITEM", "primaryKeys": ["ITEMID"], "columns": [
        {"name": "ITEMCODE", "dataType": "nvarchar", "nullable": False, "maxLength": 60},
        {"name": "ISSALEABLEITEM", "dataType": "tinyint", "nullable": False, "maxLength": None},
        {"name": "ITEMGROUPID", "dataType": "int", "nullable": False, "maxLength": None}],
        "foreignKeys": [{"column": "ITEMGROUPID", "refTable": "MITEMGROUP", "refColumn": "ITEMGROUPID"}]}]))
    return root, reg


def test_extract_screen_fields_end_to_end(erp):
    root, reg = erp
    d = extract_screen_fields(root, db_registry=reg)
    f = {r.get("fieldKey"): r for r in d["fields"]}
    assert d["table"] == "MITEM" and d["statistics"]["fields"] == 12 and d["statistics"]["uniqueFields"] == 11
    code = f["ItemCode"]
    assert code["label"] == "Code" and code["page"] == "Classification" and code["section"] == "Item Identity"
    assert code["database"]["column"] == "ITEMCODE" and code["type"]["database"] == "nvarchar(60)"
    assert code["mandatory"] == {"required": True, "conditional": {"field": "CodeDefineGenerationType", "values": [1]}, "backendValidated": True, "databaseNotNull": True}
    msgs = {m["message"] for m in code["messages"]}
    assert msgs == {"Code Should not be Empty", "ItemCode cannot be empty or whitespace."}
    assert any("Code Define configuration" in c for c in code["configuration"])
    assert any(d_["kind"] == "changing it updates" and d_["fields"] == ["ItemStockDTO"] for d_ in code["dependencies"])
    assert code["lookup"]["source"] == "Items"
    # Lookup display field comes from the related master; hidden ID gets the lookup's label.
    assert f["ItemGroupName"]["database"]["lookupDisplay"] == {"table": "MITEMGROUP", "column": "ITEMGROUPNAME"}
    assert f["ItemGroupId"]["label"] == "Group (internal ID)"
    assert any("MITEMGROUP.ITEMGROUPID" in v["rule"] for v in f["ItemGroupId"]["validations"])
    sale = f["ItemIsSaleableItem"]
    assert sale["allowedValues"] == [{"value": 0, "label": "Yes"}, {"value": 1, "label": "No"}] and sale["page"] == "Sales"
    assert "sales/transaction:salesorder" in sale["usage"]["screens"]
    mfr = f["ItemManufacturingLeadTime"]
    assert mfr["calculation"]["formula"] == "{ItemFixedLeadTime}/{ItemLotSize}"
    assert mfr["usage"]["modules"] == {"Costing": 1}  # MITEMCATEGORY file is not counted as MITEM usage
    assert any(x["kind"] == "used in calculation of" for x in f["ItemFixedLeadTime"]["dependencies"])
    assert f["ItemStockDTO"]["database"]["childTable"]["table"] == "MITEMSTOCK"
    assert f["ItemShortName#10"]["duplicateDefinition"] and "different label, mandatory" in f["ItemShortName#11"]["duplicateDefinition"]
    assert "Branch" in f["ItemShortName#10"]["labelNote"]


class _Explainer(LLMProvider):
    def __init__(self):
        self.calls = 0

    def generate(self, prompt):
        self.calls += 1
        names = [x["fieldName"] for x in json.loads(prompt.split("FACTS:\n", 1)[1])]
        return json.dumps({"fields": [{"fieldName": n, "purpose": "p", "businessMeaning": "m",
                                       "businessUsage": "Used in the Sales module for orders" if n == "ItemGroupId" else "Used in Item Master",
                                       "transactionImpact": "Not determinable from sources", "example": "x", "businessRules": "r",
                                       "confidence": "high"} for n in names]})


def test_enrichment_is_cached_and_validated(erp, tmp_path):
    root, reg = erp
    d = extract_screen_fields(root, db_registry=reg)
    llm = _Explainer()
    d = validate_explanations(enrich_fields(d, llm, tmp_path / "cache.json", batch_size=20))
    assert d["statistics"]["explained"] == 12 and llm.calls == 1
    g = next(r for r in d["fields"] if r["fieldName"] == "ItemGroupId")["explanation"]
    assert g["unverifiedMentions"] == ["Sales"] and g["confidence"] == "low" and "not in the extracted usage evidence" in g["reviewNote"]
    d2 = enrich_fields(extract_screen_fields(root, db_registry=reg), llm, tmp_path / "cache.json")
    assert llm.calls == 1 and d2["statistics"]["explained"] == 12  # no second paid call
    x = export_fields_xlsx(d, tmp_path / "f.xlsx")
    assert x.exists() and x.stat().st_size > 4000


@pytest.fixture
def field_kms(erp, tmp_path):
    root, reg = erp
    d = validate_explanations(enrich_fields(extract_screen_fields(root, db_registry=reg), _Explainer(), tmp_path / "c.json", batch_size=20))
    chat_mod._SNAPSHOTS.clear()
    k = QdrantKMS(FakeQdrant(), "pie_knowledge")
    publish_field_dictionary(k, d)
    return k, d


def test_field_questions_in_chat(field_kms):
    kms, d = field_kms
    assert detect_intents("I need all the features of each field in Item Master.") == ["field"]
    snap = load_snapshot(kms)
    assert [p["field_name"] for p in match_fields("What happens if I change the fixed lead time?", snap)] == ["ItemFixedLeadTime"]
    assert [p["field_name"] for p in match_fields("Is Saleable Item mandatory?", snap)] == ["ItemIsSaleableItem"]
    text, items = all_fields_answer(snap)
    assert "12 field definitions (11 unique)" in text and "| Code | `ItemCode` |" in text and "Source conflict" in text
    llm = FakeLLM("It is required when codes are manual [K1].")
    r = MarketingChat(kms, llm).answer("Why is Item Code required?")
    assert r["intent"] == "field" and r["sources"][0]["kind"] == "field" and "ItemCode" in r["sources"][0]["title"]
    r = MarketingChat(kms, FakeLLM("x")).answer("I need all the features of each field in Item Master.")
    assert r["plan"]["generator"] == "structured" and r["grounding"]["status"] == "grounded"
    # Change questions bring in the fields the change affects.
    llm = FakeLLM("It feeds the manufacturing lead time [K1][K2].")
    MarketingChat(kms, llm).answer("What happens if I change the Fixed Lead Time?")
    assert "ItemManufacturingLeadTime" in llm.calls[-1][1]


def test_flagged_usage_text_is_not_sent_to_the_chat_model(field_kms):
    kms, _ = field_kms
    llm = FakeLLM("ok [K1]")
    MarketingChat(kms, llm).answer("Tell me about the ItemGroupId field")
    prompt = llm.calls[-1][1]
    assert "Used in the Sales module" not in prompt and "not verified" in prompt


def test_field_pages_and_api(erp, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    root, reg = erp
    mods = tmp_path / "modules"
    d = extract_screen_fields(root, db_registry=reg)
    out = mods / "inventory" / "phase20-fields"
    out.mkdir(parents=True)
    (out / "itemmaster_fields_v20.json").write_text(json.dumps(d, default=str), encoding="utf-8")
    export_fields_xlsx(d, out / "itemmaster_fields_v20.xlsx")
    monkeypatch.setenv("PIE_MODULES_OUTPUT", str(mods))
    from pie_agent.api import app
    c = TestClient(app)
    assert c.get("/usage/fields").status_code == 404  # pages belong to the frontend project
    lst = c.get("/usage/api/fields").json()
    assert lst[0]["screen"] == "itemmaster" and lst[0]["excel"] is True
    assert c.get("/usage/api/fields/itemmaster").json()["statistics"]["fields"] == 12
    x = c.get("/usage/api/fields/itemmaster/excel")
    assert x.status_code == 200 and x.content[:2] == b"PK"
    assert c.get("/usage/api/fields/..%2Fsecret").status_code in (400, 404)
    assert c.get("/usage/api/fields/nothere").status_code == 404


def test_field_explorer_export(erp, tmp_path):
    from pie_agent.usage import export_fields_ui
    root, reg = erp
    out = export_fields_ui([extract_screen_fields(root, db_registry=reg)], tmp_path / "fx.html")
    html = out.read_text(encoding="utf-8")
    assert "window.__PIE_FIELDS__=" in html and "ItemManufacturingLeadTime" in html and "<!--PIE_EMBEDDED_DATA-->" not in html
