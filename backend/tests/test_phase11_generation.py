from pathlib import Path
import json
from pie_agent.ingestion import ingest
from pie_agent.classification import classify_all
from pie_agent.discovery import discover_screens, map_sources_to_screen
from pie_agent.generation import run_generation

def test_discovery_returns_real_leave_screen_only():
    ing=ingest(Path("data/PIE_Data.zip")); classify_all(ing.records,ing.text)
    report=discover_screens(ing.records,ing.text)
    assert report.totalScreens==1
    s=report.screens[0]
    assert "LEAVE" in s.screenId
    assert s.screenName=="Leave Application"
    assert any("screenshot" in x.lower() for x in s.sourceFiles)

def test_generation_creates_kms_ready_package(tmp_path):
    report,packages=run_generation("data/PIE_Data.zip",str(tmp_path))
    assert report.totalScreens==1
    assert len(packages)==1
    pkg=packages[0]
    assert pkg.screen.feature=="Leave Application"
    assert pkg.records
    assert all(r.evidence for r in pkg.records)
    assert (tmp_path/"screen_registry.json").exists()
    assert (tmp_path/"knowledge_manifest.json").exists()
    data=json.loads((tmp_path/"knowledge_manifest.json").read_text())
    assert data["totalScreens"]==1

def test_source_mapping_keeps_core_leave_sources():
    ing=ingest(Path("data/PIE_Data.zip")); classify_all(ing.records,ing.text)
    screen=discover_screens(ing.records,ing.text).screens[0]
    mappings=map_sources_to_screen(screen,ing.records,ing.text)
    selected={m["source"] for m in mappings if m["selected"]}
    assert any("Leave Application front end" in x for x in selected)
    assert any("Backend code Output" in x for x in selected)
    assert any("DB Schema output" in x for x in selected)
