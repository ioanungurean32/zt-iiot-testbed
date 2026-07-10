# MQTT Experiment Plan

This adds a lightweight MQTT vertical slice to the Modbus prototype.

## Architecture

MQTT client/loadgen -> MQTT PEP -> JWT verification + decision cache + OPA/PDP -> Mosquitto broker

The MQTT PEP is an application-level gateway used for controlled experiments. It exposes HTTP endpoints:

- `POST /mqtt/publish`
- `POST /mqtt/authorize-subscribe`
- `POST /cache/clear`
- `GET /health`

## Policy summary

- Telemetry publish to `factory/line1/*` is allowed for valid devices with risk <= 50.
- Command/setpoint publish to `factory/line1/*/(setpoint|cmd|command)` is allowed only for low-risk maintenance engineers during a maintenance window.
- Subscribe to `factory/line1/*` is allowed for valid devices with risk <= 50.
- Failed attestation is denied by the common policy.
- Critical risk is denied/quarantined by the common policy.
- Command-like publish decisions are not cached; telemetry decisions may be cached.

## Experiments

Run:

```powershell
docker compose up --build -d mqtt-broker opa mqtt-pep
```

Health check:

```powershell
curl.exe http://localhost:8081/health
```

Functional tests:

```powershell
docker compose run --rm loadgen python loadgen/mqtt_tests.py
```

Direct baseline:

```powershell
docker compose run --rm loadgen python loadgen/baseline_mqtt.py --n 1000
```

PEP benchmark:

```powershell
docker compose run --rm loadgen python loadgen/benchmark_mqtt_pep.py --n 1000 --clear-cache
```

PDP outage:

```powershell
docker compose start opa
curl.exe -X POST http://localhost:8081/cache/clear
docker compose run --rm loadgen python loadgen/prime_outage_cache.py --protocol mqtt
docker compose stop opa
docker compose run --rm --no-deps loadgen python loadgen/mqtt_pdp_outage_test.py
docker compose start opa
```

## Output files

- `results/baseline_mqtt.csv`
- `results/mqtt_pep_benchmark.csv`
- `results/mqtt_functional_tests.csv`
- `results/mqtt_pdp_outage_tests.csv`
- `results/pep_metrics.csv`

## Metrics

- client-observed latency: `latency_ms`
- PEP internal latency: `end_to_end_latency_ms`
- token verification: `token_verification_ms`
- cache lookup: `cache_lookup_ms`
- PDP decision time: `pdp_decision_ms`
- MQTT forwarding time: `mqtt_forward_ms`
- cache hit/miss behavior: `cache_status`
