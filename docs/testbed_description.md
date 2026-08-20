# Testbed Description

## Author and affiliation

- **Ioan UNGUREAN**  
  Stefan cel Mare University of Suceava, Romania  
  Email: <ioan.ungurean@usm.ro>  
  Institution: <https://usv.ro/en/>

## Purpose

This testbed supports the experimental evaluation of a Zero Trust architecture
for Industrial IoT. It provides application-level policy-enforcement gateways for
Modbus/TCP, MQTT, and OPC UA, a **transparent native-wire protocol-aware PEP for
Modbus/TCP** (v2.1.0), matched baselines that isolate the security processing
cost, and scripts that regenerate every reported measurement over independent
repeated trials.

## What the testbed demonstrates

- Application-level request-mediation PEPs for three IIoT protocols.
- A **transparent native-wire Modbus/TCP PEP** (v2.1.0): an in-line TCP proxy for
  unmodified pymodbus clients that parses the Modbus function code and register
  address from the wire, derives the action from the parsed operation (not a
  client-supplied field), enforces the same ABAC/OPA policy/cache/fail-safe, and
  answers a denied request with a native Modbus exception. Includes on-the-wire
  functional tests and a **non-bypassability** check: the protected device is on
  an internal network reachable only through the proxy, and a direct
  client-to-device connection is refused. A native pass-through proxy baseline is
  included so the native-wire security overhead is isolated the same way as the
  HTTP path.
- Asymmetric (ES256) trust-token verification: PEPs hold only the public key;
  algorithm pinning and issuer/audience validation; token-security tests.
- OPA/Rego attribute-based authorization with operational context and a graduated
  risk response.
- A local decision cache whose key binds operational context, risk bucket, and
  policy version, with a structural non-cacheable flag for context-dependent
  decisions; cache-invalidation tests.
- **Matched-baseline latency** (direct native / enforcement-disabled gateway /
  full PEP) that isolates the incremental Zero-Trust security overhead, reported
  over 10 trials with confidence intervals and raw per-request data.
- **Workload diversity** (controlled cache-hit ratios, mixed read/write) and a
  **concurrency/saturation sweep** with throughput, tail latency, and PEP CPU and
  memory.
- Functional allow/deny tests (normal, high-risk, critical-risk, expired-token,
  failed-attestation) across all three protocols.
- **PDP-unavailability lifecycle** (container shutdown and network partition):
  cached observation continues, control operations fail closed with repetition
  statistics, concurrent-outage requests do not fail open or exhaust threads,
  cache expiry is not served stale, and recovery is characterized.

## What the testbed does not claim

- It is not a production-grade industrial gateway.
- The **MQTT and OPC UA PEPs (and the HTTP Modbus gateway) are application-level**:
  attributes are supplied through the gateway API rather than parsed from the
  wire, and non-bypassability is assumed. The transparent native-wire proxy is
  provided for **Modbus/TCP only**; MQTT and OPC UA transparent proxies, and
  identity binding stronger than the provisioned per-session token (e.g. a client
  certificate over Modbus/TCP Security), remain future work.
- It is not certified for deployment in safety-critical OT environments; degraded
  modes and any emergency-override path are deployment responsibilities, not
  implemented or validated here.
- It does not implement hardware remote attestation, a numeric attestation score,
  adaptive-TTL computation, a dynamic SIEM Risk Engine, mTLS/X.509 device
  authentication, token revocation/rotation, or redundant control-plane
  components. Attestation and risk are simulated token attributes.

## Recommended reporting language

Report results as prototype-level measurements from a containerized Python
gateway. Isolate the Zero-Trust cost as the difference between the
enforcement-disabled gateway (passthrough) and the full PEP for the same protocol
in the same environment; the passthrough-minus-direct difference is HTTP/gateway
cost, not Zero Trust. Report percentiles per distribution (never subtract them),
and report jitter as the standard deviation of per-request latency.
