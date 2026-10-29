"""Activity-log API: the Activity Log dashboard (frontend/logs.html) and runs sent by remote CLIs.

Set PIE_LOG_TOKEN on the server; every request must then send it in the X-PIE-Log-Token header.
With PIE_LOG_REQUIRE_TOKEN=1 the API refuses to run without a token configured.
"""
from __future__ import annotations

import hmac
import os
import re
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query

from . import activity_log as log


def _auth(x_pie_log_token: str | None = Header(default=None)) -> None:
    token = os.getenv("PIE_LOG_TOKEN", "")
    if not token:
        if os.getenv("PIE_LOG_REQUIRE_TOKEN", "") == "1":
            raise HTTPException(503, "PIE_LOG_TOKEN is not configured on the server")
        return
    if not hmac.compare_digest((x_pie_log_token or "").encode(), token.encode()):
        raise HTTPException(401, "missing or wrong X-PIE-Log-Token")


router = APIRouter(prefix="/logs/api", tags=["activity log"], dependencies=[Depends(_auth)])


@router.get("/summary")
def logs_summary(hours: float = Query(24, gt=0, le=24 * 366)):
    return log.summary(hours)


@router.get("/runs")
def logs_runs(activity: str | None = None, status: str | None = None, module: str | None = None,
              source: str | None = None, q: str | None = Query(None, max_length=200),
              hours: float | None = Query(None, gt=0), top: bool = False,
              limit: int = Query(100, ge=1, le=1000), offset: int = Query(0, ge=0)):
    return log.list_runs(activity, status, module, source, q, hours, limit, offset, top)


@router.get("/runs/{run_id}")
def logs_run(run_id: str):
    if not re.fullmatch(r"[0-9a-f]{6,32}", run_id):
        raise HTTPException(400, "run id must be 6-32 hex characters")
    run = log.get_run(run_id)
    if not run:
        raise HTTPException(404, "run not found")
    return run


@router.get("/qdrant")
def logs_qdrant(limit: int = Query(48, ge=1, le=1000)):
    return log.qdrant_history(limit)


@router.post("/qdrant/refresh")
def logs_qdrant_refresh():
    return log.qdrant_snapshot(source="api")


@router.get("/inventory")
def logs_inventory():
    return log.inventory()


@router.post("/inventory/refresh")
def logs_inventory_refresh():
    log.refresh_inventory(source="api")
    return log.inventory()


@router.get("/api-requests")
def logs_api_requests(hours: float = Query(24, gt=0, le=24 * 366)):
    return log.api_stats(hours)


@router.post("/ingest")
def logs_ingest(body: dict[str, Any]):
    try:
        return {"stored": log.ingest_remote(body)}
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(400, f"bad run record: {exc}")
