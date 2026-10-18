"""Concurrency / saturation sweep.

For an increasing number of concurrent clients, this measures:
  * sustained throughput (requests/second),
  * p95 / p99 latency under load,
  * PEP CPU time consumed and peak resident memory (via the PEP /resource
    endpoint), and
  * the PDP decision time under load (mean pdp_decision proxy via miss-path).

After the highest load level, a light recovery load is issued to check that
latency returns toward its low-load value (overload recovery).

PDP (OPA) container CPU/memory can be observed in parallel with
`docker stats opa` on the host; the PEP-side pdp latency reported here already
reflects OPA saturation from the data plane's perspective.

Usage:
  python loadgen/saturation_sweep.py --protocol modbus --profile steady \
      --levels 1,2,5,10,20,50 --n 500 --warmup 50

Besides the cache-dominated steady profile, the sweep accepts the miss-heavy
cold profile (every request a PDP evaluation) and the write-heavy mixed profile
(every other request a non-cacheable write), so PEP saturation can be compared
with the PDP-bound case; see also loadgen/pdp_capacity.py for the PDP alone.
"""

from __future__ import annotations

import argparse
import csv
import os
import statistics
import time
from pathlib import Path
from typing import Dict, List

import requests

from loadgen.harness import build_caller, generate_plan, run_trial, PEP_BASE

RESULTS_DIR = Path(os.getenv("RESULTS_DIR", "/app/results"))


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


def get_resource(base: str) -> Dict:
    try:
        return requests.get(f"{base}/resource", timeout=5).json()
    except requests.RequestException:
        return {}


def clear_cache(base: str) -> None:
    try:
        requests.post(f"{base}/cache/clear", timeout=5)
    except requests.RequestException:
        pass


def run_level(protocol: str, profile: str, n: int, warmup: int, concurrency: int) -> Dict:
    base = PEP_BASE[protocol]
    caller = build_caller(protocol, "pep")
    clear_cache(base)

    if warmup > 0:
        run_trial(caller, generate_plan(protocol, profile, warmup, seed=9000 + concurrency), concurrency)

    r0 = get_resource(base)
    t0 = time.perf_counter()
    results = run_trial(caller, generate_plan(protocol, profile, n, seed=concurrency), concurrency)
    span = time.perf_counter() - t0
    r1 = get_resource(base)

    lat = [r["latency_ms"] for r in results]
    ok = sum(1 for r in results if r["status"] == 200)
    hits = sum(1 for r in results if r["cache_status"] == "hit")
    cpu_delta = None
    if r0 and r1 and r0.get("cpu_user_s") is not None:
        cpu_delta = round((r1["cpu_user_s"] + r1["cpu_sys_s"]) - (r0["cpu_user_s"] + r0["cpu_sys_s"]), 4)
    return {
        "concurrency": concurrency,
        "requests": n,
        "throughput_rps": round(n / span, 2) if span > 0 else 0.0,
        "success_rate": round(ok / n, 4),
        "cache_hit_ratio": round(hits / n, 4),
        "mean_ms": round(statistics.mean(lat), 4),
        "p95_ms": round(percentile(lat, 0.95), 4),
        "p99_ms": round(percentile(lat, 0.99), 4),
        "pep_cpu_time_s": cpu_delta,
        "pep_peak_rss_kb": r1.get("maxrss_kb") if r1 else None,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--protocol", choices=["modbus", "mqtt", "opcua"], default="modbus")
    ap.add_argument("--profile", default="steady")
    ap.add_argument("--levels", default="1,2,5,10,20,50")
    ap.add_argument("--n", type=int, default=500)
    ap.add_argument("--warmup", type=int, default=50)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    levels = [int(x) for x in args.levels.split(",")]
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out = Path(args.out) if args.out else RESULTS_DIR / f"saturation_{args.protocol}_{args.profile.replace(':', '')}.csv"

    rows: List[Dict] = []
    for c in levels:
        row = run_level(args.protocol, args.profile, args.n, args.warmup, c)
        rows.append(row)
        print(row)

    # Recovery: a light load after the peak to check latency returns toward baseline.
    recovery = run_level(args.protocol, args.profile, args.n, args.warmup, 1)
    recovery["concurrency"] = "recovery(c=1)"
    rows.append(recovery)
    print("recovery:", recovery)

    with out.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
