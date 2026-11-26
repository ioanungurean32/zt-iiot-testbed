"""Functional and non-bypassability tests for the transparent native-wire Modbus
PEP.

These tests drive the transparent Modbus/TCP proxy with an unmodified pymodbus
client and confirm that policy enforcement happens on the actual wire operation
(function code parsed from the frame, not a client-supplied label), and that the
protected device cannot be reached bypassing the PEP.

Because Modbus/TCP carries no identity or context field, the test provisions the
proxy out-of-band before each case: it mints the appropriate ES256 token and
binds it, via ``/admin/token``, to the address of this container (from which the
Modbus client connects), sets the operational context via ``/admin/context``,
and clears the decision cache so each case is evaluated independently. Every
admin request carries the shared admin secret. An allowed request returns valid
register data; a denied/degraded request is answered with a native Modbus
exception (``resp.isError()`` is True).

The identity-binding cases check that the proxy evaluates each connection with
the token bound to its own source address: a client with no binding is denied
even while another address holds a valid token, a client is evaluated with its
own principal and not with one bound to another address, and removing a binding
takes effect on a connection that is already open. The admin cases check that
state-changing admin requests without the secret, or with a wrong one, are
rejected and have no effect.

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

from src.admin_auth import admin_headers
from src.auth import mint_token

PEP_HOST = os.getenv("MODBUS_PROXY_PEP_HOST", "modbus-proxy-pep")
PEP_PORT = int(os.getenv("MODBUS_PROXY_PEP_PORT", "5030"))
PEP_ADMIN = os.getenv("MODBUS_PROXY_PEP_ADMIN", "http://modbus-proxy-pep:8083")
PLC_HOST = os.getenv("MODBUS_PLC_HOST", "modbus-plc")
PLC_PORT = int(os.getenv("MODBUS_PLC_PORT", "5020"))
RESULTS_DIR = Path(os.getenv("RESULTS_DIR", "/app/results"))

READ_OFFSET = 0        # logical register 40001
WRITE_OFFSET = 4       # logical register 40005 (inside the maintenance-write range)
# An address that no container uses (TEST-NET-2, RFC 5737): a stand-in for a
# second client that holds its own binding.
OTHER_CLIENT = "198.51.100.7"


def bind(token: Optional[str], client: Optional[str] = None) -> None:
    """Bind a token to ``client`` (default: this container's address); a null
    token removes the binding."""
    body: Dict[str, Any] = {"token": token}
    if client:
        body["client"] = client
    requests.post(f"{PEP_ADMIN}/admin/token", json=body, headers=admin_headers(), timeout=5).raise_for_status()


def set_context(maintenance_window: bool) -> None:
    requests.post(f"{PEP_ADMIN}/admin/context", json={"maintenance_window": maintenance_window, "shift": "day"},
                  headers=admin_headers(), timeout=5).raise_for_status()
    requests.post(f"{PEP_ADMIN}/cache/clear", headers=admin_headers(), timeout=5).raise_for_status()


def token_for(*, role: str, risk: int = 20, attestation: str = "valid", ttl: int = 300) -> str:
    return mint_token(subject="dev_wire", role=role, risk_score=risk,
                      attestation_state=attestation, ttl_seconds=ttl)


def provision(*, role: str, risk: int, attestation: str, ttl: int, maintenance_window: bool) -> None:
    bind(token_for(role=role, risk=risk, attestation=attestation, ttl=ttl))
    set_context(maintenance_window)


def op(client: ModbusTcpClient, action: str) -> bool:
    try:
        if action == "read":
            resp = client.read_holding_registers(address=READ_OFFSET, count=1, slave=1)
        else:
            resp = client.write_register(address=WRITE_OFFSET, value=1600, slave=1)
        return not resp.isError()
    except Exception:
        return False


def wire_op(action: str) -> bool:
    """Perform one native Modbus op through the proxy; return True if the device
    accepted it (no Modbus exception), False if the PEP blocked it."""
    client = ModbusTcpClient(PEP_HOST, port=PEP_PORT, timeout=5)
    if not client.connect():
        return False
    try:
        return op(client, action)
    finally:
        client.close()


def revoked_on_open_connection() -> bool:
    """Read once on an open connection, remove this client's binding, and read
    again on the same connection. Return True (i.e. "allowed", a FAIL) unless the
    first read succeeded and the second was blocked."""
    client = ModbusTcpClient(PEP_HOST, port=PEP_PORT, timeout=5)
    if not client.connect():
        return True
    try:
        first = op(client, "read")
        bind(None)
        second = op(client, "read")
        return (not first) or second
    finally:
        client.close()


def admin_accepted(path: str, body: Dict[str, Any], headers: Optional[Dict[str, str]]) -> bool:
    r = requests.post(f"{PEP_ADMIN}{path}", json=body, headers=headers or {}, timeout=5)
    return r.status_code == 200


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

    # Identity binding: a client with no binding is denied, even while another
    # address holds a valid token (no process-wide fallback identity).
    bind(None)
    bind(token_for(role="operator"), client=OTHER_CLIENT)
    set_context(False)
    record(rows, "wire_unbound_client_denied", False, wire_op("read"))

    # Identity binding: this client is evaluated with its own principal (operator,
    # no write right) and not with the maintenance_engineer bound to another address.
    bind(token_for(role="maintenance_engineer"), client=OTHER_CLIENT)
    bind(token_for(role="operator"))
    set_context(True)
    record(rows, "wire_binding_other_principal_not_used", False, wire_op("write"))

    # Identity binding: rebinding this client to maintenance_engineer grants the write.
    bind(token_for(role="maintenance_engineer"))
    set_context(True)
    record(rows, "wire_binding_own_principal_used", True, wire_op("write"))
    bind(None, client=OTHER_CLIENT)

    # Identity binding: removing the binding takes effect on an open connection.
    provision(role="operator", risk=20, attestation="valid", ttl=300, maintenance_window=False)
    record(rows, "wire_binding_revoked_on_open_connection", False, revoked_on_open_connection())

    # Admin interface: state-changing requests need the shared secret.
    valid = token_for(role="maintenance_engineer")
    wrong = {"Authorization": "Bearer " + "0" * 64}
    record(rows, "admin_token_without_secret_rejected", False,
           admin_accepted("/admin/token", {"token": valid}, None))
    record(rows, "admin_token_wrong_secret_rejected", False,
           admin_accepted("/admin/token", {"token": valid}, wrong))
    record(rows, "admin_context_without_secret_rejected", False,
           admin_accepted("/admin/context", {"maintenance_window": True}, None))
    record(rows, "admin_cache_without_secret_rejected", False,
           admin_accepted("/admin/cache", {"enabled": False}, None))
    record(rows, "admin_cache_clear_without_secret_rejected", False,
           admin_accepted("/cache/clear", {}, None))
    # The rejected /admin/token requests must not have bound a token to this client.
    set_context(True)
    record(rows, "admin_rejected_binding_had_no_effect", False, wire_op("write"))
    bind(None)

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
