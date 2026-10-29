from pathlib import Path
import pytest
from pie_agent.erp_discovery import run_erp_discovery

def test_real_erp_discovery(tmp_path):
    fe=Path('data/erp/frontend'); be=Path('data/erp/backend')
    if not (fe.exists() and be.exists()):
        pytest.skip('ERP sources not present under data/erp')
    out=tmp_path/'phase12'
    r=run_erp_discovery(str(fe),str(be),str(out),database_url='')
    assert r['totalScreens'] > 100
    assert r['backend']['endpoints'] > 100
    assert (out/'screen_registry.json').exists()
    assert (out/'backend_endpoint_registry.json').exists()
