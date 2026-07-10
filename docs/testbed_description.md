# Testbed Description

## Author and affiliation

- **Ioan UNGUREAN**  
  Stefan cel Mare University of Suceava, Romania  
  Email: <ioan.ungurean@usm.ro>  
  Institution: <https://usv.ro/en/>

## Purpose

This testbed supports the experimental evaluation of a protocol-aware Zero Trust architecture for Industrial IoT. It provides testbed components for Modbus/TCP, MQTT, and OPC UA, together with scripts for repeating the reported measurements.

## What the testbed demonstrates

- Protocol-aware Policy Enforcement Points for three IIoT protocols.
- JWT-based local token verification.
- OPA/Rego-based authorization decisions.
- Local decision caching and cache hit/cache miss behavior.
- Baseline versus PEP-mediated latency measurements.
- Functional allow/deny tests for normal, high-risk, expired-token, and failed-attestation cases.
- PDP outage handling and fail-safe behavior for critical operations.

## What the testbed does not claim

- It is not a production-grade industrial gateway.
- It is not a transparent packet-level proxy.
- It is not certified for deployment in safety-critical OT environments.
- It does not provide a complete remote attestation implementation; attestation states are represented as token attributes for controlled experimentation.

## Recommended reporting language

Results should be described as measurements from a containerized Python prototype. The most important comparison is the difference between direct baseline latency and PEP-mediated latency for the same protocol in the same environment.
