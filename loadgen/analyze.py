"""Statistical analysis of harness.py raw per-request output.

Reads one or more raw_*.csv files produced by harness.py and reports, per file:
  * pooled mean, p50, p95, p99, and jitter (standard deviation of per-request
    latency);
  * the mean latency per trial and a 95% confidence interval of the mean across
    independent trials (t-based).

When a passthrough file and a pep file for the same protocol/profile are both
present, it also reports the incremental security-processing overhead as the
difference of the two mean latencies with a 95% CI. Only means are subtracted;
percentiles are reported per distribution and never subtracted, because the
difference of independently estimated percentiles is not the percentile of the
overhead distribution.

Usage:
  python loadgen/analyze.py                 # analyze all results/raw_*.csv
  python loadgen/analyze.py results/raw_modbus_pep_steady_c1.csv ...
"""

from __future__ import annotations

import csv
import glob
import math
import os
import statistics
import sys
from collections import defaultdict
from pathlib import Path
from typing import Dict, List

RESULTS_DIR = Path(os.getenv("RESULTS_DIR", "/app/results"))

# t_{0.975, df} for small samples; falls back to 1.96 for df >= 30.
_T = {1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571, 6: 2.447, 7: 2.365,
      8: 2.306, 9: 2.262, 10: 2.228, 15: 2.131, 20: 2.086, 25: 2.060, 29: 2.045}


def t_crit(df: int) -> float:
    if df <= 0:
        return 0.0
    if df in _T:
        return _T[df]
    if df >= 30:
        return 1.96
    keys = sorted(_T)
    for k in keys:
        if k >= df:
            return _T[k]
    return 1.96


def percentile(values: List[float], p: float) -> float:
    if not values:
        return 0.0
    v = sorted(values)
    k = (len(v) - 1) * p
    f = int(k)
    c = min(f + 1, len(v) - 1)
    if f == c:
        return v[int(k)]
    return v[f] * (c - k) + v[c] * (k - f)


def load(path: str):
    rows = []
    with open(path, newline="") as f:
        for r in csv.DictReader(f):
            r["latency_ms"] = float(r["latency_ms"])
            r["trial"] = int(r["trial"])
            rows.append(r)
    return rows


def summarize(path: str) -> Dict:
    rows = load(path)
    errors = sum(1 for r in rows if str(r.get("status")) == "0")
    ok_rows = [r for r in rows if str(r.get("status")) != "0"]
    lat = [r["latency_ms"] for r in ok_rows]
    by_trial = defaultdict(list)
    for r in ok_rows:
        by_trial[r["trial"]].append(r["latency_ms"])
    trial_means = [statistics.mean(v) for v in by_trial.values()]
    T = len(trial_means)
    grand = statistics.mean(trial_means) if trial_means else 0.0
    if T >= 2:
        se = statistics.stdev(trial_means) / math.sqrt(T)
        ci = t_crit(T - 1) * se
    else:
        ci = 0.0
    hits = sum(1 for r in ok_rows if r["cache_status"] == "hit")
    return {
        "file": os.path.basename(path),
        "protocol": rows[0]["protocol"], "target": rows[0]["target"],
        "profile": rows[0]["profile"], "concurrency": rows[0]["concurrency"],
        "trials": T, "n_total": len(ok_rows), "errors": errors,
        "mean_ms": round(grand, 4), "ci95_ms": round(ci, 4),
        "p50_ms": round(percentile(lat, 0.50), 4),
        "p95_ms": round(percentile(lat, 0.95), 4),
        "p99_ms": round(percentile(lat, 0.99), 4),
        "jitter_ms_stddev": round(statistics.pstdev(lat), 4) if lat else 0.0,
        "cache_hit_ratio": round(hits / len(ok_rows), 4) if ok_rows else 0.0,
        "_trial_means": trial_means,
    }


def overhead(passthrough: Dict, pep: Dict) -> Dict:
    a = pep["_trial_means"]
    b = passthrough["_trial_means"]
    diff_mean = pep["mean_ms"] - passthrough["mean_ms"]
    # CI of the difference of two independent means (Welch).
    ci = 0.0
    if len(a) >= 2 and len(b) >= 2:
        va = statistics.variance(a) / len(a)
        vb = statistics.variance(b) / len(b)
        se = math.sqrt(va + vb)
        df = (va + vb) ** 2 / ((va ** 2) / (len(a) - 1) + (vb ** 2) / (len(b) - 1)) if (va + vb) > 0 else 1
        ci = t_crit(int(df)) * se
    return {
        "protocol": pep["protocol"], "profile": pep["profile"], "concurrency": pep["concurrency"],
        "pep_target": pep["target"], "baseline_target": passthrough["target"],
        "security_overhead_mean_ms": round(diff_mean, 4), "ci95_ms": round(ci, 4),
        "passthrough_mean_ms": passthrough["mean_ms"], "pep_mean_ms": pep["mean_ms"],
        "passthrough_p95_ms": passthrough["p95_ms"], "pep_p95_ms": pep["p95_ms"],
        "passthrough_p99_ms": passthrough["p99_ms"], "pep_p99_ms": pep["p99_ms"],
    }


def main() -> None:
    args = sys.argv[1:]
    files = args if args else sorted(glob.glob(str(RESULTS_DIR / "raw_*.csv")))
    if not files:
        print("No raw_*.csv files found.")
        return

    summaries = [summarize(f) for f in files]
    out = RESULTS_DIR / "summary_stats.csv"
    cols = ["file", "protocol", "target", "profile", "concurrency", "trials", "n_total", "errors",
            "mean_ms", "ci95_ms", "p50_ms", "p95_ms", "p99_ms", "jitter_ms_stddev", "cache_hit_ratio"]
    with out.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for s in summaries:
            w.writerow({k: s[k] for k in cols})
            print({k: s[k] for k in ("target", "protocol", "profile", "concurrency",
                                     "mean_ms", "ci95_ms", "p95_ms", "p99_ms", "cache_hit_ratio")})
    print(f"Wrote {out}")

    # Pair each PEP target with its matched pass-through baseline by
    # (protocol, profile, concurrency) for the isolated security overhead. The
    # HTTP gateway uses pep/passthrough; the transparent native-wire proxy uses
    # native-pep/native-passthrough (v2.1.0).
    pep_pairs = {"pep": "passthrough", "native-pep": "native-passthrough"}
    index = {(s["protocol"], s["profile"], s["concurrency"], s["target"]): s for s in summaries}
    over_rows = []
    for (proto, profile, conc, target), s in index.items():
        base_target = pep_pairs.get(target)
        if base_target is None:
            continue
        pt = index.get((proto, profile, conc, base_target))
        if pt:
            over_rows.append(overhead(pt, s))
    if over_rows:
        oout = RESULTS_DIR / "security_overhead.csv"
        with oout.open("w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(over_rows[0].keys()))
            w.writeheader()
            w.writerows(over_rows)
        print(f"\nSecurity-processing overhead (pep - passthrough):")
        for r in over_rows:
            print(r)
        print(f"Wrote {oout}")


if __name__ == "__main__":
    main()
