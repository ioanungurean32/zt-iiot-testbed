"""Trust-token security tests for the ZT-IIoT PEP.

These tests submit crafted tokens to the Modbus PEP and verify that only a
correctly signed, well-formed ES256 token is accepted. In particular, a token
signed with a different private key (the "compromised PEP forges a token"
scenario) is rejected, because each PEP holds only the public verification key.

Cases:
  1. valid ES256 token              -> 200 (accepted)
  2. token signed with another key  -> 401 (forgery rejected)
  3. token with wrong audience      -> 401 (aud validation)
  4. token with wrong issuer        -> 401 (iss validation)
  5. token signed with HS256        -> 401 (algorithm pinning)
"""

from __future__ import annotations

import csv
import os
import time
from pathlib import Path
from typing import Any, Dict

import jwt
import requests

from src.auth import ALGORITHM, AUDIENCE, ISSUER, get_private_key, mint_token
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

PEP_URL = os.getenv("PEP_URL", "http://localhost:8080")
RESULTS_DIR = Path(os.getenv("RESULTS_DIR", "/app/results"))


def _other_private_key_pem() -> str:
    other = ec.generate_private_key(ec.SECP256R1())
    return other.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()


def _custom_token(*, aud: str, iss: str, alg: str, key: str) -> str:
    now = int(time.time())
    payload = {
        "sub": "device_x", "device_id": "device_x", "role": "operator",
        "zone": "cell_A", "attestation_state": "valid", "risk_score": 20,
        "iat": now, "exp": now + 300, "iss": iss, "aud": aud, "jti": "test",
    }
    return jwt.encode(payload, key, algorithm=alg)


def call_status(token: str) -> int:
    r = requests.post(
        f"{PEP_URL}/modbus/read",
        json={"register_address": 40001, "count": 1},
        headers={"Authorization": f"Bearer {token}"},
        timeout=5,
    )
    return r.status_code


def main() -> None:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out = RESULTS_DIR / "token_security_tests.csv"

    valid = mint_token(subject="device_ok", role="operator", risk_score=20, ttl_seconds=300)
    forged = _custom_token(aud=AUDIENCE, iss=ISSUER, alg=ALGORITHM, key=_other_private_key_pem())
    wrong_aud = _custom_token(aud="wrong-audience", iss=ISSUER, alg=ALGORITHM, key=get_private_key())
    wrong_iss = _custom_token(aud=AUDIENCE, iss="rogue-issuer", alg=ALGORITHM, key=get_private_key())
    hs256 = _custom_token(aud=AUDIENCE, iss=ISSUER, alg="HS256", key="attacker-shared-secret")

    cases = [
        ("valid_es256_token", valid, 200),
        ("forged_wrong_key", forged, 401),
        ("wrong_audience", wrong_aud, 401),
        ("wrong_issuer", wrong_iss, 401),
        ("hs256_algorithm_confusion", hs256, 401),
    ]

    rows = []
    for name, token, expected in cases:
        observed = call_status(token)
        result = "PASS" if observed == expected else "FAIL"
        row = {"test": name, "expected_status": expected, "observed_status": observed, "result": result}
        rows.append(row)
        print(row)

    with out.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    passed = sum(1 for r in rows if r["result"] == "PASS")
    print(f"\nWrote {out}")
    print(f"PASS={passed}/{len(rows)}")


if __name__ == "__main__":
    main()
