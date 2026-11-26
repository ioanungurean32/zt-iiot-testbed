"""Generate the EC (P-256) key pair used for ES256 trust-token signing.

The private key is held only by the token issuer (the load generator / test
harness); each PEP receives only the public verification key. This separation
is what lets the prototype demonstrate that a compromised PEP cannot forge
tokens for other devices.

Run once before starting the testbed, e.g. inside the built image so no host
crypto dependency is required:

  docker compose run --rm loadgen python tools/generate_keys.py

Writes, in separate directories so that each container mounts only what it
needs:
  <KEYS_DIR>/zt_private.pem          issuer only (loadgen); never mounted into a PEP
  <KEYS_DIR>/public/zt_public.pem    the only key material mounted (read-only)
                                     into the PEPs
  <KEYS_DIR>/admin/admin_token       shared secret of the transparent Modbus
                                     proxy's test-only admin interface, mounted
                                     only into that proxy and loadgen (see
                                     src/admin_auth.py)

With ``--keep-key-pair`` the existing private key is kept: its public key is
re-derived into ``public/`` and only the admin secret is newly generated.

KEYS_DIR defaults to /keys (mounted to ./keys on the host).
"""

from __future__ import annotations

import argparse
import os
import secrets
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec


def write_admin_token(keys_dir: Path) -> None:
    path = keys_dir / "admin" / "admin_token"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(secrets.token_hex(32) + "\n", encoding="utf-8")
    print(f"Wrote {path} (proxy admin secret — mounted only into the proxy and loadgen)")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep-key-pair", action="store_true")
    args = ap.parse_args()

    keys_dir = Path(os.getenv("KEYS_DIR", "/keys"))
    keys_dir.mkdir(parents=True, exist_ok=True)
    priv_path = keys_dir / "zt_private.pem"
    pub_path = keys_dir / "public" / "zt_public.pem"
    pub_path.parent.mkdir(parents=True, exist_ok=True)

    if args.keep_key_pair:
        private = serialization.load_pem_private_key(priv_path.read_bytes(), password=None)
    else:
        private = ec.generate_private_key(ec.SECP256R1())
        priv_path.write_bytes(private.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ))
        print(f"Wrote {priv_path} (issuer private key — do NOT mount into PEPs)")

    pub_path.write_bytes(private.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ))
    print(f"Wrote {pub_path} (PEP public verification key)")

    write_admin_token(keys_dir)


if __name__ == "__main__":
    main()
