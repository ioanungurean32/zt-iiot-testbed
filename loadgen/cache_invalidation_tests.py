"""Cache-invalidation tests for the ZT-IIoT PEP decision cache.

These tests exercise the cache-key binding on the Modbus PEP and verify that a
change in security-relevant state produces a cache MISS (re-evaluation) rather
than reusing an obsolete cached decision. The cache logic is identical across
the Modbus/TCP, MQTT, and OPC UA PEPs, so the Modbus PEP is used as the
canonical demonstration.

Scenarios:

  A. Operational-context change (maintenance_window): binding the environment
     context into the cache key means a maintenance-window change forces
     re-evaluation. Expected: prime=miss, same=hit, context-changed=MISS.

  B. Risk-bucket crossing: a risk-score change that crosses a 25-point bucket
     boundary forces re-evaluation. Expected: prime=miss, cross-bucket=MISS.

  C. Same-bucket risk change (documented residual limitation): a
     risk-score change that stays inside the same 25-point bucket does NOT
     invalidate the cached entry before its TTL expires. Expected:
     prime=miss, same-bucket=HIT. This case is reported as a known limitation,
     not a pass/fail failure.

Each scenario uses a distinct device id so cache keys do not collide across
scenarios. The cache is cleared once at the start.
"""

from __future__ import annotations

import csv
import os
import time
from pathlib import Path
from typing import Any, Dict, Optional

import requests

from src.auth import mint_token

PEP_URL = os.getenv("PEP_URL", "http://localhost:8080")
RESULTS_DIR = Path(os.getenv("RESULTS_DIR", "/app/results"))


def clear_cache() -> None:
    try:
        requests.post(f"{PEP_URL}/cache/clear", timeout=3)
    except requests.RequestException:
        pass


def read_once(device: str, risk_score: int, maintenance_window: bool) -> str:
    """Issue one authorized read and return the observed cache_status
    ('hit', 'miss', or 'none')."""
    token = mint_token(subject=device, role="operator", risk_score=risk_score, ttl_seconds=300)
    payload = {"register_address": 40001, "count": 1, "maintenance_window": maintenance_window}
    r = requests.post(
        f"{PEP_URL}/modbus/read",
        json=payload,
        headers={"Authorization": f"Bearer {token}"},
        timeout=5,
    )
    if r.status_code != 200:
        return "none"
    return r.json().get("cache_status", "none")


def record(rows, scenario, device, risk, mw, expected, observed, limitation=False):
    if limitation:
        result = "LIMITATION" if observed == expected else "UNEXPECTED"
    else:
        result = "PASS" if observed == expected else "FAIL"
    row = {
        "scenario": scenario,
        "device": device,
        "risk_score": risk,
        "maintenance_window": mw,
        "expected_cache_status": expected,
        "observed_cache_status": observed,
        "result": result,
    }
    rows.append(row)
    print(row)


def main() -> None:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out = RESULTS_DIR / "cache_invalidation_tests.csv"
    clear_cache()

    rows = []

    # Scenario A: operational-context (maintenance_window) change.
    record(rows, "A_context_prime", "dev_ctx", 20, False, "miss", read_once("dev_ctx", 20, False))
    record(rows, "A_context_same", "dev_ctx", 20, False, "hit", read_once("dev_ctx", 20, False))
    record(rows, "A_context_changed", "dev_ctx", 20, True, "miss", read_once("dev_ctx", 20, True))

    # Scenario B: risk-bucket crossing (20 -> 40 crosses the 25-point boundary).
    record(rows, "B_riskbucket_prime", "dev_risk", 20, False, "miss", read_once("dev_risk", 20, False))
    record(rows, "B_riskbucket_crossed", "dev_risk", 40, False, "miss", read_once("dev_risk", 40, False))

    # Scenario C: same-bucket risk change (documented residual limitation).
    record(rows, "C_samebucket_prime", "dev_same", 20, False, "miss", read_once("dev_same", 20, False))
    record(rows, "C_samebucket_change", "dev_same", 24, False, "hit", read_once("dev_same", 24, False), limitation=True)

    with out.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    passed = sum(1 for r in rows if r["result"] == "PASS")
    failed = sum(1 for r in rows if r["result"] == "FAIL")
    print(f"\nWrote {out}")
    print(f"PASS={passed} FAIL={failed} (plus documented same-bucket limitation)")


if __name__ == "__main__":
    main()
