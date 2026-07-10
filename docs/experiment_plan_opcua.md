# OPC UA Experiment Plan

This vertical slice evaluates representative OPC UA read/write/method authorization through the ZT-IIoT PEP.

## Architecture

OPC UA client/loadgen -> OPC UA PEP -> JWT verification + decision cache + OPA/PDP -> OPC UA server

The OPC UA PEP is an application-level gateway used for controlled experiments. It exposes HTTP endpoints:

- `POST /opcua/read`
- `POST /opcua/write`
- `POST /opcua/call-method`
- `POST /cache/clear`
- `GET /health`

## Policy summary

- Read of `ns=2;s=Line1.*` nodes is allowed for valid devices with risk <= 50.
- Write to `ns=2;s=Line1.SpeedSetpoint` is allowed only for low-risk maintenance engineers during a maintenance window.
- Calling `ns=2;s=Line1.StartMotor` is allowed only for low-risk maintenance engineers during a maintenance window.
- High-risk write or method call (risk 51-75) is degraded to read-only rather than failed open.
- Critical risk is denied/quarantined by the common policy.
- Failed attestation is denied by the common policy.
- Write and method-call decisions are never cached; read decisions may be cached.

## Experiments

Run:

```powershell
docker compose up --build -d opcua-server opa opcua-pep
```

Health check:

```powershell
curl.exe http://localhost:8082/health
```

Functional tests:

```powershell
docker compose run --rm loadgen python loadgen/opcua_tests.py
```

Direct baseline:

```powershell
docker compose run --rm loadgen python loadgen/baseline_opcua.py --n 1000
```

PEP benchmark:

```powershell
docker compose run --rm loadgen python loadgen/benchmark_opcua_pep.py --n 1000 --clear-cache
```

PDP outage:

```powershell
docker compose start opa
curl.exe -X POST http://localhost:8082/cache/clear
docker compose run --rm loadgen python loadgen/prime_outage_cache.py --protocol opcua
docker compose stop opa
docker compose run --rm --no-deps loadgen python loadgen/opcua_pdp_outage_test.py
docker compose start opa
```

## Output files

- `results/baseline_opcua.csv`
- `results/opcua_pep_benchmark.csv`
- `results/opcua_functional_tests.csv`
- `results/outage_cache_prime_tests.csv`
- `results/opcua_pdp_outage_tests.csv`
- `results/pep_metrics.csv`

## Metrics

- client-observed latency: `latency_ms`
- PEP internal latency: `end_to_end_latency_ms`
- token verification: `token_verification_ms`
- cache lookup: `cache_lookup_ms`
- PDP decision time: `pdp_decision_ms`
- OPC UA forwarding time: `opcua_forward_ms`
- cache hit/miss behavior: `cache_status`
