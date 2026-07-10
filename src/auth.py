"""JWT helper functions for the ZT-IIoT Modbus prototype."""

from __future__ import annotations

import os
import time
from typing import Any, Dict, Optional

import jwt


class TokenError(Exception):
    """Raised when a JWT is missing, invalid, or expired."""


def get_secret() -> str:
    return os.getenv("ZT_SECRET", "dev-secret-change-me")


def mint_token(
    *,
    subject: str = "device_01",
    role: str = "maintenance_engineer",
    zone: str = "cell_A",
    attestation_state: str = "valid",
    risk_score: int = 20,
    ttl_seconds: int = 300,
    secret: Optional[str] = None,
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
        "iss": "zt-iiot-token-service",
    }
    return jwt.encode(payload, secret or get_secret(), algorithm="HS256")


def decode_token(token: str, *, secret: Optional[str] = None) -> Dict[str, Any]:
    try:
        return jwt.decode(token, secret or get_secret(), algorithms=["HS256"])
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
