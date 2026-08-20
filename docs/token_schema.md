# Trust Token Schema

Trust tokens are JSON Web Tokens signed with **ES256 (EC P-256, asymmetric)**.
The token issuer (the load generator / test harness) holds the private key; each
PEP holds only the public verification key, so a compromised PEP cannot forge
tokens for other devices. Generate the key pair once with
`docker compose run --rm loadgen python tools/generate_keys.py` (see
`docs/experiment_reproduction.md`). Keys are provided via `ZT_PRIVATE_KEY_FILE`
(issuer only) and `ZT_PUBLIC_KEY_FILE` (PEPs).

Verification pins the algorithm to ES256 (rejecting `alg=none` and RS256/HS256
confusion) and validates the `iss` and `aud` claims.

| Claim | Meaning |
|---|---|
| `sub` | Subject/device identifier. |
| `device_id` | Device identifier (mirrors `sub`). |
| `role` | Operational role, e.g., `maintenance_engineer`, `operator`, `sensor`. |
| `zone` | Logical industrial zone/cell. |
| `attestation_state` | Simulated categorical attestation state: `valid`, `stale`, or `failed`. |
| `risk_score` | Integer risk score used by the policies (carried in the token; the prototype does not recompute it per request). |
| `iat` | Issued-at time. |
| `exp` | Expiration time. |
| `iss` | Token issuer (`zt-iiot-token-service`); validated on verification. |
| `aud` | Intended audience (`zt-iiot-pep`); validated on verification. |
| `jti` | Unique per-token identifier (audit/correlation). |

Notes and known limitations:

- The prototype does **not** implement hardware remote attestation (TPM Quote,
  PCR verification, measured boot). `attestation_state` is a simulated
  categorical attribute; the numeric attestation score and adaptive-TTL formula
  of the architecture are specifications not computed by the prototype.
- Bearer tokens remain replayable within their validity window by design; the
  residual risk is bounded by a short TTL and, in production, would require
  TLS/channel binding or a DPoP-style per-request proof. Revocation and key
  rotation are not implemented.
- Token expiry is checked before the cache lookup: a request with an expired
  token is rejected before any cached decision is consulted.
