"""Every field and section point carries the whole chain (module -> functionality -> capability -> screen -> field),
and a display field (DepartmentName) is linked to the ID field that is actually stored (DepartmentId)."""
import json

from pie_agent.field_intel import field_summary_text, link_display_fields
from pie_agent.knowledge_ingest import field_points, publish_usage_catalog, screen_contexts, section_points
from pie_agent.qdrant_kms import QdrantKMS
from test_phase19_chat import CATALOG, FakeQdrant


def _field(name, column="", table="MEMPLOYEE", lookup=None, fk=None):
    return {"fieldName": name, "lookup": lookup or {}, "relatedFields": [], "evidence": [],
            "database": {"table": table if column else "", "column": column, "dataType": "int" if column else "", "foreignKey": fk,
                         "lookupDisplay": None, "childTable": None, "storage": "backend only (not stored in MEMPLOYEE)"}}


def test_display_field_is_linked_to_its_stored_id_field():
    recs = [_field("DepartmentName"), _field("DepartmentId", "DEPARTMENTID", fk={"table": "MDEPARTMENT", "column": "DEPARTMENTID"}),
            _field("LeaveTypeName", lookup={"linkField": "LeaveTypeId"}), _field("LeaveTypeId"),
            _field("ReasonName")]                                                   # no ID field next to it
    link_display_fields(recs, "formjson/employee.json")
    dep, leave, reason = recs[0]["database"], recs[2]["database"], recs[4]["database"]
    assert dep["storedVia"] == {"field": "DepartmentId", "table": "MEMPLOYEE", "column": "DEPARTMENTID", "dataType": "int",
                                "foreignKey": {"table": "MDEPARTMENT", "column": "DEPARTMENTID"}, "basis": "ID field of the same name"}
    assert dep["storage"] == "display of DepartmentId (stored as MEMPLOYEE.DEPARTMENTID)" and "DepartmentId" in recs[0]["relatedFields"]
    assert recs[0]["evidence"][0]["source"] == "formjson/employee.json"
    assert leave["storedVia"]["basis"] == "picklist link field" and "not stored by this screen" in leave["storage"]
    assert "storedVia" not in reason and "storedVia" not in recs[1]["database"]     # stored fields are left alone


def _module_dir(tmp_path):
    mdir = tmp_path / "modules" / "ess"
    (mdir / "phase13-identity").mkdir(parents=True)
    (mdir / "phase19-usage").mkdir(parents=True)
    (mdir / "phase13-identity" / "screen_registry_v13.json").write_text(json.dumps([
        {"screenId": "ESS_EMP", "functionality": "ESS"}, {"screenId": "INVENTORY_BOM", "functionality": "Inventory"}]), encoding="utf-8")
    (mdir / "phase19-usage" / "usage_catalog_v19.json").write_text(json.dumps({"features": [
        {"screenId": "ESS_EMP", "aliasScreenIds": ["ESS_EMP_OLD"],
         "capability": {"name": "Profile & Directory", "basis": "keyword_taxonomy_candidate", "needsReview": True}}]}), encoding="utf-8")
    return mdir


def test_screen_context_comes_from_registry_and_usage_catalog(tmp_path):
    ctx = screen_contexts(_module_dir(tmp_path))
    assert ctx["ESS_EMP"] == {"functionality": "ESS", "capability": "Profile & Directory",
                              "capability_basis": "keyword_taxonomy_candidate", "capability_needs_review": True}
    assert ctx["ESS_EMP_OLD"]["capability"] == "Profile & Directory"   # alias screen ids share the feature's capability
    assert screen_contexts(tmp_path / "missing") == {}


def test_field_and_section_points_carry_the_context(tmp_path):
    rec = _field("DepartmentName")
    rec["database"]["storedVia"] = {"field": "DepartmentId", "table": "MEMPLOYEE", "column": "DEPARTMENTID", "foreignKey": None}
    rec.update({"screenId": "ESS_EMP", "screen": "Employee", "module": "ess", "label": "Department Name", "page": "", "section": "Header",
                "visibleOnScreen": True, "type": {"ui": "Text", "form": "string", "backend": "", "database": ""},
                "mandatory": {"required": False, "conditional": None, "backendValidated": False, "databaseNotNull": None},
                "defaultValue": None, "allowedValues": [], "validations": [], "messages": [], "dependencies": [], "calculation": None,
                "frontend": {}, "usage": {"modules": {}, "reports": []}, "configuration": [], "relatedMasters": []})
    d = {"screenId": "ESS_EMP", "screen": "Employee", "module": "ess", "fields": [rec]}
    assert "kept in DepartmentId, stored in MEMPLOYEE.DEPARTMENTID" in field_summary_text(rec)
    kms = QdrantKMS(FakeQdrant(), "pie_knowledge")
    ctx = screen_contexts(_module_dir(tmp_path))["ESS_EMP"]
    fp = field_points(d, kms, ctx)[0]["payload"]
    assert (fp["functionality"], fp["capability"], fp["capability_needs_review"]) == ("ESS", "Profile & Directory", True)
    assert fp["field_column"] == "DEPARTMENTID" and fp["field_stored_via"] == "DepartmentId"
    sp = section_points(d, kms, ctx)[0]["payload"]
    assert (sp["functionality"], sp["capability"]) == ("ESS", "Profile & Directory")
    assert field_points(d, kms)[0]["payload"]["capability"] == ""          # no context: unchanged behaviour


def test_usage_points_get_functionality():
    kms = QdrantKMS(FakeQdrant(), "pie_knowledge")
    publish_usage_catalog(kms, CATALOG, functionality={"INVENTORY_BOM": "Inventory", "INVENTORY_GATE_ENTRY": "Inventory"})
    by_type = {}
    for p in kms.client.points.values():
        by_type.setdefault(p["payload"]["knowledge_type"], []).append(p["payload"]["functionality"])
    assert set(by_type["UsageFeature"]) == {"Inventory"} and by_type["UsageModule"] == ["Inventory"]
    assert set(by_type["UsageCapability"]) == {"Inventory"}
