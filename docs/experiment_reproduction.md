# Experiment Reproduction Instructions

This document describes how to reproduce the experiments for the ZT-IIoT prototype.

## Requirements

- Docker Desktop or Docker Engine with Docker Compose v2.
- Windows PowerShell, PowerShell 7, or a Unix shell with minor command adjustments.
- Internet access for the first run, so Docker can pull `openpolicyagent/opa:0.63.0` and `eclipse-mosquitto:2`.

## Clean start

```powershell
docker compose down --remove-orphans
Remove-Item .\results\* -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path .\results | Out-Null
```

## Start all services

```powershell
docker compose up --build -d modbus-server mqtt-broker opcua-server opa modbus-pep mqtt-pep opcua-pep
```

## Health checks

```powershell
curl.exe http://localhost:8080/health
curl.exe http://localhost:8081/health
curl.exe http://localhost:8082/health
curl.exe http://localhost:8181/health
```

## Modbus/TCP experiments

```powershell
docker compose run --rm loadgen python loadgen/baseline_modbus.py --n 1000
docker compose run --rm loadgen python loadgen/benchmark_pep.py --n 1000 --clear-cache
docker compose run --rm loadgen python loadgen/pep_tests.py
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

## MQTT experiments

```powershell
docker compose run --rm loadgen python loadgen/baseline_mqtt.py --n 1000
docker compose run --rm loadgen python loadgen/benchmark_mqtt_pep.py --n 1000 --clear-cache
docker compose run --rm loadgen python loadgen/mqtt_tests.py
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

## OPC UA experiments

```powershell
docker compose run --rm loadgen python loadgen/baseline_opcua.py --n 1000
docker compose run --rm loadgen python loadgen/benchmark_opcua_pep.py --n 1000 --clear-cache
docker compose run --rm loadgen python loadgen/opcua_tests.py
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

## Summarize results

```powershell
docker compose run --rm loadgen python loadgen/summarize_results.py `
  /app/results/baseline_modbus.csv `
  /app/results/pep_benchmark.csv `
  /app/results/baseline_mqtt.csv `
  /app/results/mqtt_pep_benchmark.csv `
  /app/results/baseline_opcua.csv `
  /app/results/opcua_pep_benchmark.csv `
  /app/results/pep_metrics.csv
```

## Main output files

| File | Description |
|---|---|
| `results/baseline_modbus.csv` | Direct Modbus/TCP baseline. |
| `results/pep_benchmark.csv` | Modbus/TCP via PEP. |
| `results/baseline_mqtt.csv` | Direct MQTT baseline. |
| `results/mqtt_pep_benchmark.csv` | MQTT via PEP. |
| `results/baseline_opcua.csv` | Direct OPC UA baseline. |
| `results/opcua_pep_benchmark.csv` | OPC UA via PEP. |
| `results/pep_metrics.csv` | Internal timing metrics for PEP pipeline stages. |
| `results/*functional_tests.csv` | Functional authorization results. |
| `results/outage_cache_prime_tests.csv` | Cache-priming rows executed while PDP is online before outage tests. |
| `results/*pdp_outage_tests.csv` | PDP outage/fail-safe behavior. |
| `results/summary.csv` | Aggregated mean/p50/p95/p99/jitter and cache/decision counts, written by the "Summarize results" step below. |

## Interpretation note

The prototype is implemented in Python and runs in Docker containers. Report the results as prototype-level measurements. To isolate Zero Trust overhead, compare each PEP-mediated result against the direct baseline for the same protocol, executed in the same environment.
