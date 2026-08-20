"""Functional and non-bypassability tests for the transparent native-wire Modbus
PEP.

These tests drive the transparent Modbus/TCP proxy with an unmodified pymodbus
client and confirm that policy enforcement happens on the actual wire operation
(function code parsed from the frame, not a client-supplied label), and that the
protected device cannot be reached bypassing the PEP.

Because Modbus/TCP carries no identity or context field, the test provisions the
proxy out-of-band before each case: it mints the appropriate ES256 token and
pushes it to ``/admin/token``, sets the operational context via ``/admin/context``,
and clears the decision cache so each case is evaluated independently. An allowed
request returns valid register data; a denied/degraded request is answered with a
native Modbus exception (``resp.isError()`` is True).

The bypass case attempts to reach the protected server (``modbus-plc``) directly,
which must fail because that server is on an internal network reachable only by
the proxy.
"""

from __future__ import annotations

import csv
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests
from pymodbus.client import ModbusTcpClient

from src.auth import mint_token

PEP_HOST = os.getenv("MODBUS_PROXY_PEP_HOST", "modbus-proxy-pep")
PEP_PORT = int(os.getenv("MODBUS_PROXY_PEP_PORT", "5030"))
PEP_ADMIN = os.getenv("MODBUS_PROXY_PEP_ADMIN", "http://modbus-proxy-pep:8083")
PLC_HOST = os.getenv("MODBUS_PLC_HOST", "modbus-plc")
PLC_PORT = int(os.getenv("MODBUS_PLC_PORT", "5020"))
RESULTS_DIR = Path(os.getenv("RESULTS_DIR", "/app/results"))

READ_OFFSET = 0        # logical register 40001
WRITE_OFFSET = 4       # logical register 40005 (inside the maintenance-write range)


def provision(*, role: str, risk: int, attestation: str, ttl: int, maintenance_window: bool) -> None:
    token = mint_token(subject="dev_wire", role=role, risk_score=risk,
                       attestation_state=attestation, ttl_seconds=ttl)
    requests.post(f"{PEP_ADMIN}/admin/token", json={"token": token}, timeout=5).raise_for_status()
    requests.post(f"{PEP_ADMIN}/admin/context",
                  json={"maintenance_window": maintenance_window, "shift": "day"}, timeout=5).raise_for_status()
    requests.post(f"{PEP_ADMIN}/cache/clear", timeout=5)


def wire_op(action: str) -> bool:
    """Perform one native Modbus op through the proxy; return True if the device
    accepted it (no Modbus exception), False if the PEP blocked it."""
    client = ModbusTcpClient(PEP_HOST, port=PEP_PORT, timeout=5)
    if not client.connect():
        return False
    try:
        if action == "read":
            resp = client.read_holding_registers(address=READ_OFFSET, count=1, slave=1)
        else:
            resp = client.write_register(address=WRITE_OFFSET, value=1600, slave=1)
        return not resp.isError()
    except Exception:
        return False
    finally:
        client.close()


def record(rows: List[Dict[str, Any]], name: str, expected_allowed: bool, observed_allowed: bool) -> None:
    result = "PASS" if observed_allowed == expected_allowed else "FAIL"
    row = {
        "test": name,
        "expected": "allow" if expected_allowed else "block",
        "observed": "allow" if observed_allowed else "block",
        "result": result,
    }
    rows.append(row)
    print(row)


def bypass_blocked() -> bool:
    """Attempt to reach the protected device directly (bypassing the PEP).
    Return True if the connection is refused/unreachable (non-bypassable)."""
    client = ModbusTcpClient(PLC_HOST, port=PLC_PORT, timeout=3)
    try:
        if not client.connect():
            return True  # cannot even connect -> bypass prevented
        resp = client.read_holding_registers(address=READ_OFFSET, count=1, slave=1)
        # If we got a valid read, the device was reachable directly -> bypassable.
        return bool(resp.isError())
    except Exception:
        return True
    finally:
        client.close()


def main() -> None:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out = RESULTS_DIR / "modbus_proxy_tests.csv"
    rows: List[Dict[str, Any]] = []

    # Authorized read (valid, low risk).
    provision(role="operator", risk=20, attestation="valid", ttl=300, maintenance_window=False)
    record(rows, "wire_authorized_read", True, wire_op("read"))

    # Write inside a maintenance window (allowed).
    provision(role="maintenance_engineer", risk=20, attestation="valid", ttl=300, maintenance_window=True)
    record(rows, "wire_write_within_maintenance", True, wire_op("write"))

    # Write outside a maintenance window (default-deny).
    provision(role="maintenance_engineer", risk=20, attestation="valid", ttl=300, maintenance_window=False)
    record(rows, "wire_write_outside_maintenance", False, wire_op("write"))

    # High-risk write (degraded to read_only -> blocked on the wire).
    provision(role="maintenance_engineer", risk=60, attestation="valid", ttl=300, maintenance_window=True)
    record(rows, "wire_high_risk_write", False, wire_op("write"))

    # Critical-risk read (quarantine).
    provision(role="operator", risk=90, attestation="valid", ttl=300, maintenance_window=False)
    record(rows, "wire_critical_risk_read", False, wire_op("read"))

    # Failed attestation (denied).
    provision(role="operator", risk=20, attestation="stale", ttl=300, maintenance_window=False)
    record(rows, "wire_failed_attestation", False, wire_op("read"))

    # Expired token (rejected at verification).
    provision(role="operator", risk=20, attestation="valid", ttl=-10, maintenance_window=False)
    record(rows, "wire_expired_token", False, wire_op("read"))

    # Non-bypassability: the protected device is not reachable directly.
    record(rows, "wire_direct_bypass_blocked", False, not bypass_blocked())
    # (expected_allowed=False, observed_allowed = not bypass_blocked(): a blocked
    #  bypass means "not allowed", i.e. PASS.)

    with out.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    passed = sum(1 for r in rows if r["result"] == "PASS")
    print(f"\nWrote {out}")
    print(f"PASS={passed}/{len(rows)}")


if __name__ == "__main__":
    main()
