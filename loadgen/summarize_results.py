"""Summarize CSV latency results into publication-friendly metrics.

Works with:
- loadgen/benchmark_pep.py output: latency_ms
- PEP internal metrics output: end_to_end_latency_ms
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

import pandas as pd


def pick_latency_column(df: pd.DataFrame) -> str:
    candidates = ["latency_ms", "end_to_end_latency_ms"]
    for col in candidates:
        if col in df.columns:
            return col
    raise KeyError(
        f"No latency column found. Expected one of {candidates}; found {list(df.columns)}"
    )


def count_value(df: pd.DataFrame, col: str, value: str) -> int:
    if col not in df.columns:
        return 0
    return int((df[col].astype(str) == value).sum())


def summarize(path: Path) -> dict:
    df = pd.read_csv(path)
    latency_col = pick_latency_column(df)
    lat = pd.to_numeric(df[latency_col], errors="coerce").dropna()

    row = {
        "file": path.name,
        "latency_column": latency_col,
        "n": len(lat),
        "mean_ms": round(float(lat.mean()), 4) if len(lat) else 0.0,
        "p50_ms": round(float(lat.quantile(0.50)), 4) if len(lat) else 0.0,
        "p95_ms": round(float(lat.quantile(0.95)), 4) if len(lat) else 0.0,
        "p99_ms": round(float(lat.quantile(0.99)), 4) if len(lat) else 0.0,
        "jitter_ms_stddev": round(float(lat.std(ddof=0)), 4) if len(lat) else 0.0,
        "cache_hits": count_value(df, "cache_status", "hit"),
        "cache_misses": count_value(df, "cache_status", "miss"),
        "allows": count_value(df, "decision", "allow"),
        "denies": count_value(df, "decision", "deny"),
    }

    for col in ["token_verification_ms", "cache_lookup_ms", "pdp_decision_ms", "modbus_forward_ms", "mqtt_forward_ms", "opcua_forward_ms"]:
        if col in df.columns:
            values = pd.to_numeric(df[col], errors="coerce").dropna()
            if len(values):
                row[f"{col}_mean"] = round(float(values.mean()), 4)
                row[f"{col}_p95"] = round(float(values.quantile(0.95)), 4)

    return row


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("files", nargs="+", help="CSV files with latency_ms or end_to_end_latency_ms column")
    parser.add_argument("--out", default=os.getenv("RESULTS_DIR", "/app/results") + "/summary.csv")
    args = parser.parse_args()
    rows = [summarize(Path(f)) for f in args.files]
    df = pd.DataFrame(rows)
    print(df.to_string(index=False))

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out_path, index=False)
    print(f"Wrote {out_path}")


if __name__ == "__main__":
    main()
