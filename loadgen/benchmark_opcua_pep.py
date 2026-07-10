"""Benchmark OPC UA PEP read latency."""

from __future__ import annotations

import argparse
import csv
import os
import statistics
import time
from pathlib import Path

import requests

from src.auth import mint_token


def percentile(values, p: float) -> float:
    if not values:
        return 0.0
    values = sorted(values)
    k = (len(values) - 1) * p
    f = int(k)
    c = min(f + 1, len(values) - 1)
    if f == c:
        return values[int(k)]
    return values[f] * (c - k) + values[c] * (k - f)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--n", type=int, default=100)
    parser.add_argument("--opcua-pep-url", default=os.getenv("OPCUA_PEP_URL", "http://localhost:8082"))
    parser.add_argument("--node-id", default="ns=2;s=Line1.Temperature")
    parser.add_argument("--clear-cache", action="store_true")
    parser.add_argument("--out", default=os.getenv("RESULTS_DIR", "/app/results") + "/opcua_pep_benchmark.csv")
    args = parser.parse_args()

    if args.clear_cache:
        requests.post(f"{args.opcua_pep_url}/cache/clear", timeout=3)

    token = mint_token(subject="opcua_device_01", role="operator", risk_score=20, ttl_seconds=600)
    headers = {"Authorization": f"Bearer {token}"}
    payload = {"node_id": args.node_id}
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)

    rows = []
    latencies = []
    for i in range(args.n):
        t0 = time.perf_counter()
        r = requests.post(f"{args.opcua_pep_url}/opcua/read", json=payload, headers=headers, timeout=5)
        latency_ms = (time.perf_counter() - t0) * 1000.0
        latencies.append(latency_ms)
        try:
            body = r.json()
        except Exception:
            body = {}
        rows.append(
            {
                "i": i,
                "status_code": r.status_code,
                "latency_ms": latency_ms,
                "decision": body.get("decision", {}).get("mode") if r.status_code == 200 else "deny",
                "cache_status": body.get("cache_status") if r.status_code == 200 else "none",
                "node_id": args.node_id,
            }
        )

    with open(args.out, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    print(f"Wrote {args.out}")
    print({
        "n": len(latencies),
        "mean_ms": round(statistics.mean(latencies), 4),
        "p50_ms": round(percentile(latencies, 0.50), 4),
        "p95_ms": round(percentile(latencies, 0.95), 4),
        "p99_ms": round(percentile(latencies, 0.99), 4),
        "jitter_ms_stddev": round(statistics.pstdev(latencies), 4),
        "cache_hits": sum(1 for r in rows if r["cache_status"] == "hit"),
        "cache_misses": sum(1 for r in rows if r["cache_status"] == "miss"),
    })


if __name__ == "__main__":
    main()
