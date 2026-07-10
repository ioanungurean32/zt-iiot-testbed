"""OPC UA PDP outage behavior test.

Precondition: run ``prime_outage_cache.py --protocol opcua`` while OPA/PDP is
online, then stop OPA and run this script with ``docker compose run --rm --no-deps``.
Expected behavior:
- cached read remains allowed from the local PEP cache
- write without a current PDP decision is denied/fail-closed
"""

from __future__ import annotations

import csv
import os
import time
from pathlib import Path
from typing import Any, Dict

import requests

from src.auth import mint_token

OPCUA_PEP_URL = os.getenv("OPCUA_PEP_URL", "http://localhost:8082")
RESULTS_DIR = Path(os.getenv("RESULTS_DIR", "/app/results"))


def call(path: str, token: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    t0 = time.perf_counter()
    try:
        r = requests.post(
            f"{OPCUA_PEP_URL}{path}",
            json=payload,
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
        )
        latency_ms = (time.perf_counter() - t0) * 1000.0
        try:
            body = r.json()
        except Exception:
            body = {"raw": r.text}
        return {"status_code": r.status_code, "latency_ms": latency_ms, "body": body}
    except requests.RequestException as exc:
        latency_ms = (time.perf_counter() - t0) * 1000.0
        return {"status_code": 598, "latency_ms": latency_ms, "body": {"detail": {"decision": {"mode": "timeout", "reason": str(exc)}}}}


def row(name: str, result: Dict[str, Any]) -> Dict[str, Any]:
    body = result["body"]
    if result["status_code"] == 200:
        decision = body.get("decision", {}).get("mode")
        reason = body.get("decision", {}).get("reason")
        cache_status = body.get("cache_status")
        decision_source = body.get("decision_source")
        pdp_available = body.get("pdp_available")
    else:
        decision = body.get("detail", {}).get("decision", {}).get("mode")
        reason = body.get("detail", {}).get("decision", {}).get("reason")
        cache_status = "none"
        decision_source = "pdp" if reason == "pdp_unavailable_fail_safe" else "none"
        pdp_available = False
    return {
        "test": name,
        "status_code": result["status_code"],
        "decision": decision,
        "reason": reason,
        "cache_status": cache_status,
        "decision_source": decision_source,
        "pdp_available": pdp_available,
        "latency_ms": round(result["latency_ms"], 6),
    }


def main() -> None:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out = RESULTS_DIR / "opcua_pdp_outage_tests.csv"
    cached_read_token = mint_token(subject="opcua_device_01", role="operator", risk_score=20, ttl_seconds=600)
    write_token = mint_token(subject="opcua_device_02", role="maintenance_engineer", risk_score=20, ttl_seconds=600)

    tests = [
        ("pdp_down_cached_read_allowed", "/opcua/read", cached_read_token, {"node_id": "ns=2;s=Line1.Temperature"}),
        ("pdp_down_write_fail_closed", "/opcua/write", write_token, {"node_id": "ns=2;s=Line1.SpeedSetpoint", "value": 1900, "value_type": "int", "maintenance_window": True}),
    ]

    rows = []
    for name, path, token, payload in tests:
        r = row(name, call(path, token, payload))
        rows.append(r)
        print(r)

    with out.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
