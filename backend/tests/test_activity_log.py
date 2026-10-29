"""Activity log: status, counts, errors, secrets, nesting, remote spool + ingest, inventory, CLI and API tracking."""
import json

import pytest

from pie_agent import activity_log as log


def test_success_run_records_counts_events_and_module():
    with log.track("field_discovery", command="fields-build-module", module="ess") as run:
        run.event("pass 1: Leave Request done")
        log.record_result({"status": "COMPLETED", "screens": 27, "fields": 966, "stats": {"mapped": 318}, "names": ["a", "b"]})
    r = log.get_run(run.run_id)
    assert r["status"] == "SUCCESS" and r["module"] == "ess" and r["finished_at"] and r["duration_ms"] >= 0
    assert r["counts"] == {"screens": 27, "fields": 966, "stats.mapped": 318, "names": 2}
    assert r["events"][0]["message"] == "pass 1: Leave Request done"


def test_failure_exit_codes_and_warnings():
    with pytest.raises(FileNotFoundError):
        with log.track("evidence_mapping", command="map-db") as failed:
            raise FileNotFoundError("missing.json")
    with pytest.raises(SystemExit):
        with log.track("other", command="bad-args") as bad:
            raise SystemExit(2)
    with log.track("evidence_mapping", command="map-db") as warned:
        log.record_result({"status": "COMPLETED", "database": {"status": "ERROR"}})
    f = log.get_run(failed.run_id)
    assert f["status"] == "FAILED" and f["error_type"] == "FileNotFoundError" and "missing.json" in f["traceback"]
    assert log.get_run(bad.run_id)["status"] == "FAILED"
    w = log.get_run(warned.run_id)
    assert w["status"] == "WARNING" and w["attrs"]["warnings"] == ["database.status=ERROR"]


def test_secrets_masked_and_sub_activities_linked():
    args = ["kms-publish", "--api-key", "abc", "--database-url=mssql://u:p@h/db", "--url", "http://a:b@h"]
    with log.track("qdrant_ingestion", params=args) as parent:
        with log.track("qdrant_status") as child:
            pass
    p = log.get_run(parent.run_id)
    assert p["params"] == ["kms-publish", "--api-key", "***", "--database-url=***", "--url", "http://***@h"]
    assert child.parent_id == parent.run_id and p["children"][0]["run_id"] == child.run_id


def test_summary_counts_chat_and_api_requests():
    with log.track("retrieval", command="chat", source="api") as run:
        run.count(totalSeconds=2.5).attr(grounding="grounded")
    log.log_api_request("POST", "/usage/api/chat", 200, 2500, "1.2.3.4")
    s = log.summary(24)
    assert s["totals"]["SUCCESS"] == 1 and s["chat"]["questions"] == 1 and s["chat"]["grounded"] == 1
    assert s["api"]["requests"] == 1 and log.api_stats(24)["paths"][0]["path"] == "/usage/api/chat"
    assert log.list_runs(activity="retrieval")["total"] == 1 and log.list_runs(status="FAILED")["total"] == 0


def test_unsent_runs_are_spooled_and_ingest_is_idempotent(monkeypatch):
    monkeypatch.setenv("PIE_LOG_REMOTE", "http://127.0.0.1:9/logs/api/ingest")  # nothing listens there
    monkeypatch.setenv("PIE_LOG_REMOTE_TIMEOUT", "0.5")
    with log.track("export", command="usage-export") as run:
        run.event("wrote catalog.html")
    body = json.loads(log._spool_path().read_text(encoding="utf-8").splitlines()[0])
    assert body["run"]["run_id"] == run.run_id and body["events"][0]["message"] == "wrote catalog.html"
    log.ingest_remote(body)
    log.ingest_remote(body)
    assert len(log.get_run(run.run_id)["events"]) == 1
    with pytest.raises(ValueError):
        log.ingest_remote({"run": {"run_id": "not-hex"}})


def test_inventory_counts_screens_sections_and_fields(tmp_path):
    fdir = tmp_path / "modules" / "ess" / "phase20-fields"
    fdir.mkdir(parents=True)
    fields = [{"section": "Leave", "visibleOnScreen": True}, {"section": "Leave", "visibleOnScreen": True},
              {"page": "General", "visibleOnScreen": True}, {"visibleOnScreen": False}]
    (fdir / "leaverequest_fields_v20.json").write_text(json.dumps({
        "screenId": "ESS_LR", "screen": "Leave Request", "module": "ess", "form": "leaverequest", "backendFound": True,
        "statistics": {"fields": 4, "uniqueFields": 4, "mandatory": 2, "mappedToColumn": 1, "explained": 4, "explanationsFlagged": 1},
        "fields": fields}), encoding="utf-8")
    (fdir / "module_fields_summary_v20.json").write_text(json.dumps({"totals": {"fields": 4}}), encoding="utf-8")
    log.refresh_inventory(tmp_path / "modules", qdrant=False)
    inv = log.inventory()
    s = inv["screens"][0]
    assert (s["screen"], s["sections"], s["fields"], s["mandatory"], s["qdrant_fields"]) == ("Leave Request", 2, 4, 2, None)
    assert inv["modules"][0]["stats"] == {"fields.fields": 4} and inv["modules"][0]["field_screens"] == 1


def test_cli_command_is_tracked_with_masked_secrets(monkeypatch):
    from pie_agent import cli
    monkeypatch.setattr("sys.argv", ["pie", "kms-stats", "--url", "http://127.0.0.1:9", "--api-key", "s3cret"])
    with pytest.raises(RuntimeError):
        cli.main()
    run = log.get_run(log.list_runs(activity="qdrant_status")["runs"][0]["run_id"])
    assert run["command"] == "kms-stats" and run["status"] == "FAILED" and "s3cret" not in json.dumps(run["params"])


def test_log_api_requires_the_token(monkeypatch):
    from fastapi.testclient import TestClient
    from pie_agent import api
    monkeypatch.setenv("PIE_LOG_TOKEN", "t0k")
    c = TestClient(api.app)
    assert c.get("/logs/api/summary").status_code == 401
    assert c.get("/logs/api/summary", headers={"X-PIE-Log-Token": "t0k"}).status_code == 200
