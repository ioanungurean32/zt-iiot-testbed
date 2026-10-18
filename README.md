# ZT-IIoT Multi-Protocol Testbed (v2.2.0)

Zero Trust IIoT prototype for **Modbus/TCP**, **MQTT**, and **OPC UA**, with
matched baselines that isolate the security-processing cost, asymmetric (ES256)
trust tokens, a concurrency/saturation sweep, and a PDP-outage lifecycle
evaluation. Version 2.1.0 added a **transparent native-wire protocol-aware Modbus/TCP
PEP** that parses function codes directly from the wire, enforces policy on
**unmodified** clients, and demonstrates non-bypassability.

**Version 2.2.0** corrects two measurement defects (overlapping warm-up/measured
keys in the cold workload; PDP re-attached without its service alias in the
network-partition test), adds recovery over repeated outage cycles, miss-heavy and
write-heavy saturation sweeps, and a PDP-only capacity sweep, and ships a complete
result set regenerated in a single run of `scripts/run_full_evaluation.ps1`. The
v2.1.0 result set is kept in `results/results_v2.1.0/` for comparison. See
`CHANGELOG.md`.

## Authors and affiliation

- **Ioan UNGUREAN**  
  Stefan cel Mare University of Suceava, Romania  
  Email: <ioan.ungurean@usm.ro>  
  Institution: <https://usv.ro/en/>

## Scope

Three protocol vertical slices, each: client/load generator → PEP → OPA/PDP →
simulated server/broker. Two enforcement styles are provided:

- **Application-level request-mediation gateways** (HTTP) for Modbus/TCP, MQTT,
  and OPC UA: attributes are supplied through the gateway API rather than parsed
  from the native wire, and non-bypassability is assumed. These remain the basis
  of the multi-protocol latency, workload, saturation, and outage experiments.
- **Transparent native-wire Modbus/TCP PEP** (v2.1.0): an in-line TCP proxy that
  unmodified pymodbus clients connect to as if it were the PLC. It parses the
  Modbus function code/address from the wire, derives the action from the parsed
  operation (not a client field), enforces the same ABAC/OPA policy, and forwards
  only allowed frames. The protected device sits on an internal network reachable
  only through the proxy, so non-bypassability is **demonstrated, not assumed**.

Neither is a production-certified OT gateway. See `docs/testbed_description.md`
for the full list of what the testbed does and does not claim.

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
| `modbus-gw` | 8090 | Modbus pass-through baseline (enforcement disabled) |
| `mqtt-gw` | 8091 | MQTT pass-through baseline |
| `opcua-gw` | 8092 | OPC UA pass-through baseline |
| `modbus-plc` | (internal) | Protected Modbus device, reachable only via the transparent proxy |
| `modbus-proxy-pep` | 5030 / 8083 | Transparent native-wire Modbus PEP (wire port / admin) |
| `modbus-proxy-gw` | 5031 | Transparent native pass-through baseline (`PASSTHROUGH_MODE`) |
| `loadgen` | - | Load generator, harness, analysis, and tests |

The pass-through gateways run the same PEP images with `PASSTHROUGH_MODE=true`
(no token verification, cache, or policy), giving a matched baseline whose
difference from the full PEP isolates the incremental Zero-Trust cost.

## Quick start

```powershell
docker compose build
docker compose run --rm loadgen python tools/generate_keys.py   # ES256 key pair (mandatory)
docker compose up -d
```

Key generation is mandatory: the issuer holds the private key and each PEP holds
only the public verification key; without it, every token is rejected (HTTP 401).

Health checks:

```powershell
curl.exe http://localhost:8080/health   # PEPs: 8080/8081/8082
curl.exe http://localhost:8090/health   # gateways: 8090/8091/8092
curl.exe http://localhost:8181/health   # OPA
```

## Run the full evaluation

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run_full_evaluation.ps1
```

Runs correctness/functional tests, the matched-baseline latency matrix (10 trials
with confidence intervals), workload-diversity profiles, the concurrency/
saturation sweep, the **transparent native-wire Modbus PEP** functional and
non-bypassability tests plus its matched native latency matrix
(`native-direct` / `native-passthrough` / `native-pep`), the latency/overhead
analysis, and the PDP-outage lifecycle (container-shutdown and network-partition).
Raw per-request data and summaries are written to `results/`.

Individual experiments (harness, saturation, outage phases) and their options are
documented in `docs/experiment_reproduction.md`.

## Result files

Generated outputs are written to `results/*.csv`:

| File | Meaning |
|---|---|
| `raw_*.csv` | Raw per-request latency for each matched configuration/profile/concurrency (HTTP and `native-*` targets) |
| `summary_stats.csv` | mean ± 95% CI, p50/p95/p99, jitter, cache-hit ratio per configuration |
| `security_overhead.csv` | Isolated security overhead (pep − passthrough) with CI; `pep_target` distinguishes HTTP (`pep`) from native-wire (`native-pep`) |
| `saturation_*.csv` | Throughput, tail latency, PEP CPU/RSS, recovery per concurrency level |
| `cache_invalidation_tests.csv` | Context/risk cache-invalidation correctness |
| `token_security_tests.csv` | ES256 token-security correctness |
| `modbus_proxy_tests.csv` | Transparent native-wire PEP functional + non-bypassability results |
| `modbus_proxy_metrics.csv` | Per-request internals of the transparent Modbus PEP (token/cache/forward timing) |
| `*_functional_tests.csv` | Functional authorization results per protocol (HTTP gateways) |
| `pdp_outage_lifecycle.csv` | Outage lifecycle (shutdown/partition, fail-closed stats, recovery) |
| `pdp_recovery_cycles.csv` | Time to restored protected-write availability over repeated outage cycles |
| `pdp_capacity*.csv` | PDP (OPA) decision capacity under concurrency, without a PEP |

## Documentation

- `docs/experiment_reproduction.md` — full reproduction workflow and definitions.
- `docs/experiment_plan_{modbus,mqtt,opcua}.md` — per-protocol policy and endpoints.
- `docs/testbed_description.md` — scope, claims, and limitations.
- `docs/token_schema.md` — ES256 trust-token schema and key management.
- `MANIFEST.md` — full file manifest.

## Citation

Please cite this testbed using the metadata in `CITATION.cff`.

## License

MIT License. See `LICENSE`.
