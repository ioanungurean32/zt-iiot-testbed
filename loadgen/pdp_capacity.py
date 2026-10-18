"""PDP (OPA) capacity sweep, measured without any PEP in the path.

Issues policy-decision queries directly to the OPA decision endpoint from an
increasing number of concurrent clients and reports throughput and latency per
level. Every query carries a distinct device identity (OPA itself does not
cache decisions), so each one is a full Rego evaluation, i.e. the work a PEP
offloads to the PDP on every cache miss. Comparing this capacity with the PEP
saturation sweep (saturation_sweep.py, steady and cold profiles) shows which
component limits throughput on the evaluated single host.

Usage:
  python loadgen/pdp_capacity.py --levels 1,2,5,10,20,50 --n 2000
"""

from __future__ import annotations

import argparse
import csv
import os
import statistics
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Dict, List

import requests

OPA_URL = os.getenv("OPA_DECISION_URL", "http://opa:8181/v1/data/zt_iiot/authz/decision")
RESULTS_DIR = Path(os.getenv("RESULTS_DIR", "/app/results"))

_thread_local = threading.local()


def _session() -> requests.Session:
    s = getattr(_thread_local, "session", None)
    if s is None:
        s = requests.Session()
        _thread_local.session = s
    return s


def percentile(values, p):
    if not values:
        return 0.0
    v = sorted(values)
    k = (len(v) - 1) * p
    f = int(k)
    c = min(f + 1, len(v) - 1)
    if f == c:
        return v[int(k)]
    return v[f] * (c - k) + v[c] * (k - f)


def opa_input(device: str, write: bool) -> Dict:
    # Same attribute layout as build_opa_input() in src/modbus_pep.py.
    return {
        "protocol": "modbus",
        "action": "write" if write else "read",
        "subject": {"device_id": device, "role": "maintenance_engineer" if write else "operator",
                    "zone": "cell_A", "attestation_state": "valid", "risk_score": 20},
        "resource": {"protocol": "modbus", "function_code": 6 if write else 3,
                     "register_address": 40005 if write else 40001, "unit_id": 1},
        "environment": {"maintenance_window": write, "shift": "day"},
        "policy_version": "v0.1-modbus",
    }


def query(item) -> Dict:
    idx, payload = item
    t0 = time.perf_counter()
    try:
        r = _session().post(OPA_URL, json={"input": payload}, timeout=10)
        ok = r.status_code == 200 and "result" in r.json()
    except requests.RequestException:
        ok = False
    return {"latency_ms": (time.perf_counter() - t0) * 1000.0, "ok": ok}


def run_level(concurrency: int, n: int, warmup: int, write_share: float) -> Dict:
    def plan(count: int, tag: str):
        every = int(round(1 / write_share)) if write_share > 0 else 0
        return [(i, opa_input(f"dev_pdp_{tag}_{i}", bool(every) and i % every == 0)) for i in range(count)]

    with ThreadPoolExecutor(max_workers=concurrency) as ex:
        if warmup > 0:
            list(ex.map(query, plan(warmup, f"w{concurrency}")))
        t0 = time.perf_counter()
        results = list(ex.map(query, plan(n, f"m{concurrency}")))
        span = time.perf_counter() - t0
    lat = [r["latency_ms"] for r in results]
    ok = sum(1 for r in results if r["ok"])
    return {
        "concurrency": concurrency,
        "requests": n,
        "write_share": write_share,
        "throughput_rps": round(n / span, 2) if span > 0 else 0.0,
        "success_rate": round(ok / n, 4),
        "mean_ms": round(statistics.mean(lat), 4),
        "p95_ms": round(percentile(lat, 0.95), 4),
        "p99_ms": round(percentile(lat, 0.99), 4),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--levels", default="1,2,5,10,20,50")
    ap.add_argument("--n", type=int, default=2000)
    ap.add_argument("--warmup", type=int, default=100)
    ap.add_argument("--write-share", type=float, default=0.0,
                    help="fraction of write (maintenance) queries, e.g. 0.5")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    suffix = f"_w{int(args.write_share * 100)}" if args.write_share > 0 else ""
    out = Path(args.out) if args.out else RESULTS_DIR / f"pdp_capacity{suffix}.csv"

    rows: List[Dict] = []
    for c in [int(x) for x in args.levels.split(",")]:
        row = run_level(c, args.n, args.warmup, args.write_share)
        rows.append(row)
        print(row)

    with out.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
