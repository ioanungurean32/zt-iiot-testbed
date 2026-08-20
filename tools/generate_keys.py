"""Generate the EC (P-256) key pair used for ES256 trust-token signing.

The private key is held only by the token issuer (the load generator / test
harness); each PEP receives only the public verification key. This separation
is what lets the prototype demonstrate that a compromised PEP cannot forge
tokens for other devices.

Run once before starting the testbed, e.g. inside the built image so no host
crypto dependency is required:

  docker compose run --rm loadgen python tools/generate_keys.py

Writes:
  <KEYS_DIR>/zt_private.pem   (issuer only; keep out of PEP containers)
  <KEYS_DIR>/zt_public.pem    (mounted read-only into every PEP)

KEYS_DIR defaults to /keys (mounted to ./keys on the host).
"""

from __future__ import annotations

import os
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec


def main() -> None:
    keys_dir = Path(os.getenv("KEYS_DIR", "/keys"))
    keys_dir.mkdir(parents=True, exist_ok=True)

    private = ec.generate_private_key(ec.SECP256R1())

    private_pem = private.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_pem = private.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )

    priv_path = keys_dir / "zt_private.pem"
    pub_path = keys_dir / "zt_public.pem"
    priv_path.write_bytes(private_pem)
    pub_path.write_bytes(public_pem)

    print(f"Wrote {priv_path} (issuer private key — do NOT mount into PEPs)")
    print(f"Wrote {pub_path} (PEP public verification key)")


if __name__ == "__main__":
    main()
