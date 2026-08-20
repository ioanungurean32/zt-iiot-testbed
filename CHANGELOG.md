# Changelog

## v2.1.0

Adds a genuinely transparent, native-wire protocol-aware PEP for Modbus/TCP: an
in-line proxy that parses the Modbus function code from the wire, enforces policy
on unmodified clients, and demonstrates that direct client-to-device access
cannot bypass enforcement. The application-level HTTP gateways of v2.0.0 are
retained unchanged; the transparent proxy is added alongside them.

**Transparent native-wire Modbus/TCP PEP (new)**
- `src/modbus_proxy_pep.py`: an in-line transparent TCP proxy that unmodified
  pymodbus clients connect to as if it were the PLC. It parses the Modbus
  function code and register address directly from the wire (MBAP header + PDU),
  **derives the action from the parsed function code rather than a client-supplied
  field** (closing the attribute-to-operation binding gap), evaluates the same
  ABAC/OPA policy with the same decision cache and fail-safe behaviour as the
  HTTP PEP, forwards only allowed frames to the upstream device, and answers a
  denied/degraded request with a native Modbus exception response. Supports
  `PASSTHROUGH_MODE=true` for a native pass-through proxy baseline. Trust tokens
  remain asymmetric ES256 (the proxy holds only the public key); identity and
  operational context are provisioned out-of-band via an admin side channel
  (`/admin/token`, `/admin/context`, `/cache/clear`, `/health`, `/resource`),
  never trusted from the Modbus wire.

**Non-bypassability (demonstrated, not assumed)**
- New `modbus-plc` service on an internal-only Docker network (`plc_net`),
  reachable exclusively through the transparent proxy. `loadgen/modbus_proxy_tests.py`
  includes a bypass check confirming a direct client-to-device connection is
  refused.

**Evaluation additions**
- `loadgen/modbus_native_harness.py`: matched native-wire latency for
  `native-direct` / `native-passthrough` / `native-pep` (trials, warm-up,
  steady/cold profiles), written as `raw_modbus_native-*_*.csv` in the harness
  schema. `loadgen/analyze.py` now also pairs `native-pep` with
  `native-passthrough` to report the isolated native-wire security overhead
  (`security_overhead.csv` gains `pep_target`/`baseline_target` columns).
- `loadgen/modbus_proxy_tests.py`: functional policy enforcement on the wire
  (authorized read, maintenance write, out-of-window deny, high-risk degrade,
  critical-risk quarantine, failed attestation, expired token) plus the
  non-bypassability check → `results/modbus_proxy_tests.csv`. The proxy also
  writes per-request internals to `results/modbus_proxy_metrics.csv`.
- New Compose services `modbus-plc`, `modbus-proxy-pep` (port 5030, admin 8083),
  and `modbus-proxy-gw` (port 5031, `PASSTHROUGH_MODE`); `scripts/run_full_evaluation.ps1`
  runs the transparent-PEP functional/bypass tests and the native latency matrix.

Re-run `scripts/run_full_evaluation.ps1` to regenerate all results (the run
clears `results/` first); the transparent-PEP data is produced only by this
version.

## v2.0.0

Major rework of the implementation and evaluation. Breaking changes to token
signing, service topology, and the experiment workflow.

**Security / tokens**
- Switched trust tokens from symmetric HS256 to **asymmetric ES256 (EC P-256)**:
  the issuer holds the private key and each PEP holds only the public
  verification key, so a compromised PEP cannot forge tokens. Added algorithm
  pinning, issuer/audience validation, and a per-token `jti`. Added
  `tools/generate_keys.py` and `loadgen/token_security_tests.py`. `ZT_SECRET` is
  removed; keys are provided via `ZT_PRIVATE_KEY_FILE` / `ZT_PUBLIC_KEY_FILE`.
  Requires the `cryptography` package.

**Cache correctness**
- The decision cache key now binds the operational-context (environment)
  attributes and the policy version; environment-dependent policy decisions carry
  a `context_dependent` flag and are structurally non-cacheable regardless of TTL
  (mechanism-enforced, not a policy convention). Cache operations are
  lock-serialized. Added `loadgen/cache_invalidation_tests.py`.

**Evaluation harness (new)**
- `loadgen/harness.py`: matched targets (direct / passthrough / pep), independent
  trials, warm-up exclusion, concurrency, and workload profiles
  (steady/cold/mixed/hit-ratio); writes raw per-request data.
- `loadgen/analyze.py`: mean ± 95% CI, per-distribution percentiles, jitter, and
  the isolated security overhead (pep − passthrough).
- `loadgen/saturation_sweep.py`: concurrency sweep with throughput, tail latency,
  PEP CPU/RSS (via a new `/resource` endpoint), and recovery.
- `loadgen/pdp_outage_lifecycle.py`: full outage lifecycle (shutdown and network
  partition; cached-read, fail-closed statistics, concurrent, expiry, recovery).
- Added pass-through baseline services `modbus-gw`/`mqtt-gw`/`opcua-gw`
  (`PASSTHROUGH_MODE=true`) and `src/resource_stats.py`.

**Scripts / docs**
- Consolidated `scripts/` to a single orchestrator, `run_full_evaluation.ps1`
  (removed `run_all_experiments.ps1`).
- Updated all documentation for the new workflow; relabeled the enforcement
  mechanism as application-level request mediation.

## v1.4.3

- Added a critical-risk (`risk_score > 75`) functional test case to `loadgen/pep_tests.py`, `loadgen/mqtt_tests.py`, and `loadgen/opcua_tests.py`, exercising the `critical_risk_quarantine` policy rule (implemented in `policies/modbus.rego`, shared across all three protocols) that was previously never validated by any test.
- Added a high-risk `call_method` test case to `loadgen/opcua_tests.py`, exercising the `opcua_high_risk_method_blocked_read_only` rule in `policies/opcua.rego`, which was previously untested (only the equivalent `write` rule was covered).
- `loadgen/summarize_results.py` now writes its aggregated mean/p50/p95/p99/jitter output to `results/summary.csv` (via `--out`, default `$RESULTS_DIR/summary.csv`) in addition to printing it, so these headline metrics are persisted instead of only appearing in console output.

## v1.4.2

- Fixed PDP-outage fail-closed latency: `requests`' `timeout=(connect, read)` does not bound DNS resolution (`socket.getaddrinfo`), which can block for several seconds when the `opa` Docker Compose service is stopped. `opa_decision()` in `src/modbus_pep.py`, `src/mqtt_pep.py`, and `src/opcua_pep.py` now runs the OPA call in a worker thread with an explicit wall-clock deadline (`OPA_CONNECT_TIMEOUT + OPA_READ_TIMEOUT`), so the PEP responds within the configured budget regardless of where the call hangs. Measured fail-closed latency dropped from ~4-8 s to ~400-420 ms.
- Updated `src/modbus_pep.py` to reuse a persistent `ModbusTcpClient` connection instead of reconnecting for every `/modbus/read` and `/modbus/write` request, mirroring the MQTT PEP's persistent-client approach (v0.1.3) and making Modbus/TCP PEP latency measurements comparable to the direct baseline, which also reuses a single connection.

## v1.4.1

- Updated `scripts/run_all_experiments.ps1` to explicitly rebuild the `loadgen` image.
- This fixes cases where Docker reused an older loadgen image that did not contain `loadgen/prime_outage_cache.py`.
- No changes to the experimental methodology or reported metrics.

## v0.1.4

- Added `loadgen/prime_outage_cache.py` to explicitly prime protocol-specific cached read/telemetry decisions before PDP outage tests.
- Updated Modbus, MQTT, and OPC UA outage tests to use cache keys that match the priming step.
- Fixed the OPC UA outage cached-read scenario by using the same operator identity and NodeId for priming and outage validation.
- Added `decision_source` to PEP responses and `pep_metrics.csv` to distinguish cached decisions from PDP decisions.
- Reduced default PDP client timeout values in Docker Compose to `OPA_CONNECT_TIMEOUT=0.10` and `OPA_READ_TIMEOUT=0.30` for faster fail-closed responses.

## v0.1.3

- Updated the MQTT PEP to use a persistent MQTT client and network loop instead of reconnecting for every publish request.
- Increased the MQTT benchmark token TTL to avoid token expiration during long runs.
- This patch makes MQTT latency measurements comparable with Modbus/TCP and OPC UA PEP benchmarks.

## v0.1.2

- Renamed the main package title to "ZT-IIoT Multi-Protocol Testbed" in user-facing metadata.
- Simplified metadata wording so the package is presented as an experimental testbed.

## v0.1.1

- Initial supplementary testbed.
- Includes Modbus/TCP, MQTT, and OPC UA protocol vertical slices.
- Includes protocol-aware PEPs, OPA/Rego policies, token generation, cache behavior, latency benchmarks, functional authorization tests, and PDP outage tests.
- Docker Compose testbed prepared for Git hosting and journal supplementary material.

## v0.1.0 - PDP outage robustness patch

- Reduced OPA/PDP client timeouts inside PEP services using configurable `OPA_CONNECT_TIMEOUT` and `OPA_READ_TIMEOUT` values.
- Increased outage-test client timeout to avoid premature load-generator failure before PEP fail-safe decision is returned.
- Made outage scripts robust to timeout/connection exceptions so full experiment runs continue and record diagnostic rows.
