# Experiment Reproduction Instructions

This document describes how to reproduce the ZT-IIoT prototype evaluation
(v2.2.0). All results are regenerated from isolated experiments over independent
repeated trials; no table is derived from a cumulative log. v2.1.0 added the
transparent native-wire Modbus PEP experiments (steps 8–9); v2.2.0 corrects the
cold workload and the partition reconnection and adds the recovery-cycle,
miss-heavy/write-heavy saturation, and PDP-capacity experiments.

## Requirements

- Docker Desktop or Docker Engine with Docker Compose v2.
- Windows PowerShell (the orchestration script is PowerShell), or a Unix shell
  with minor command adjustments for the individual steps.
- Internet access on the first run so Docker can pull
  `openpolicyagent/opa:0.63.0` and `eclipse-mosquitto:2`.

## One-command full evaluation

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run_full_evaluation.ps1
```

This cleans and rebuilds, generates the ES256 key pair, starts all services
(including the pass-through baseline gateways), runs the correctness and
functional tests, the matched-baseline latency matrix, the workload-diversity
profiles, the concurrency/saturation sweep, the latency/overhead analysis, and
the PDP-outage lifecycle (container-shutdown and network-partition variants).
Expect roughly 1–1.5 hours. Raw per-request data and summaries are written to
`results/`.

## Manual steps (for individual experiments)

### 1. Build, keys, start

```powershell
docker compose build
New-Item -ItemType Directory -Force -Path .\keys | Out-Null
docker compose run --rm loadgen python tools/generate_keys.py   # ES256 key pair (mandatory)
docker compose up -d
```

Key generation is mandatory: without it, the issuer and PEPs use mismatched
ephemeral keys and every token is rejected (HTTP 401). The private key stays with
the load generator; each PEP receives only the public verification key.

### 2. Health checks

```powershell
curl.exe http://localhost:8080/health   # modbus-pep
curl.exe http://localhost:8081/health   # mqtt-pep
curl.exe http://localhost:8082/health   # opcua-pep
curl.exe http://localhost:8090/health   # modbus-gw  (pass-through baseline)
curl.exe http://localhost:8091/health   # mqtt-gw
curl.exe http://localhost:8092/health   # opcua-gw
curl.exe http://localhost:8083/health   # modbus-proxy-pep (transparent PEP admin)
curl.exe http://localhost:8181/health   # opa (PDP)
```

### 3. Correctness and functional tests

```powershell
docker compose run --rm loadgen python loadgen/cache_invalidation_tests.py
docker compose run --rm loadgen python loadgen/token_security_tests.py
docker compose run --rm loadgen python loadgen/pep_tests.py
docker compose run --rm loadgen python loadgen/mqtt_tests.py
docker compose run --rm loadgen python loadgen/opcua_tests.py
```

### 4. Matched-baseline latency (per protocol/target/profile)

```powershell
docker compose run --rm loadgen python loadgen/harness.py `
  --protocol modbus --target pep --profile steady --n 1000 --warmup 100 --trials 10 --clear-cache
```

`--target` is `direct` | `passthrough` | `pep`; `--profile` is `steady` | `cold` |
`mixed` | `hitratio:P`; `--concurrency C` runs C concurrent clients. Each run
writes `results/raw_<protocol>_<target>_<profile>_c<concurrency>.csv`.

Miss keys embed the plan seed, so the warm-up and measured phases never share a
key: `cold` is a pure-miss workload (cache-hit ratio 0 on the PEP) and the
achieved ratio of `hitratio:P` equals P up to sampling noise. `analyze.py` warns
if a PEP cold run reports a non-zero hit ratio.

### 5. Concurrency / saturation sweep

```powershell
docker compose run --rm loadgen python loadgen/saturation_sweep.py `
  --protocol modbus --profile steady --levels 1,2,5,10,20,50 --n 500 --warmup 50
```

Run it with `--profile cold` (miss-heavy: every request is a PDP evaluation) and
`--profile mixed` (write-heavy: every other request is a non-cacheable write) to
characterize saturation beyond the cache-dominated workload. The PDP capacity
alone, without a PEP in the path, is measured with:

```powershell
docker compose run --rm loadgen python loadgen/pdp_capacity.py --levels 1,2,5,10,20,50 --n 2000
docker compose run --rm loadgen python loadgen/pdp_capacity.py --levels 1,2,5,10,20,50 --n 2000 --write-share 0.5
```

### 6. Statistical analysis

```powershell
docker compose run --rm loadgen python loadgen/analyze.py
```

Reads all `results/raw_*.csv` and writes `results/summary_stats.csv`
(mean ± 95% CI, p50/p95/p99, jitter, cache-hit ratio) and
`results/security_overhead.csv` (pep − passthrough mean difference with CI).

### 7. PDP-outage lifecycle

Orchestrated with OPA stop/start (and, for the partition variant, network
disconnect/reconnect); see `scripts/run_full_evaluation.ps1` for the full
sequence. The phases are `prime`, `outage-seq`, `outage-conc`, `outage-expiry`,
and `recovery` (`loadgen/pdp_outage_lifecycle.py --phase <name>`).

The partition variant must reconnect OPA with `docker network connect --alias
opa ...`: without the alias the Compose service name no longer resolves and the
PDP stays unreachable after the partition heals.

Recovery over repeated outage cycles is measured from the host:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\pdp_recovery_cycles.ps1 -Cycles 10 -TimeoutSec 60
```

Each cycle primes a cached read, breaks the PDP (partition or shutdown), checks
that the protected write fails closed while the cached read is served, restores
the PDP, and probes the maintenance write until it returns 200. The recovery
time runs from the moment the restore command returns to the first successful
protected write; its resolution is bounded by the probe duration (a failing
probe takes about one PDP deadline). The CSV also records whether `opa`
resolves inside the PEP after the cycle.

### 7b. Corrected and new experiments only

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run_v2.2.0_experiments.ps1
```

Re-runs the cold and hit-ratio profiles, the partition lifecycle, the recovery
cycles, the miss-heavy/write-heavy saturation sweeps, and the PDP capacity sweep,
leaving every other result untouched. The results published with v2.2.0 come
from a complete run of `scripts/run_full_evaluation.ps1`; the v2.1.0 result set
is kept in `results/results_v2.1.0/`.

### 8. Transparent native-wire Modbus PEP: functional + non-bypassability

```powershell
docker compose run --rm loadgen python loadgen/modbus_proxy_tests.py
```

Drives the transparent proxy (`modbus-proxy-pep:5030`) with an unmodified
pymodbus client: authorized read, maintenance write, out-of-window deny,
high-risk degrade, critical-risk quarantine, failed attestation, and expired
token are enforced on the wire, and a direct connection to the protected device
(`modbus-plc`, internal network) is confirmed to be refused. Writes
`results/modbus_proxy_tests.csv`.

### 9. Transparent native-wire Modbus latency matrix

```powershell
docker compose run --rm loadgen python loadgen/modbus_native_harness.py `
  --target native-pep --profile steady --n 1000 --warmup 100 --trials 10
```

`--target` is `native-direct` | `native-passthrough` | `native-pep`; `--profile`
is `steady` (cache-hit path) | `cold` (cache disabled → all-miss path). Each run
writes `results/raw_modbus_<target>_<profile>_c1.csv` in the same schema as
`harness.py`, so step 6 (`analyze.py`) also reports the native-wire overhead
(`native-pep − native-passthrough`; distinguished in `security_overhead.csv` by
the `pep_target` column). Identity and operational context are provisioned to the
proxy out-of-band (the harness mints an ES256 token and pushes it to
`/admin/token`); they are never taken from the Modbus wire.

## Main output files

| File | Description |
|---|---|
| `results/raw_*.csv` | Raw per-request latency for each matched configuration/profile/concurrency. |
| `results/summary_stats.csv` | Aggregated mean ± 95% CI, p50/p95/p99, jitter, cache-hit ratio per configuration. |
| `results/security_overhead.csv` | Isolated security-processing overhead (pep − passthrough) with CI. |
| `results/saturation_*.csv` | Throughput, tail latency, PEP CPU/RSS, and recovery per concurrency level. |
| `results/cache_invalidation_tests.csv` | Context/risk cache-invalidation correctness. |
| `results/token_security_tests.csv` | ES256 token-security correctness (forgery/aud/iss/alg). |
| `results/*_functional_tests.csv` | Functional authorization results per protocol (HTTP gateways). |
| `results/modbus_proxy_tests.csv` | Transparent native-wire PEP functional + non-bypassability results. |
| `results/modbus_proxy_metrics.csv` | Per-request internals of the transparent Modbus PEP. |
| `results/pdp_outage_lifecycle.csv` | Outage lifecycle (shutdown/partition, fail-closed stats, recovery). |
| `results/pdp_recovery_cycles.csv` | Per-cycle fail-closed checks and time to the first successful protected write after PDP restoration. |
| `results/pdp_capacity*.csv` | OPA decision throughput and latency per concurrency level, without a PEP. |
| `results/docker_stats_saturation.csv` | OPA/PEP CPU and memory samples during the v2.2.0 saturation sweeps. |

## Definitions and methodology notes

- **Matched baselines:** *direct* (native client), *passthrough* (same gateway
  image with token/cache/policy disabled), *pep* (full ZT-IIoT). The security
  overhead is `pep − passthrough`; the HTTP/gateway cost is `passthrough − direct`.
- **Trials/warm-up:** each latency condition is 10 trials × 1000 requests with a
  100-request warm-up per trial excluded from the statistics.
- **Percentiles** are reported per distribution and never subtracted. **Jitter**
  is the standard deviation of per-request latency.
- The prototype is a containerized Python application-level gateway; report the
  numbers as prototype-level measurements.
