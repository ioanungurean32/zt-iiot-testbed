"""Functional tests for the OPC UA PEP.

Tests:
1. read allowed, cache miss first time
2. repeated read, cache hit
3. write allowed during maintenance window
4. write denied without maintenance window
5. method call authorized during maintenance window
6. high-risk write blocked
7. high-risk method call blocked
8. critical-risk read: quarantine/deny
9. failed attestation denied
10. expired token denied
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
    r = requests.post(
        f"{OPCUA_PEP_URL}{path}",
        json=payload,
        headers={"Authorization": f"Bearer {token}"},
        timeout=5,
    )
    latency_ms = (time.perf_counter() - t0) * 1000.0
    try:
        body = r.json()
    except Exception:
        body = {"raw": r.text}
    return {"status_code": r.status_code, "latency_ms": latency_ms, "body": body}


def extract_row(name: str, result: Dict[str, Any]) -> Dict[str, Any]:
    body = result["body"]
    if result["status_code"] == 200:
        decision = body.get("decision", {}).get("mode")
        reason = body.get("decision", {}).get("reason")
        cache_status = body.get("cache_status")
    else:
        decision = body.get("detail", {}).get("decision", {}).get("mode")
        reason = body.get("detail", {}).get("decision", {}).get("reason")
        cache_status = "none"
    return {
        "test": name,
        "status_code": result["status_code"],
        "decision": decision,
        "reason": reason,
        "cache_status": cache_status,
        "latency_ms": round(result["latency_ms"], 6),
    }


def main() -> None:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out = RESULTS_DIR / "opcua_functional_tests.csv"

    low_risk_operator = mint_token(subject="opcua_device_01", role="operator", risk_score=20, ttl_seconds=300)
    low_risk_maintenance = mint_token(subject="opcua_device_02", role="maintenance_engineer", risk_score=20, ttl_seconds=300)
    high_risk_maintenance = mint_token(subject="opcua_device_03", role="maintenance_engineer", risk_score=60, ttl_seconds=300)
    failed_attestation = mint_token(subject="opcua_device_04", role="operator", risk_score=20, attestation_state="failed", ttl_seconds=300)
    expired = mint_token(subject="opcua_device_05", role="operator", risk_score=20, ttl_seconds=-1)
    critical_risk = mint_token(subject="opcua_device_06", role="operator", risk_score=90, ttl_seconds=300)

    tests = [
        ("read_cache_miss", "/opcua/read", low_risk_operator, {"node_id": "ns=2;s=Line1.Temperature"}),
        ("read_cache_hit", "/opcua/read", low_risk_operator, {"node_id": "ns=2;s=Line1.Temperature"}),
        ("write_allowed_maintenance", "/opcua/write", low_risk_maintenance, {"node_id": "ns=2;s=Line1.SpeedSetpoint", "value": 1600, "value_type": "int", "maintenance_window": True}),
        ("write_denied_no_maintenance", "/opcua/write", low_risk_maintenance, {"node_id": "ns=2;s=Line1.SpeedSetpoint", "value": 1700, "value_type": "int", "maintenance_window": False}),
        ("method_call_allowed_maintenance", "/opcua/call-method", low_risk_maintenance, {"object_id": "ns=2;s=Line1", "method_id": "ns=2;s=Line1.StartMotor", "maintenance_window": True}),
        ("write_high_risk_blocked", "/opcua/write", high_risk_maintenance, {"node_id": "ns=2;s=Line1.SpeedSetpoint", "value": 1800, "value_type": "int", "maintenance_window": True}),
        ("method_call_high_risk_blocked", "/opcua/call-method", high_risk_maintenance, {"object_id": "ns=2;s=Line1", "method_id": "ns=2;s=Line1.StartMotor", "maintenance_window": True}),
        ("read_critical_risk_quarantine", "/opcua/read", critical_risk, {"node_id": "ns=2;s=Line1.Temperature"}),
        ("read_failed_attestation", "/opcua/read", failed_attestation, {"node_id": "ns=2;s=Line1.Temperature"}),
        ("read_expired_token", "/opcua/read", expired, {"node_id": "ns=2;s=Line1.Temperature"}),
    ]

    requests.post(f"{OPCUA_PEP_URL}/cache/clear", timeout=3)
    rows = []
    for name, path, token, payload in tests:
        row = extract_row(name, call(path, token, payload))
        rows.append(row)
        print(row)

    with out.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
