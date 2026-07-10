"""Direct Modbus baseline: client -> Modbus server, without PEP/PDP."""

from __future__ import annotations

import argparse
import csv
import os
import statistics
import time
from pathlib import Path

from pymodbus.client import ModbusTcpClient


def logical_to_offset(address: int) -> int:
    return address - 40001 if address >= 40001 else address


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
    parser.add_argument("--host", default=os.getenv("MODBUS_HOST", "localhost"))
    parser.add_argument("--port", type=int, default=int(os.getenv("MODBUS_PORT", "5020")))
    parser.add_argument("--address", type=int, default=40001)
    parser.add_argument("--count", type=int, default=1)
    parser.add_argument("--out", default=os.getenv("RESULTS_DIR", "/app/results") + "/baseline_modbus.csv")
    args = parser.parse_args()

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    latencies = []

    with open(args.out, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["i", "status", "latency_ms", "registers"])
        writer.writeheader()

        client = ModbusTcpClient(args.host, port=args.port)
        if not client.connect():
            raise SystemExit(f"Cannot connect to Modbus server at {args.host}:{args.port}")
        try:
            for i in range(args.n):
                t0 = time.perf_counter()
                response = client.read_holding_registers(address=logical_to_offset(args.address), count=args.count, slave=1)
                latency = (time.perf_counter() - t0) * 1000.0
                status = "error" if response.isError() else "ok"
                registers = getattr(response, "registers", [])
                latencies.append(latency)
                writer.writerow({"i": i, "status": status, "latency_ms": latency, "registers": registers})
        finally:
            client.close()

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
