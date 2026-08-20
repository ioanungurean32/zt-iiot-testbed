# OPC UA Experiment Plan

The OPC UA vertical slice (read/write/method authorization). For the full,
current reproduction workflow see `docs/experiment_reproduction.md`; run
everything via `scripts/run_full_evaluation.ps1`.

## Architecture

OPC UA client/loadgen -> OPC UA PEP -> ES256 token verification + decision cache + OPA/PDP -> OPC UA server

The OPC UA PEP is an application-level request-mediation gateway (not a
transparent proxy). A matched pass-through baseline (`opcua-gw`, port 8092) runs
the same image with enforcement disabled to isolate the security overhead. HTTP
endpoints:

- `POST /opcua/read`
- `POST /opcua/write`
- `POST /opcua/call-method`
- `POST /cache/clear`
- `GET /health`
- `GET /resource`

## Policy summary

- Read of `ns=2;s=Line1.*` nodes is allowed for valid devices with risk <= 50.
- Write to `ns=2;s=Line1.SpeedSetpoint` is allowed only for low-risk maintenance engineers during a maintenance window.
- Calling `ns=2;s=Line1.StartMotor` is allowed only for low-risk maintenance engineers during a maintenance window.
- High-risk write or method call (risk 51-75) is degraded to read-only rather than failed open.
- Critical risk is denied/quarantined by the common policy.
- Failed attestation is denied by the common policy.
- Write and method-call decisions are never cached; read decisions may be cached.

## Experiments

The full matched-baseline, workload, saturation, and outage experiments are run
via `scripts/run_full_evaluation.ps1` (see `docs/experiment_reproduction.md`).
OPC UA-only examples:

Functional test:

```powershell
docker compose run --rm loadgen python loadgen/opcua_tests.py
```

Matched-baseline latency (direct / passthrough / pep), 10 trials:

```powershell
docker compose run --rm loadgen python loadgen/harness.py --protocol opcua --target direct      --profile steady --n 1000 --warmup 100 --trials 10
docker compose run --rm loadgen python loadgen/harness.py --protocol opcua --target passthrough --profile steady --n 1000 --warmup 100 --trials 10
docker compose run --rm loadgen python loadgen/harness.py --protocol opcua --target pep         --profile steady --n 1000 --warmup 100 --trials 10 --clear-cache
```

Saturation sweep:

```powershell
docker compose run --rm loadgen python loadgen/saturation_sweep.py --protocol opcua --profile steady --levels 1,2,5,10,20,50 --n 500 --warmup 50
```

## Output files

- `results/raw_opcua_<target>_<profile>_c<concurrency>.csv`
- `results/saturation_opcua_steady.csv`
- `results/opcua_functional_tests.csv`
- `results/summary_stats.csv`, `results/security_overhead.csv` (from `analyze.py`)

## Metrics

- client-observed latency per request: `latency_ms` (raw), aggregated to
  mean ± 95% CI, p50/p95/p99, and jitter (standard deviation) by `analyze.py`.
- cache hit/miss behavior: `cache_status`.
- isolated security overhead: `pep − passthrough` mean difference.
- throughput and PEP CPU/RSS under load: from the saturation sweep.

Note: the OPC UA baseline opens a session per request, so its native latency is
higher than Modbus/MQTT; the passthrough-vs-pep comparison remains valid because
both use the same OPC UA PEP forwarding path.
