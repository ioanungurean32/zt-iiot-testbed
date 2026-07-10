"""CSV metrics logger for ZT-IIoT prototype experiments."""

from __future__ import annotations

import csv
import os
import threading
import time
from pathlib import Path
from typing import Any, Dict

_LOCK = threading.Lock()

FIELDS = [
    "timestamp_unix",
    "request_id",
    "protocol",
    "action",
    # Modbus-specific attributes
    "function_code",
    "register_address",
    # MQTT-specific attributes
    "topic",
    "qos",
    "payload_size_bytes",
    # OPC UA-specific attributes
    "node_id",
    "method_id",
    # Common authorization state
    "decision",
    "reason",
    "cache_status",
    "decision_source",
    "pdp_available",
    "token_status",
    "token_verification_ms",
    "cache_lookup_ms",
    "pdp_decision_ms",
    # Protocol forwarding and total timing
    "modbus_forward_ms",
    "mqtt_forward_ms",
    "opcua_forward_ms",
    "end_to_end_latency_ms",
    "risk_score",
    "attestation_state",
    "policy_version",
]


def metrics_path() -> Path:
    results_dir = Path(os.getenv("RESULTS_DIR", "/app/results"))
    results_dir.mkdir(parents=True, exist_ok=True)
    return results_dir / "pep_metrics.csv"


def log_metric(row: Dict[str, Any]) -> None:
    path = metrics_path()
    record = {field: row.get(field, "") for field in FIELDS}
    record["timestamp_unix"] = record.get("timestamp_unix") or time.time()

    with _LOCK:
        needs_header = not path.exists() or path.stat().st_size == 0
        with path.open("a", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=FIELDS)
            if needs_header:
                writer.writeheader()
            writer.writerow(record)
