# Modbus Experiment Plan

This is the original vertical slice of the ZT-IIoT prototype.

## Architecture

Modbus client/loadgen -> Modbus PEP -> JWT verification + decision cache + OPA/PDP -> Modbus/TCP server

The Modbus PEP is an application-level gateway used for controlled experiments. It exposes HTTP endpoints:

- `POST /modbus/read`
- `POST /modbus/write`
- `POST /cache/clear`
- `GET /health`

## Policy summary

- Read (function code 3) is allowed for valid devices with attestation and risk <= 50.
- Write (function code 6) to registers 40001-40010 is allowed only for low-risk maintenance engineers during a maintenance window.
- High-risk write (risk 51-75) is degraded to read-only rather than failed open.
- Critical risk (risk > 75) is denied/quarantined.
- Failed attestation is denied by the common policy.
- Write decisions are not cached by default (`CACHE_WRITES=false`); read decisions may be cached.

## Experiments

Run:

```powershell
docker compose up --build -d modbus-server opa modbus-pep
```

Health check:

```powershell
curl.exe http://localhost:8080/health
```

Functional tests:

```powershell
docker compose run --rm loadgen python loadgen/pep_tests.py
```

Direct baseline:

```powershell
docker compose run --rm loadgen python loadgen/baseline_modbus.py --n 1000
```

PEP benchmark:

```powershell
docker compose run --rm loadgen python loadgen/benchmark_pep.py --n 1000 --clear-cache
```

PDP outage:

```powershell
docker compose start opa
curl.exe -X POST http://localhost:8080/cache/clear
docker compose run --rm loadgen python loadgen/prime_outage_cache.py --protocol modbus
docker compose stop opa
docker compose run --rm --no-deps loadgen python loadgen/pdp_outage_test.py
docker compose start opa
```

## Output files

- `results/baseline_modbus.csv`
- `results/pep_benchmark.csv`
- `results/pep_functional_tests.csv`
- `results/outage_cache_prime_tests.csv`
- `results/pdp_outage_tests.csv`
- `results/pep_metrics.csv`

## Metrics

- client-observed latency: `latency_ms`
- PEP internal latency: `end_to_end_latency_ms`
- token verification: `token_verification_ms`
- cache lookup: `cache_lookup_ms`
- PDP decision time: `pdp_decision_ms`
- Modbus forwarding time: `modbus_forward_ms`
- cache hit/miss behavior: `cache_status`
