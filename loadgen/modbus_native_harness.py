"""Matched-baseline latency harness for the transparent native-wire Modbus PEP.

Whereas ``harness.py`` drives the application-level HTTP gateways, this harness
drives the transparent Modbus/TCP proxy with an **unmodified pymodbus client**
(the client is unaware a PEP is in the path). It measures the same three matched
configurations, now entirely on the native wire:

  native-direct       pymodbus -> modbus-server            (no gateway)
  native-passthrough  pymodbus -> modbus-proxy-gw  -> plc  (parse+forward, no security)
  native-pep          pymodbus -> modbus-proxy-pep -> plc  (token + cache + OPA)

The native-pep minus native-passthrough difference isolates the native-wire
security-processing cost; native-passthrough minus native-direct is the proxy /
extra-hop cost, not attributable to Zero Trust. Output is written as
``raw_modbus_<target>_<profile>_c1.csv`` in the same schema as ``harness.py`` so
that ``analyze.py`` computes the pooled statistics, CI, and overhead uniformly.

For native-pep the harness provisions the proxy out-of-band before timing: it
mints an ES256 token (it holds the private key; the proxy holds only the public
key) and pushes it to the proxy's ``/admin/token`` endpoint, and sets the
operational context via ``/admin/context``. Neither identity nor context is
taken from the client on the wire.

Workload profiles (reads, matching the HTTP steady/cold profiles):
  steady  the same holding register repeated  -> cache hits at the PEP
  cold    a distinct holding register per request -> cache misses (each a PDP call)
"""

from __future__ import annotations

import argparse
import csv
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests
from pymodbus.client import ModbusTcpClient

from src.auth import mint_token

RESULTS_DIR = Path(os.getenv("RESULTS_DIR", "/app/results"))

# host:port and admin endpoint per target.
TARGETS = {
    "native-direct": (os.getenv("MODBUS_HOST", "modbus-server"), int(os.getenv("MODBUS_PORT", "5020")), None),
    "native-passthrough": (os.getenv("MODBUS_PROXY_GW_HOST", "modbus-proxy-gw"),
                            int(os.getenv("MODBUS_PROXY_GW_PORT", "5031")), None),
    "native-pep": (os.getenv("MODBUS_PROXY_PEP_HOST", "modbus-proxy-pep"),
                   int(os.getenv("MODBUS_PROXY_PEP_PORT", "5030")),
                   os.getenv("MODBUS_PROXY_PEP_ADMIN", "http://modbus-proxy-pep:8083")),
}

# 90 distinct holding-register offsets are available on the server (0..99).
COLD_SPAN = 90


def provision_pep(admin_base: str, *, device: str, role: str, risk: int) -> None:
    """Push a freshly minted ES256 token and a benign operational context to the
    transparent PEP before timing, so identity/context are supplied out-of-band
    rather than trusted from the Modbus wire."""
    token = mint_token(subject=device, role=role, risk_score=risk, attestation_state="valid", ttl_seconds=3600)
    requests.post(f"{admin_base}/admin/token", json={"token": token}, timeout=5).raise_for_status()
    requests.post(f"{admin_base}/admin/context", json={"maintenance_window": False, "shift": "day"}, timeout=5).raise_for_status()


def clear_pep_cache(admin_base: str) -> None:
    try:
        requests.post(f"{admin_base}/cache/clear", timeout=5)
    except requests.RequestException:
        pass


def set_pep_cache_enabled(admin_base: str, enabled: bool) -> None:
    """steady exercises the cache-hit path; cold disables the cache so every
    request is a PDP evaluation (the all-miss path), which a single provisioned
    identity over a bounded register space could not otherwise produce."""
    try:
        requests.post(f"{admin_base}/admin/cache", json={"enabled": enabled}, timeout=5).raise_for_status()
    except requests.RequestException:
        pass


def register_offset(profile: str, seq: int) -> int:
    if profile == "cold":
        return seq % COLD_SPAN  # vary the register; the cache is disabled for cold
    return 0  # steady: the same register, so PEP decisions are cache hits


def run_trial(client: ModbusTcpClient, profile: str, n: int) -> List[Tuple[float, int]]:
    out: List[Tuple[float, int]] = []
    for seq in range(n):
        offset = register_offset(profile, seq)
        t0 = time.perf_counter()
        try:
            resp = client.read_holding_registers(address=offset, count=1, slave=1)
            latency_ms = (time.perf_counter() - t0) * 1000.0
            status = 0 if resp.isError() else 200
        except Exception:
            latency_ms = (time.perf_counter() - t0) * 1000.0
            status = 0
        out.append((latency_ms, status))
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", choices=list(TARGETS.keys()), required=True)
    ap.add_argument("--profile", choices=["steady", "cold"], default="steady")
    ap.add_argument("--n", type=int, default=1000)
    ap.add_argument("--warmup", type=int, default=100)
    ap.add_argument("--trials", type=int, default=10)
    ap.add_argument("--device", default="dev_native")
    ap.add_argument("--role", default="operator")
    ap.add_argument("--risk", type=int, default=20)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    host, port, admin = TARGETS[args.target]
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out = Path(args.out) if args.out else RESULTS_DIR / f"raw_modbus_{args.target}_{args.profile}_c1.csv"

    if admin:  # native-pep: provision identity + context out-of-band
        provision_pep(admin, device=args.device, role=args.role, risk=args.risk)
        # steady -> cache-hit path; cold -> disable cache for the all-miss path.
        set_pep_cache_enabled(admin, args.profile == "steady")

    all_rows: List[Dict[str, Any]] = []
    for trial in range(args.trials):
        if admin:
            clear_pep_cache(admin)
        client = ModbusTcpClient(host, port=port)
        if not client.connect():
            raise RuntimeError(f"could not connect to {host}:{port}")
        try:
            if args.warmup > 0:
                run_trial(client, args.profile, args.warmup)  # discarded
            results = run_trial(client, args.profile, args.n)
        finally:
            client.close()
        for seq, (latency_ms, status) in enumerate(results):
            all_rows.append({
                "trial": trial, "seq": seq, "protocol": "modbus", "target": args.target,
                "profile": args.profile, "concurrency": 1,
                "latency_ms": round(latency_ms, 6), "status": status, "cache_status": "native",
            })
        ok = sum(1 for _, s in results if s == 200)
        print(f"trial {trial}: n={args.n} ok={ok}/{args.n}")

    with out.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(all_rows[0].keys()))
        writer.writeheader()
        writer.writerows(all_rows)
    print(f"Wrote {out} ({len(all_rows)} rows)")


if __name__ == "__main__":
    main()
