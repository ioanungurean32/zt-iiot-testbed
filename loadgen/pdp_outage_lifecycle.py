"""PDP-unavailability lifecycle test.

Runs as a sequence of phases orchestrated by the host (which stops/starts the
OPA container and, for the partition variant, disconnects/reconnects the OPA
network). Each phase probes the Modbus PEP and records the outcome, so the full
outage lifecycle is characterized rather than only the single cached-read /
uncached-write pair of the original experiment.

Phases:
  prime            populate the cache with a valid read decision (PDP up)
  outage-seq       PDP down: cached read -> allow-from-cache; uncached write ->
                   fail-closed; repeat the write --reps times and report
                   fail-closed latency statistics (mean/p50/p95/p99/stddev)
  outage-conc      PDP down: fire --conc simultaneous uncached writes and check
                   that all fail-closed (none fail-open, none exceed the
                   deadline budget) -- worker-thread / connection-pool stress
  outage-expiry    PDP down: after the cached entry is no longer present
                   (its TTL has elapsed; here forced via /cache/clear to avoid a
                   long wait), a read fail-closes instead of being served stale
  recovery         PDP back up: read and maintenance write succeed again

Typical host sequence (see scripts/run_all_experiments.ps1):
  prime -> [stop opa] -> outage-seq -> outage-conc -> outage-expiry ->
  [start opa] -> recovery
"""

from __future__ import annotations

import argparse
import csv
import os
import statistics
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Dict, List

import requests

from src.auth import mint_token

PEP_URL = os.getenv("PEP_URL", "http://modbus-pep:8080")
RESULTS_DIR = Path(os.getenv("RESULTS_DIR", "/app/results"))
OUT = RESULTS_DIR / "pdp_outage_lifecycle.csv"


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


def _token(role="operator", risk=20):
    return mint_token(subject="dev_outage", role=role, risk_score=risk, ttl_seconds=3600)


def read(token, maintenance=False):
    t0 = time.perf_counter()
    r = requests.post(f"{PEP_URL}/modbus/read",
                      json={"register_address": 40001, "count": 1, "maintenance_window": maintenance},
                      headers={"Authorization": f"Bearer {token}"}, timeout=10)
    lat = (time.perf_counter() - t0) * 1000.0
    cache = r.json().get("cache_status", "none") if r.status_code == 200 else "none"
    return r.status_code, lat, cache


def write(token, maintenance=True):
    t0 = time.perf_counter()
    r = requests.post(f"{PEP_URL}/modbus/write",
                      json={"register_address": 40005, "value": 1600, "maintenance_window": maintenance},
                      headers={"Authorization": f"Bearer {token}"}, timeout=10)
    lat = (time.perf_counter() - t0) * 1000.0
    return r.status_code, lat


VARIANT = os.getenv("OUTAGE_VARIANT", "shutdown")


def append_rows(rows: List[Dict]):
    exists = OUT.exists()
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    for r in rows:
        r["variant"] = VARIANT
    with OUT.open("a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["variant", "phase", "check", "result", "detail"])
        if not exists:
            w.writeheader()
        w.writerows(rows)


def phase_prime():
    tok = _token()
    sc, lat, cache = read(tok)
    sc2, lat2, cache2 = read(tok)
    rows = [
        {"phase": "prime", "check": "read_first", "result": sc, "detail": f"{cache} {lat:.1f}ms"},
        {"phase": "prime", "check": "read_cached", "result": sc2, "detail": f"{cache2} {lat2:.1f}ms"},
    ]
    append_rows(rows)
    print(rows)


def phase_outage_seq(reps: int):
    tok = _token()
    sc_read, lat_read, cache_read = read(tok)
    fc_lat = []
    fc_ok = 0
    for _ in range(reps):
        sc, lat = write(tok)
        fc_lat.append(lat)
        if sc == 403:
            fc_ok += 1
    rows = [
        {"phase": "outage_seq", "check": "cached_read_served", "result": sc_read, "detail": f"{cache_read} {lat_read:.1f}ms (expect 200/hit)"},
        {"phase": "outage_seq", "check": "write_fail_closed_rate", "result": f"{fc_ok}/{reps}", "detail": "expect all 403"},
        {"phase": "outage_seq", "check": "fail_closed_latency_ms", "result": "stats",
         "detail": f"mean={statistics.mean(fc_lat):.1f} p50={percentile(fc_lat,0.5):.1f} p95={percentile(fc_lat,0.95):.1f} p99={percentile(fc_lat,0.99):.1f} sd={statistics.pstdev(fc_lat):.1f} n={reps}"},
    ]
    append_rows(rows)
    print(rows)


def phase_outage_conc(conc: int):
    tok = _token()

    def one(_):
        return write(tok)

    t0 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=conc) as ex:
        results = list(ex.map(one, range(conc)))
    span = (time.perf_counter() - t0) * 1000.0
    fail_closed = sum(1 for sc, _ in results if sc == 403)
    fail_open = sum(1 for sc, _ in results if sc == 200)
    max_lat = max(lat for _, lat in results)
    rows = [
        {"phase": "outage_conc", "check": "concurrent_fail_closed", "result": f"{fail_closed}/{conc}", "detail": "expect all 403"},
        {"phase": "outage_conc", "check": "concurrent_fail_open", "result": fail_open, "detail": "expect 0 (never fail-open)"},
        {"phase": "outage_conc", "check": "max_latency_ms", "result": f"{max_lat:.1f}", "detail": f"wall={span:.1f}ms for {conc} simultaneous"},
    ]
    append_rows(rows)
    print(rows)


def phase_outage_expiry():
    tok = _token()
    # Force the "cached entry no longer present" state (TTL elapsed) without a
    # long wait by evicting the cache while the PDP is down.
    try:
        requests.post(f"{PEP_URL}/cache/clear", timeout=5)
    except requests.RequestException:
        pass
    sc, lat, cache = read(tok)
    rows = [
        {"phase": "outage_expiry", "check": "read_after_expiry_fail_closed", "result": sc,
         "detail": f"{cache} {lat:.1f}ms (expect 403; not served stale)"},
    ]
    append_rows(rows)
    print(rows)


def phase_recovery():
    tok = _token()
    tok_maint = _token(role="maintenance_engineer")
    sc_read, lat_read, cache_read = read(tok)
    sc_write, lat_write = write(tok_maint, maintenance=True)
    rows = [
        {"phase": "recovery", "check": "read_after_recovery", "result": sc_read, "detail": f"{cache_read} {lat_read:.1f}ms (expect 200)"},
        {"phase": "recovery", "check": "maint_write_after_recovery", "result": sc_write, "detail": f"{lat_write:.1f}ms (expect 200)"},
    ]
    append_rows(rows)
    print(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--phase", required=True,
                    choices=["prime", "outage-seq", "outage-conc", "outage-expiry", "recovery", "reset"])
    ap.add_argument("--reps", type=int, default=30)
    ap.add_argument("--conc", type=int, default=20)
    args = ap.parse_args()

    if args.phase == "reset":
        if OUT.exists():
            OUT.unlink()
        print("reset lifecycle output")
        return
    {"prime": lambda: phase_prime(),
     "outage-seq": lambda: phase_outage_seq(args.reps),
     "outage-conc": lambda: phase_outage_conc(args.conc),
     "outage-expiry": lambda: phase_outage_expiry(),
     "recovery": lambda: phase_recovery()}[args.phase]()


if __name__ == "__main__":
    main()
