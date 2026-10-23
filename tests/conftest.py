import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture(autouse=True)
def _no_osrm_by_default(monkeypatch):
    """Tests never touch the network; OSRM tests opt back in with their own URL."""
    from saafroute import routing

    monkeypatch.setenv("SAAFROUTE_OSRM_URL", "off")
    monkeypatch.setattr(routing, "_cache", {})
    monkeypatch.setattr(routing, "_osrm_failed_at", 0.0)
