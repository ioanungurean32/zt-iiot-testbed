# Trust Token Schema

The prototype uses JWTs for local token verification in controlled experiments. The default development secret is provided through the `ZT_SECRET` environment variable.

| Claim | Meaning |
|---|---|
| `sub` | Subject/device identifier. |
| `device_id` | Device identifier (mirrors `sub`). |
| `role` | Operational role, e.g., `maintenance_engineer`, `operator`, `sensor`. |
| `zone` | Logical industrial zone/cell. |
| `attestation_state` | Simulated attestation state: `valid`, `stale`, or `failed`. |
| `risk_score` | Integer risk score used by the policies. |
| `iat` | Issued-at time. |
| `exp` | Expiration time. |
| `iss` | Token issuer (`zt-iiot-token-service`). |

The testbed does not implement full hardware remote attestation. Attestation is represented as a token attribute to support controlled policy and failure-mode experiments.
