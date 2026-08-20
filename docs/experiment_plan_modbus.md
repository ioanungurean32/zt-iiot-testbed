# Modbus Experiment Plan

The Modbus/TCP vertical slice of the ZT-IIoT prototype. For the full,
current reproduction workflow see `docs/experiment_reproduction.md`; this file
summarizes the Modbus-specific policy and endpoints.

## Architecture

Modbus/TCP is served by two enforcement styles that share the same ABAC/OPA
policy, decision cache, and fail-safe behavior:

**1. Application-level HTTP gateway** (`modbus-pep`, port 8080):

Modbus loadgen -> HTTP request -> ES256 token verification + decision cache + OPA/PDP -> Modbus/TCP server

Attributes are supplied through the gateway API. A matched pass-through baseline
(`modbus-gw`, port 8090) runs the same image with enforcement disabled to isolate
the security overhead. HTTP endpoints: `POST /modbus/read`, `POST /modbus/write`,
`POST /cache/clear`, `GET /health`, `GET /resource` (CPU/RSS, for the saturation
sweep).

**2. Transparent native-wire PEP** (`modbus-proxy-pep`, wire port 5030, admin 8083):

unmodified pymodbus client -> transparent proxy (parses the function code from the wire) -> Modbus/TCP device

Unmodified clients connect as if it were the PLC; the enforced action is the
operation parsed from the wire, not a client-supplied field. The protected device
(`modbus-plc`) is on an internal network reachable only through the proxy, so a
direct client-to-device connection is refused (non-bypassability). A native
pass-through baseline (`modbus-proxy-gw`, port 5031) isolates the native-wire
security overhead. Identity/context are provisioned out-of-band via the admin
endpoints (`/admin/token`, `/admin/context`, `/admin/cache`, `/cache/clear`,
`/health`, `/resource`).

## Policy summary

- Read (function code 3) is allowed for valid devices with attestation and risk <= 50.
- Write (function code 6) to registers 40001-40010 is allowed only for low-risk maintenance engineers during a maintenance window.
- High-risk write (risk 51-75) is degraded to read-only rather than failed open.
- Critical risk (risk > 75) is denied/quarantined.
- Failed attestation is denied by the common policy.
- Write decisions are not cached (`CACHE_WRITES=false`); the maintenance-write
  rule reads `environment.maintenance_window` and is flagged `context_dependent`,
  so it is structurally non-cacheable. Read decisions may be cached; the cache key
  binds operational context, risk bucket, and policy version.

## Experiments

The full matched-baseline, workload, saturation, and outage experiments are run
via `scripts/run_full_evaluation.ps1` (see `docs/experiment_reproduction.md`).
Modbus-only examples:

Functional and correctness tests:

```powershell
docker compose run --rm loadgen python loadgen/pep_tests.py
docker compose run --rm loadgen python loadgen/cache_invalidation_tests.py
docker compose run --rm loadgen python loadgen/token_security_tests.py
```

Matched-baseline latency (direct / passthrough / pep), 10 trials:

```powershell
docker compose run --rm loadgen python loadgen/harness.py --protocol modbus --target direct      --profile steady --n 1000 --warmup 100 --trials 10
docker compose run --rm loadgen python loadgen/harness.py --protocol modbus --target passthrough --profile steady --n 1000 --warmup 100 --trials 10
docker compose run --rm loadgen python loadgen/harness.py --protocol modbus --target pep         --profile steady --n 1000 --warmup 100 --trials 10 --clear-cache
```

Saturation sweep:

```powershell
docker compose run --rm loadgen python loadgen/saturation_sweep.py --protocol modbus --profile steady --levels 1,2,5,10,20,50 --n 500 --warmup 50
```

Transparent native-wire PEP (functional + non-bypassability, then the native
matched-latency matrix):

```powershell
docker compose run --rm loadgen python loadgen/modbus_proxy_tests.py
docker compose run --rm loadgen python loadgen/modbus_native_harness.py --target native-direct      --profile steady --n 1000 --warmup 100 --trials 10
docker compose run --rm loadgen python loadgen/modbus_native_harness.py --target native-passthrough --profile steady --n 1000 --warmup 100 --trials 10
docker compose run --rm loadgen python loadgen/modbus_native_harness.py --target native-pep         --profile steady --n 1000 --warmup 100 --trials 10
```

## Output files

- `results/raw_modbus_<target>_<profile>_c<concurrency>.csv` (HTTP and `native-*` targets)
- `results/saturation_modbus_steady.csv`
- `results/pep_functional_tests.csv`, `results/cache_invalidation_tests.csv`, `results/token_security_tests.csv`
- `results/modbus_proxy_tests.csv` (transparent-PEP functional + non-bypassability), `results/modbus_proxy_metrics.csv`
- `results/summary_stats.csv`, `results/security_overhead.csv` (from `analyze.py`; `security_overhead.csv` includes the `native-pep` row via `pep_target`)

## Metrics

- client-observed latency per request: `latency_ms` (raw), aggregated to
  mean ± 95% CI, p50/p95/p99, and jitter (standard deviation) by `analyze.py`.
- cache hit/miss behavior: `cache_status`.
- isolated security overhead: `pep − passthrough` mean difference.
- throughput and PEP CPU/RSS under load: from the saturation sweep.
