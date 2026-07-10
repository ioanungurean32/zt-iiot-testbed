# ZT-IIoT Multi-Protocol Testbed

Protocol-aware Zero Trust IIoT prototype for **Modbus/TCP**, **MQTT**, and **OPC UA**.

This testbed supports experimental evaluation of latency, jitter, cache hit/cache miss behavior, token verification overhead, PDP outage handling, and fail-safe authorization for representative IIoT protocol operations.

## Authors and affiliation

- **Ioan UNGUREAN**  
  Stefan cel Mare University of Suceava, Romania  
  Email: <ioan.ungurean@usm.ro>  
  Institution: <https://usv.ro/en/>

## Scope

The prototype implements three vertical slices:

1. **Modbus/TCP**: client/load generator → Modbus PEP → OPA/PDP → simulated Modbus server.
2. **MQTT**: publisher/load generator → MQTT PEP → OPA/PDP → Mosquitto broker.
3. **OPC UA**: client/load generator → OPC UA PEP → OPA/PDP → minimal OPC UA server.

The PEPs are application-level gateways intended for controlled academic experiments. They are not transparent industrial proxies and are not production-certified OT gateways.

## Services

| Service | Port | Role |
|---|---:|---|
| `modbus-server` | 5020 | Simulated Modbus/TCP device |
| `mqtt-broker` | 1883 | Mosquitto MQTT broker |
| `opcua-server` | 4840 | Minimal OPC UA server |
| `opa` | 8181 | OPA/Rego Policy Decision Point |
| `modbus-pep` | 8080 | Modbus/TCP Policy Enforcement Point |
| `mqtt-pep` | 8081 | MQTT Policy Enforcement Point |
| `opcua-pep` | 8082 | OPC UA Policy Enforcement Point |
| `loadgen` | - | Experiment and benchmark scripts |

## Quick start

```powershell
docker compose up --build -d modbus-server mqtt-broker opcua-server opa modbus-pep mqtt-pep opcua-pep
```

Health checks:

```powershell
curl.exe http://localhost:8080/health
curl.exe http://localhost:8081/health
curl.exe http://localhost:8082/health
curl.exe http://localhost:8181/health
```

## Run experiments

### Modbus/TCP

```powershell
docker compose run --rm loadgen python loadgen/baseline_modbus.py --n 1000
docker compose run --rm loadgen python loadgen/benchmark_pep.py --n 1000 --clear-cache
docker compose run --rm loadgen python loadgen/pep_tests.py
```

### MQTT

```powershell
docker compose run --rm loadgen python loadgen/baseline_mqtt.py --n 1000
docker compose run --rm loadgen python loadgen/benchmark_mqtt_pep.py --n 1000 --clear-cache
docker compose run --rm loadgen python loadgen/mqtt_tests.py
```

### OPC UA

```powershell
docker compose run --rm loadgen python loadgen/baseline_opcua.py --n 1000
docker compose run --rm loadgen python loadgen/benchmark_opcua_pep.py --n 1000 --clear-cache
docker compose run --rm loadgen python loadgen/opcua_tests.py
```

### Full experiment script on Windows

```powershell
.\scripts\run_all_experiments.ps1
```

## Result files

Generated outputs are written to `results/*.csv`.

| File | Meaning |
|---|---|
| `baseline_modbus.csv` | Direct Modbus/TCP latency baseline |
| `pep_benchmark.csv` | Modbus/TCP latency through PEP |
| `baseline_mqtt.csv` | Direct MQTT publish latency baseline |
| `mqtt_pep_benchmark.csv` | MQTT latency through PEP |
| `baseline_opcua.csv` | Direct OPC UA read latency baseline |
| `opcua_pep_benchmark.csv` | OPC UA latency through PEP |
| `pep_metrics.csv` | Internal PEP timing metrics across protocols |
| `*_functional_tests.csv` | Functional authorization tests |
| `outage_cache_prime_tests.csv` | Cache-priming rows used before PDP outage tests |
| `*_pdp_outage_tests.csv` | Control-plane outage/fail-safe tests |
| `summary.csv` | Aggregated mean/p50/p95/p99/jitter and cache/decision counts per input file, written by `loadgen/summarize_results.py` |

## Documentation

- `docs/experiment_reproduction.md`: commands for reproducing the reported experiments.
- `docs/experiment_plan_modbus.md`, `docs/experiment_plan_mqtt.md`, `docs/experiment_plan_opcua.md`: per-protocol experiment plans, policy summaries, and metrics.
- `docs/testbed_description.md`: scope, claims, and limitations.
- `docs/token_schema.md`: JWT claim schema used by the PEPs and policies.
- `MANIFEST.md`: full file manifest.

## Citation

Please cite this testbed using the metadata in `CITATION.cff`.

## License

MIT License. See `LICENSE`.

## Note on PDP outage tests

Version v0.1.4 makes the outage experiments deterministic by adding an explicit cache-priming step before OPA/PDP is stopped:

```powershell
docker compose run --rm loadgen python loadgen/prime_outage_cache.py --protocol modbus
docker compose run --rm loadgen python loadgen/prime_outage_cache.py --protocol mqtt
docker compose run --rm loadgen python loadgen/prime_outage_cache.py --protocol opcua
```

The PEP services use short, configurable PDP client timeouts to return fail-closed decisions quickly when OPA/PDP is intentionally stopped:

- `OPA_CONNECT_TIMEOUT=0.10`
- `OPA_READ_TIMEOUT=0.30`

Cached read/telemetry operations are served from the local PEP cache, while write/command operations without a current PDP decision are denied fail-closed.


## MQTT benchmark note

The MQTT PEP uses a persistent MQTT client and a persistent Paho network loop. This avoids per-request connect/loop startup overhead and makes MQTT PEP latency measurements comparable with the Modbus/TCP and OPC UA benchmark paths.


### Troubleshooting: `prime_outage_cache.py` not found in `/app/loadgen`

If Docker reports `python: can't open file '/app/loadgen/prime_outage_cache.py'`, the local folder may be correct but Docker is reusing an older `loadgen` image. Rebuild the load generator explicitly:

```powershell
docker compose build --no-cache loadgen
```

Then rerun:

```powershell
powershell.exe -ExecutionPolicy Bypass -File .\scripts\run_all_experiments.ps1
```
