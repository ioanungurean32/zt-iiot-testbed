"""Direct MQTT baseline: publisher -> MQTT broker, without PEP/PDP."""

from __future__ import annotations

import argparse
import csv
import os
import statistics
import time
import uuid
from pathlib import Path

import paho.mqtt.client as mqtt


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
    parser.add_argument("--host", default=os.getenv("MQTT_HOST", "localhost"))
    parser.add_argument("--port", type=int, default=int(os.getenv("MQTT_PORT", "1883")))
    parser.add_argument("--topic", default="factory/line1/plc1/status")
    parser.add_argument("--payload", default='{"temperature":21.0}')
    parser.add_argument("--qos", type=int, default=0, choices=[0, 1])
    parser.add_argument("--out", default=os.getenv("RESULTS_DIR", "/app/results") + "/baseline_mqtt.csv")
    args = parser.parse_args()

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    client = mqtt.Client(client_id=f"zt-mqtt-baseline-{uuid.uuid4()}", clean_session=True)
    client.connect(args.host, args.port, keepalive=30)
    client.loop_start()

    rows = []
    latencies = []
    try:
        for i in range(args.n):
            t0 = time.perf_counter()
            info = client.publish(args.topic, payload=args.payload, qos=args.qos, retain=False)
            info.wait_for_publish(timeout=5)
            latency_ms = (time.perf_counter() - t0) * 1000.0
            latencies.append(latency_ms)
            rows.append({"i": i, "status_code": 200 if info.rc == mqtt.MQTT_ERR_SUCCESS else 502, "latency_ms": latency_ms, "topic": args.topic, "qos": args.qos})
    finally:
        client.loop_stop()
        client.disconnect()

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
