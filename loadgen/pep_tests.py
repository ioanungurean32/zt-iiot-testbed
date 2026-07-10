"""Functional tests for the Modbus PEP.

Tests:
1. allowed read: should be allow, cache miss first time
2. repeated read: should be cache hit
3. allowed write: maintenance engineer, low risk, maintenance window
4. write without maintenance window: deny
5. high-risk write: read_only/deny
6. critical-risk read: quarantine/deny
7. failed attestation: deny
8. expired token: deny
"""

from __future__ import annotations

import csv
import os
import time
from pathlib import Path
from typing import Any, Dict

import requests

from src.auth import mint_token

PEP_URL = os.getenv("PEP_URL", "http://localhost:8080")
RESULTS_DIR = Path(os.getenv("RESULTS_DIR", "/app/results"))


def call(path: str, token: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    t0 = time.perf_counter()
    r = requests.post(
        f"{PEP_URL}{path}",
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


def main() -> None:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out = RESULTS_DIR / "pep_functional_tests.csv"

    low_risk_maintenance = mint_token(subject="device_01", role="maintenance_engineer", risk_score=20, ttl_seconds=300)
    high_risk = mint_token(subject="device_02", role="maintenance_engineer", risk_score=60, ttl_seconds=300)
    failed_attestation = mint_token(subject="device_03", role="maintenance_engineer", risk_score=20, attestation_state="failed", ttl_seconds=300)
    expired = mint_token(subject="device_04", role="maintenance_engineer", risk_score=20, ttl_seconds=-1)
    critical_risk = mint_token(subject="device_05", role="maintenance_engineer", risk_score=90, ttl_seconds=300)

    tests = [
        ("read_allowed_cache_miss", "/modbus/read", low_risk_maintenance, {"register_address": 40001, "count": 1}),
        ("read_allowed_cache_hit", "/modbus/read", low_risk_maintenance, {"register_address": 40001, "count": 1}),
        ("write_allowed", "/modbus/write", low_risk_maintenance, {"register_address": 40005, "value": 1600, "maintenance_window": True}),
        ("write_denied_no_maintenance_window", "/modbus/write", low_risk_maintenance, {"register_address": 40005, "value": 1700, "maintenance_window": False}),
        ("write_high_risk_read_only", "/modbus/write", high_risk, {"register_address": 40005, "value": 1800, "maintenance_window": True}),
        ("read_critical_risk_quarantine", "/modbus/read", critical_risk, {"register_address": 40001, "count": 1}),
        ("read_failed_attestation", "/modbus/read", failed_attestation, {"register_address": 40001, "count": 1}),
        ("read_expired_token", "/modbus/read", expired, {"register_address": 40001, "count": 1}),
    ]

    rows = []
    for name, path, token, payload in tests:
        result = call(path, token, payload)
        body = result["body"]
        if result["status_code"] == 200:
            decision = body.get("decision", {}).get("mode")
            reason = body.get("decision", {}).get("reason")
            cache_status = body.get("cache_status")
        else:
            decision = body.get("detail", {}).get("decision", {}).get("mode")
            reason = body.get("detail", {}).get("decision", {}).get("reason")
            cache_status = "none"
        row = {
            "test": name,
            "status_code": result["status_code"],
            "decision": decision,
            "reason": reason,
            "cache_status": cache_status,
            "latency_ms": round(result["latency_ms"], 6),
        }
        rows.append(row)
        print(row)

    with out.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
