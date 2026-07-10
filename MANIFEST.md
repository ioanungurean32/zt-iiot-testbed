# Testbed Manifest

This package contains a Zero Trust IIoT experimental testbed for three representative industrial protocols.

## Core files

| Path | Purpose |
|---|---|
| `docker-compose.yml` | Starts all services and protocol slices. |
| `Dockerfile` | Builds the Python-based services and load generators. |
| `requirements.txt` | Python dependency versions. |
| `.env.example` | Example development environment variables. |
| `Makefile` | Convenience targets for common Docker Compose commands. |
| `mosquitto/mosquitto.conf` | Mosquitto broker configuration for the MQTT slice. |
| `README.md` | Quick-start instructions. |
| `CHANGELOG.md` | Version history of the testbed. |
| `CITATION.cff` | Citation metadata for the testbed software. |
| `VERSION` | Current testbed version string. |
| `LICENSE` | MIT license text. |
| `docs/experiment_reproduction.md` | Commands and settings for repeating the reported experiments. |
| `docs/experiment_plan_modbus.md` | Modbus/TCP experiment plan and metrics. |
| `docs/experiment_plan_mqtt.md` | MQTT experiment plan and metrics. |
| `docs/experiment_plan_opcua.md` | OPC UA experiment plan and metrics. |
| `docs/testbed_description.md` | Testbed scope, claims, and limitations. |
| `docs/token_schema.md` | JWT claim schema used by the PEPs and policies. |
| `scripts/run_all_experiments.ps1` | PowerShell helper for running the full experiment sequence. |

## Source code

| Path | Purpose |
|---|---|
| `src/modbus_pep.py` | Modbus/TCP Policy Enforcement Point. |
| `src/mqtt_pep.py` | MQTT Policy Enforcement Point. |
| `src/opcua_pep.py` | OPC UA Policy Enforcement Point. |
| `src/modbus_server.py` | Simulated Modbus/TCP server. |
| `src/opcua_server.py` | Minimal OPC UA server. |
| `src/auth.py` | JWT minting and verification helpers. |
| `src/metrics.py` | CSV metrics logging. |
| `policies/*.rego` | OPA/Rego authorization policies. |
| `loadgen/*.py` | Baseline, benchmark, functional, cache-priming, and outage tests. |
| `tools/mint_token.py` | CLI utility to mint JWTs for manual testing. |

## Generated outputs

Experiment outputs are written to `results/` and are published alongside the code for reproducibility (baseline and PEP-benchmark CSVs, PEP internal metrics, functional-test results, PDP-outage-test results, and the aggregated `summary.csv`).
