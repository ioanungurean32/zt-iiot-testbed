# MQTT Experiment Plan

The MQTT vertical slice. For the full, current reproduction workflow (matched
baselines, trials, saturation, outage lifecycle) see
`docs/experiment_reproduction.md`; run everything via
`scripts/run_full_evaluation.ps1`.

## Architecture

MQTT client/loadgen -> MQTT PEP -> ES256 token verification + decision cache + OPA/PDP -> Mosquitto broker

The MQTT PEP is an application-level request-mediation gateway (not a transparent
proxy). A matched pass-through baseline (`mqtt-gw`, port 8091) runs the same image
with enforcement disabled to isolate the security overhead. HTTP endpoints:

- `POST /mqtt/publish`
- `POST /mqtt/authorize-subscribe`
- `POST /cache/clear`
- `GET /health`
- `GET /resource`

## Policy summary

- Telemetry publish to `factory/line1/*` is allowed for valid devices with risk <= 50.
- Command/setpoint publish to `factory/line1/*/(setpoint|cmd|command)` is allowed only for low-risk maintenance engineers during a maintenance window.
- Subscribe to `factory/line1/*` is allowed for valid devices with risk <= 50.
- Failed attestation is denied by the common policy.
- Critical risk is denied/quarantined by the common policy.
- Command-like publish decisions are not cached; telemetry decisions may be cached.

## Experiments

The full matched-baseline, workload, saturation, and outage experiments are run
via `scripts/run_full_evaluation.ps1` (see `docs/experiment_reproduction.md`).
MQTT-only examples:

Functional test:

```powershell
docker compose run --rm loadgen python loadgen/mqtt_tests.py
```

Matched-baseline latency (direct / passthrough / pep), 10 trials:

```powershell
docker compose run --rm loadgen python loadgen/harness.py --protocol mqtt --target direct      --profile steady --n 1000 --warmup 100 --trials 10
docker compose run --rm loadgen python loadgen/harness.py --protocol mqtt --target passthrough --profile steady --n 1000 --warmup 100 --trials 10
docker compose run --rm loadgen python loadgen/harness.py --protocol mqtt --target pep         --profile steady --n 1000 --warmup 100 --trials 10 --clear-cache
```

Saturation sweep:

```powershell
docker compose run --rm loadgen python loadgen/saturation_sweep.py --protocol mqtt --profile steady --levels 1,2,5,10,20,50 --n 500 --warmup 50
```

## Output files

- `results/raw_mqtt_<target>_<profile>_c<concurrency>.csv`
- `results/saturation_mqtt_steady.csv`
- `results/mqtt_functional_tests.csv`
- `results/summary_stats.csv`, `results/security_overhead.csv` (from `analyze.py`)

## Metrics

- client-observed latency per request: `latency_ms` (raw), aggregated to
  mean ± 95% CI, p50/p95/p99, and jitter (standard deviation) by `analyze.py`.
- cache hit/miss behavior: `cache_status`.
- isolated security overhead: `pep − passthrough` mean difference.
- throughput and PEP CPU/RSS under load: from the saturation sweep.
