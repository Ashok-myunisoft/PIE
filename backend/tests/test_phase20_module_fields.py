"""Module-wide field knowledge (ESS-style forms): entity discovery, literal labels, sections, one usage scan, multi-screen chat."""
import json
from pathlib import Path

import pytest

from pie_agent import chat as chat_mod
from pie_agent.chat import MarketingChat, all_fields_answer, load_snapshot, match_fields, mentioned_screens
from pie_agent.field_intel import build_module_fields, parse_html_controls
from pie_agent.knowledge_ingest import publish_field_dictionary
from pie_agent.qdrant_kms import QdrantKMS
from test_phase19_chat import FakeLLM, FakeQdrant


def _w(p: Path, text: str):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def test_sections_from_html_comments_and_headers():
    html = ('<!-- Current Data Section --><gb-input formControlName="A"></gb-input>'
            '<div class="section-header">Leave*</div><gb-date formControlName="B"></gb-date>')
    c = parse_html_controls(html)
    assert c["A"]["section"] == "Current Data" and c["B"]["section"] == "Leave"


@pytest.fixture
def ess(tmp_path):
    root = tmp_path / "erp"
    fe, be = root / "frontend" / "projects", root / "backend" / "GB5Solution"
    _w(fe / "gbhost/public/formactionurls/geturl.ts", "export class GetUrl {\n  public static leaverequest = '/prs/TLeave.svc/?TLeaveId='\n}")
    _w(fe / "gbhost/public/formactionurls/saveurl.ts", "export class SaveUrl {\n  public static leaverequest = '/prs/TLeave.svc/'\n}")
    _w(fe / "gbhost/public/i18n/English.json", "{}")
    _w(fe / "gbhost/public/picklist/allpicklist.json", "{}")
    fields = [{"Name": "TLeaveLeaveDate", "Type": "string", "Label": "Leave Date*", "Required": True},
              {"Name": "TLeaveFromDate", "Type": "string", "Label": "From Date", "Required": False},
              {"Name": "ReasonName", "Type": "string", "Label": "Reason*", "Required": True}]
    _w(fe / "ess/public/formjson/leaverequest.json", json.dumps({"ObjectFields": fields}))
    _w(fe / "ess/public/formjson/permissionrequest.json", json.dumps({"ObjectFields": [
        {"Name": "TimeSlipFromDate", "Type": "string", "Label": "From Date", "Required": True}]}))
    _w(fe / "ess/transaction/leave/leaverequest/leaverequest.component.html",
       '<!-- Current Data Section --><gb-date formControlName="TLeaveLeaveDate"></gb-date>'
       '<div class="section-header">Leave</div><gb-date formControlName="TLeaveFromDate"></gb-date><gb-newpicklist formControlName="ReasonName"></gb-newpicklist>')
    _w(fe / "ess/transaction/leave/leaverequest/leaverequest.component.ts", "export class L { }")
    _w(fe / "ess/transaction/attendance/permissionrequest/permissionrequest.component.html", '<gb-date formControlName="TimeSlipFromDate"></gb-date>')
    _w(fe / "ess/transaction/attendance/permissionrequest/permissionrequest.component.ts", "export class P { }")
    _w(be / "PayRoll/PayRollDAL/DTO/TLeave/TLeaveDTO.cs", "public string TLeaveLeaveDate { get { return a; } }\npublic string TLeaveFromDate { get { return b; } }")
    _w(be / "PayRoll/PayRollDAL/Query/TLeave/TLeaveQB.cs",
       'public const string SAVE_TLEAVE = @"INSERT INTO TLEAVE (LEAVEDATE, FROMDATE) VALUES (@TLeaveLeaveDate, @TLeaveFromDate)";')
    _w(be / "PayRoll/PayRollDAL/CustomeCode/TLeave/TLeaveDAL.cs", "public class TLeaveDAL { }")   # PayRoll spells it CustomeCode
    _w(be / "PayRoll/PayRollDAL/Query/Report/LeaveReportQB.cs", 'const string R = @"SELECT L.FROMDATE FROM TLEAVE L";')
    mdir = tmp_path / "modules" / "ess"
    _w(mdir / "phase13-identity/screen_registry_v13.json", json.dumps([
        {"screenId": "ESS_LEAVE_REQUEST", "screenName": "Leave Request", "module": "ess",
         "componentPath": "projects/ess/transaction/leave/leaverequest/leaverequest.component.ts"},
        {"screenId": "ESS_PERMISSION_REQUEST", "screenName": "Permission Request", "module": "ess",
         "formDefinitionPath": "projects/ess/public/formjson/permissionrequest.json"}]))
    return root, mdir


def test_module_build_discovers_entity_labels_sections_and_usage(ess):
    root, mdir = ess
    ds = {d["form"]: d for d in build_module_fields(root, "ess", mdir)}
    lr = ds["leaverequest"]
    assert lr["entity"] == "TLeave" and lr["entitySource"] == "form action URL" and lr["table"] == "TLEAVE"
    assert lr["screenId"] == "ESS_LEAVE_REQUEST" and lr["screen"] == "Leave Request"
    f = {r["fieldName"]: r for r in lr["fields"]}
    assert f["TLeaveLeaveDate"]["label"] == "Leave Date" and "label text" in f["TLeaveLeaveDate"]["labelSource"]
    assert f["TLeaveLeaveDate"]["section"] == "Current Data" and f["ReasonName"]["section"] == "Leave"
    assert f["TLeaveFromDate"]["database"]["column"] == "FROMDATE"
    assert f["TLeaveFromDate"]["usage"]["modules"] == {"PayRoll": 1} and f["TLeaveFromDate"]["usage"]["reports"]
    assert ds["permissionrequest"]["backendFound"] is False  # no entity: reported, not guessed
    summary = json.loads((mdir / "phase20-fields/module_fields_summary_v20.json").read_text(encoding="utf-8"))
    assert summary["totals"]["fields"] == 4 and len(summary["screens"]) == 2
    assert (mdir / "phase20-fields/leaverequest_fields_v20.xlsx").exists()


@pytest.fixture
def ess_kms(ess):
    root, mdir = ess
    chat_mod._SNAPSHOTS.clear()
    kms = QdrantKMS(FakeQdrant(), "pie_knowledge")
    results = [publish_field_dictionary(kms, d) for d in build_module_fields(root, "ess", mdir)]
    assert sum(r["sections"] for r in results) == 3  # Current Data, Leave, (main form) of permission
    return kms


def test_chat_is_screen_aware(ess_kms):
    snap = load_snapshot(ess_kms)
    assert mentioned_screens("What is From Date in Leave Request?", snap) == {"Leave Request"}
    only = match_fields("What is From Date in Leave Request?", snap, screens={"Leave Request"})
    assert [p["field_name"] for p in only] == ["TLeaveFromDate"]
    spread = match_fields("What is the From Date field?", snap)
    assert {p["screen_name"] for p in spread} == {"Leave Request", "Permission Request"}
    text, items = all_fields_answer(snap)
    assert "2 screens, 4 field definitions" in text and "| Leave Request | ess | 3 |" in text
    text, _ = all_fields_answer(snap, {"Leave Request"})
    assert "Leave Request: 3 field definitions" in text and "TimeSlipFromDate" not in text


def test_single_field_question_is_not_an_all_fields_question(ess_kms):
    # Regression: "the Reason field in Leave Request" matched the all-fields pattern.
    llm = FakeLLM("Reason is mandatory [K1].")
    r = MarketingChat(ess_kms, llm).answer("What is the Reason field in Leave Request and is it mandatory?")
    assert r["plan"]["generator"] != "structured" and r["sources"][0]["kind"] == "field"


def test_mandatory_fields_list_is_deterministic_and_grouped_by_section(ess_kms):
    llm = FakeLLM("unused")
    r = MarketingChat(ess_kms, llm).answer("Which fields are mandatory in Leave Request?")
    assert llm.calls == [] and r["plan"]["generator"] == "structured"
    assert "2 mandatory fields (of 3)" in r["answer"] and "| Leave Date | `TLeaveLeaveDate` | Current Data | always |" in r["answer"]
    text, _ = all_fields_answer(load_snapshot(ess_kms), {"Leave Request"})
    assert "### Current Data" in text and "### Leave" in text   # sections, not "not shown on a tab"


def test_section_question_uses_section_knowledge(ess_kms):
    llm = FakeLLM("The Leave section has From Date and Reason [K1].")
    r = MarketingChat(ess_kms, llm).answer("Which fields are in the Leave section of Leave Request?")
    prompt = llm.calls[-1][1]
    assert r["intent"] == "field" and "Leave Request section: Leave" in prompt


def test_db_mapping_uses_live_snapshot_when_sql_server_is_down(tmp_path, monkeypatch):
    from pie_agent import db_mapping
    monkeypatch.setattr(db_mapping, "scan_sqlserver", lambda url: {"status": "ERROR", "error": "timed out", "objects": []})
    snap = tmp_path / "database_object_registry_v15.json"
    snap.write_text(json.dumps([{"name": "TLEAVE", "type": "TABLE", "columns": [{"name": "FROMDATE", "dataType": "date"}]}]), encoding="utf-8")
    objs, meta = db_mapping._load_db_objects("mssql://unreachable", tmp_path, snap)
    assert meta["source"] == "live_sqlserver_snapshot" and objs[0]["columns"]
