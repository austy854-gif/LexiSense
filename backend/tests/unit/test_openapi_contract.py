"""OpenAPI route contract for routers mounted when feature flags are off."""

import json
from pathlib import Path

from openapi_contract import collect_contract

SNAPSHOT = Path(__file__).resolve().parents[2] / "openapi_route_contract.json"


def test_openapi_route_contract_matches_the_source():
    expected = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    actual = collect_contract()
    assert actual["mounted_routers"] == expected["mounted_routers"]
    assert actual["operations"] == expected["operations"]


def test_contract_covers_launch_routes_and_skips_flagged_ones():
    operations = set(collect_contract()["operations"])
    assert "GET /api/v1/health" in operations
    assert "GET /api/v1/ready" in operations
    assert "POST /api/v1/billing/checkout" in operations
    assert "POST /api/v1/billing/webhook" in operations
    assert "POST /api/v1/contracts" in operations
    assert not any("/agentic/" in item for item in operations)
    assert not any(item.endswith("/approvals/email-action") for item in operations)
    assert not any("/reports/" in item for item in operations)
