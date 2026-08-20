"""Trust-token (JWT) helpers for the ZT-IIoT prototype.

Tokens are signed with an asymmetric scheme (ES256, EC P-256) so that Policy
Enforcement Points hold only the public verification key. A compromised PEP
therefore cannot mint valid tokens for other devices, which is the property a
symmetric (HS256) scheme cannot provide because every verifier would also hold
the signing secret.

Key material:
  - The token issuer (in the prototype, the load generator / test harness)
    reads the EC private key from ZT_PRIVATE_KEY (inline PEM) or
    ZT_PRIVATE_KEY_FILE (path).
  - Each PEP reads only the public key from ZT_PUBLIC_KEY / ZT_PUBLIC_KEY_FILE.

Verification pins the algorithm to ES256 (preventing alg=none and
RS256/HS256-confusion attacks) and validates the issuer and audience claims.
Each token carries a unique jti for audit/correlation. Bearer tokens remain
replayable within their validity window by design; this residual risk is
bounded by a short TTL and, in a production deployment, by TLS/channel binding
or a DPoP-style per-request proof (see the manuscript's token security
discussion).
"""

from __future__ import annotations

import os
import time
import uuid
from typing import Any, Dict, Optional

import jwt

ISSUER = "zt-iiot-token-service"
AUDIENCE = "zt-iiot-pep"
ALGORITHM = "ES256"

# Lazily-generated ephemeral keypair, used only when no key material is
# configured (e.g. a single-process local smoke test). In the containerized
# testbed, real keys are mounted so that the private key never reaches a PEP.
_EPHEMERAL_PRIVATE_PEM: Optional[str] = None
_EPHEMERAL_PUBLIC_PEM: Optional[str] = None


class TokenError(Exception):
    """Raised when a JWT is missing, invalid, or expired."""


def _generate_ephemeral_keypair() -> None:
    global _EPHEMERAL_PRIVATE_PEM, _EPHEMERAL_PUBLIC_PEM
    if _EPHEMERAL_PRIVATE_PEM is not None:
        return
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    private = ec.generate_private_key(ec.SECP256R1())
    _EPHEMERAL_PRIVATE_PEM = private.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    _EPHEMERAL_PUBLIC_PEM = (
        private.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode()
    )


def _read_key(inline_env: str, file_env: str) -> Optional[str]:
    inline = os.getenv(inline_env)
    if inline:
        return inline
    path = os.getenv(file_env)
    if path and os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    return None


def get_private_key() -> str:
    key = _read_key("ZT_PRIVATE_KEY", "ZT_PRIVATE_KEY_FILE")
    if key:
        return key
    _generate_ephemeral_keypair()
    return _EPHEMERAL_PRIVATE_PEM  # type: ignore[return-value]


def get_public_key() -> str:
    key = _read_key("ZT_PUBLIC_KEY", "ZT_PUBLIC_KEY_FILE")
    if key:
        return key
    _generate_ephemeral_keypair()
    return _EPHEMERAL_PUBLIC_PEM  # type: ignore[return-value]


def mint_token(
    *,
    subject: str = "device_01",
    role: str = "maintenance_engineer",
    zone: str = "cell_A",
    attestation_state: str = "valid",
    risk_score: int = 20,
    ttl_seconds: int = 300,
    private_key: Optional[str] = None,
) -> str:
    now = int(time.time())
    payload: Dict[str, Any] = {
        "sub": subject,
        "device_id": subject,
        "role": role,
        "zone": zone,
        "attestation_state": attestation_state,
        "risk_score": risk_score,
        "iat": now,
        "exp": now + ttl_seconds,
        "iss": ISSUER,
        "aud": AUDIENCE,
        "jti": uuid.uuid4().hex,
    }
    return jwt.encode(payload, private_key or get_private_key(), algorithm=ALGORITHM)


def decode_token(token: str, *, public_key: Optional[str] = None) -> Dict[str, Any]:
    try:
        return jwt.decode(
            token,
            public_key or get_public_key(),
            algorithms=[ALGORITHM],  # algorithm pinning
            issuer=ISSUER,
            audience=AUDIENCE,
        )
    except jwt.ExpiredSignatureError as exc:
        raise TokenError("expired_token") from exc
    except jwt.InvalidTokenError as exc:
        raise TokenError("invalid_token") from exc


def decode_authorization_header(auth_header: Optional[str]) -> Dict[str, Any]:
    if not auth_header:
        raise TokenError("missing_authorization_header")
    parts = auth_header.split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise TokenError("malformed_authorization_header")
    return decode_token(parts[1].strip())
