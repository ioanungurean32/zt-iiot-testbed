# Changelog

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
