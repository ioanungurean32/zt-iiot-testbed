"""Prime PEP read/telemetry caches before PDP outage tests.

Run this script while OPA/PDP is online. It performs exactly the cached-safe
operation that the corresponding outage test will later retry while OPA/PDP is
stopped. Matching subject, role, resource, action, and policy version are
required because these fields are part of the PEP cache key.
"""

from __future__ import annotations

import argparse
import csv
import os
import time
from pathlib import Path
from typing import Any, Dict, Tuple

import requests

from src.auth import mint_token

RESULTS_DIR = Path(os.getenv("RESULTS_DIR", "/app/results"))
PEP_URL = os.getenv("PEP_URL", "http://localhost:8080")
MQTT_PEP_URL = os.getenv("MQTT_PEP_URL", "http://localhost:8081")
OPCUA_PEP_URL = os.getenv("OPCUA_PEP_URL", "http://localhost:8082")


def scenario(protocol: str) -> Tuple[str, str, str, Dict[str, Any]]:
    if protocol == "modbus":
        token = mint_token(subject="device_01", role="maintenance_engineer", risk_score=20, ttl_seconds=600)
        return PEP_URL, "/modbus/read", token, {"register_address": 40001, "count": 1}
    if protocol == "mqtt":
        token = mint_token(subject="mqtt_device_01", role="sensor", risk_score=20, ttl_seconds=600)
        return MQTT_PEP_URL, "/mqtt/publish", token, {"topic": "factory/line1/plc1/status", "payload": '{"temperature":22.0}', "qos": 0}
    if protocol == "opcua":
        token = mint_token(subject="opcua_device_01", role="operator", risk_score=20, ttl_seconds=600)
        return OPCUA_PEP_URL, "/opcua/read", token, {"node_id": "ns=2;s=Line1.Temperature"}
    raise ValueError(f"Unsupported protocol: {protocol}")


def call(base_url: str, path: str, token: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    t0 = time.perf_counter()
    r = requests.post(
        f"{base_url}{path}",
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


def extract(protocol: str, result: Dict[str, Any]) -> Dict[str, Any]:
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
        decision_source = "none"
        pdp_available = False
    return {
        "protocol": protocol,
        "phase": "prime_cache_while_pdp_online",
        "status_code": result["status_code"],
        "decision": decision,
        "reason": reason,
        "cache_status": cache_status,
        "decision_source": decision_source,
        "pdp_available": pdp_available,
        "latency_ms": round(result["latency_ms"], 6),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--protocol", choices=["modbus", "mqtt", "opcua"], required=True)
    args = parser.parse_args()

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    base_url, path, token, payload = scenario(args.protocol)
    row = extract(args.protocol, call(base_url, path, token, payload))
    print(row)

    out = RESULTS_DIR / "outage_cache_prime_tests.csv"
    needs_header = not out.exists() or out.stat().st_size == 0
    with out.open("a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(row.keys()))
        if needs_header:
            writer.writeheader()
        writer.writerow(row)
    print(f"Appended {out}")


if __name__ == "__main__":
    main()
