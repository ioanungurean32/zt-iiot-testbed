"""Direct OPC UA baseline: client -> OPC UA server, without PEP/PDP."""

from __future__ import annotations

import argparse
import asyncio
import csv
import os
import statistics
import time
from pathlib import Path

from asyncua import Client


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


async def read_once(endpoint: str, node_id: str):
    async with Client(url=endpoint) as client:
        node = client.get_node(node_id)
        return await node.read_value()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--n", type=int, default=100)
    parser.add_argument("--endpoint", default=os.getenv("OPCUA_ENDPOINT", "opc.tcp://opcua-server:4840/zt-iiot/opcua/server/"))
    parser.add_argument("--node-id", default="ns=2;s=Line1.Temperature")
    parser.add_argument("--out", default=os.getenv("RESULTS_DIR", "/app/results") + "/baseline_opcua.csv")
    args = parser.parse_args()

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    rows = []
    latencies = []
    for i in range(args.n):
        t0 = time.perf_counter()
        value = asyncio.run(read_once(args.endpoint, args.node_id))
        latency_ms = (time.perf_counter() - t0) * 1000.0
        latencies.append(latency_ms)
        rows.append({"i": i, "status_code": 200, "latency_ms": latency_ms, "node_id": args.node_id, "value": value})

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
    })


if __name__ == "__main__":
    main()
