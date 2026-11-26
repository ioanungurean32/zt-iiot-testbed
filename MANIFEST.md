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
| `docs/token_schema.md` | ES256 trust-token schema and key management. |
| `scripts/run_full_evaluation.ps1` | Single orchestrator for the full evaluation. |
| `scripts/pdp_recovery_cycles.ps1` | PDP-outage recovery over repeated partition and shutdown cycles. |
| `scripts/run_v2.2.0_experiments.ps1` | Re-runs only the experiments affected by the v2.2.0 corrections. |
| `scripts/run_v2.2.1_experiments.ps1` | Re-runs only the transparent Modbus PEP experiments affected by v2.2.1. |

## Source code

| Path | Purpose |
|---|---|
| `src/modbus_pep.py`, `src/mqtt_pep.py`, `src/opcua_pep.py` | Application-level PEP gateways (also run as pass-through baselines via `PASSTHROUGH_MODE`). |
| `src/modbus_proxy_pep.py` | Transparent native-wire Modbus/TCP PEP: in-line TCP proxy that parses function codes from the wire and enforces policy on unmodified clients (also a native pass-through baseline via `PASSTHROUGH_MODE`). |
| `src/modbus_server.py`, `src/opcua_server.py` | Simulated Modbus/TCP and OPC UA servers (`modbus_server.py` also serves the protected `modbus-plc`). |
| `src/auth.py` | ES256 trust-token minting and verification (asymmetric). |
| `src/admin_auth.py` | Shared-secret check for the test-only admin interface of the transparent Modbus PEP. |
| `src/metrics.py` | CSV metrics logging. |
| `src/resource_stats.py` | Per-process CPU/RSS for the `/resource` endpoint. |
| `policies/*.rego` | OPA/Rego authorization policies (with `context_dependent` flags). |
| `tools/generate_keys.py` | Generate the ES256 key pair (private = issuer, public = PEPs) and the proxy admin secret. |
| `tools/mint_token.py` | CLI utility to mint a token for manual testing. |

## Evaluation harness (`loadgen/`)

| Path | Purpose |
|---|---|
| `loadgen/harness.py` | Matched-target latency harness (direct/passthrough/pep), trials, warm-up, concurrency, workload profiles. |
| `loadgen/analyze.py` | Statistics (mean ± 95% CI, percentiles, jitter) and isolated security overhead. |
| `loadgen/saturation_sweep.py` | Concurrency/throughput/saturation sweep with PEP CPU/RSS and recovery. |
| `loadgen/pdp_outage_lifecycle.py` | PDP-unavailability lifecycle (shutdown/partition phases). |
| `loadgen/pdp_capacity.py` | OPA decision throughput/latency under concurrency, without a PEP in the path. |
| `loadgen/cache_invalidation_tests.py` | Cache-invalidation correctness (context/risk). |
| `loadgen/token_security_tests.py` | ES256 token-security correctness. |
| `loadgen/{pep,mqtt,opcua}_tests.py` | Functional allow/deny tests per protocol (HTTP gateways). |
| `loadgen/modbus_native_harness.py` | Matched native-wire latency (`native-direct`/`native-passthrough`/`native-pep`) for the transparent Modbus PEP. |
| `loadgen/modbus_proxy_tests.py` | Transparent-PEP functional enforcement on the wire, per-client identity binding, admin-interface access control, and non-bypassability (bypass) check. |

## Generated outputs

Experiment outputs are written to `results/` and are published alongside the code
for reproducibility: raw per-request latency (`raw_*.csv`), aggregated statistics
(`summary_stats.csv`), isolated security overhead (`security_overhead.csv`),
saturation sweeps (`saturation_*.csv`), correctness suites
(`cache_invalidation_tests.csv`, `token_security_tests.csv`), functional results,
the PDP-outage lifecycle (`pdp_outage_lifecycle.csv`), recovery over repeated
outage cycles (`pdp_recovery_cycles.csv`), and the PDP capacity sweep
(`pdp_capacity*.csv`).

The complete v2.1.0 result set is kept in `results/results_v2.1.0/` for comparison with the
corrected v2.2.0 results. The v2.2.0 files superseded by the v2.2.1 re-run of the
transparent Modbus PEP experiments are kept in `results/results_v2.2.0/`; the
admin requests of that run are in `results/modbus_proxy_admin_audit.csv`.
