"""Functional tests for the MQTT PEP.

Tests:
1. telemetry publish allowed, cache miss first time
2. repeated telemetry publish, cache hit
3. command/setpoint publish allowed during maintenance window
4. command/setpoint publish denied without maintenance window
5. high-risk command publish blocked
6. subscribe authorized for line-local topic
7. critical-risk publish: quarantine/deny
8. failed attestation denied
9. expired token denied
"""

from __future__ import annotations

import csv
import os
import time
from pathlib import Path
from typing import Any, Dict

import requests

from src.auth import mint_token

MQTT_PEP_URL = os.getenv("MQTT_PEP_URL", "http://localhost:8081")
RESULTS_DIR = Path(os.getenv("RESULTS_DIR", "/app/results"))


def call(path: str, token: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    t0 = time.perf_counter()
    r = requests.post(
        f"{MQTT_PEP_URL}{path}",
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
    out = RESULTS_DIR / "mqtt_functional_tests.csv"

    low_risk_sensor = mint_token(subject="mqtt_device_01", role="sensor", risk_score=20, ttl_seconds=300)
    low_risk_maintenance = mint_token(subject="mqtt_device_02", role="maintenance_engineer", risk_score=20, ttl_seconds=300)
    high_risk_maintenance = mint_token(subject="mqtt_device_03", role="maintenance_engineer", risk_score=60, ttl_seconds=300)
    failed_attestation = mint_token(subject="mqtt_device_04", role="sensor", risk_score=20, attestation_state="failed", ttl_seconds=300)
    expired = mint_token(subject="mqtt_device_05", role="sensor", risk_score=20, ttl_seconds=-1)
    critical_risk = mint_token(subject="mqtt_device_06", role="sensor", risk_score=90, ttl_seconds=300)

    tests = [
        ("publish_telemetry_cache_miss", "/mqtt/publish", low_risk_sensor, {"topic": "factory/line1/plc1/status", "payload": '{"temperature":21.0}', "qos": 0}),
        ("publish_telemetry_cache_hit", "/mqtt/publish", low_risk_sensor, {"topic": "factory/line1/plc1/status", "payload": '{"temperature":21.1}', "qos": 0}),
        ("publish_command_allowed_maintenance", "/mqtt/publish", low_risk_maintenance, {"topic": "factory/line1/plc1/setpoint", "payload": '{"speed":1600}', "qos": 0, "maintenance_window": True}),
        ("publish_command_denied_no_maintenance", "/mqtt/publish", low_risk_maintenance, {"topic": "factory/line1/plc1/setpoint", "payload": '{"speed":1700}', "qos": 0, "maintenance_window": False}),
        ("publish_command_high_risk_blocked", "/mqtt/publish", high_risk_maintenance, {"topic": "factory/line1/plc1/setpoint", "payload": '{"speed":1800}', "qos": 0, "maintenance_window": True}),
        ("subscribe_allowed", "/mqtt/authorize-subscribe", low_risk_sensor, {"topic": "factory/line1/plc1/status", "qos": 0}),
        ("publish_critical_risk_quarantine", "/mqtt/publish", critical_risk, {"topic": "factory/line1/plc1/status", "payload": '{"temperature":21.4}', "qos": 0}),
        ("publish_failed_attestation", "/mqtt/publish", failed_attestation, {"topic": "factory/line1/plc1/status", "payload": '{"temperature":21.2}', "qos": 0}),
        ("publish_expired_token", "/mqtt/publish", expired, {"topic": "factory/line1/plc1/status", "payload": '{"temperature":21.3}', "qos": 0}),
    ]

    requests.post(f"{MQTT_PEP_URL}/cache/clear", timeout=3)
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
