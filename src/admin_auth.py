"""Shared-secret credential for the test-only administration interface of the
transparent Modbus proxy PEP.

The proxy's administration endpoints (identity binding, operational context,
cache control) exist only so that the experiment orchestrator can provision a
binary protocol that has no identity or context fields. They are not a Zero
Trust management plane. Access is restricted in two ways:

  * the administration port is not published to the host; it is reachable only
    on the Compose network shared by the proxy and the orchestrator (loadgen);
  * every state-changing request must carry ``Authorization: Bearer <secret>``,
    where the secret is generated locally by ``tools/generate_keys.py`` into the
    (git-ignored) keys directory and mounted only into the proxy and loadgen.

The secret is read from ``PROXY_ADMIN_TOKEN`` (inline) or
``PROXY_ADMIN_TOKEN_FILE`` (path) on every call, so it can be generated after the
proxy has started. If neither is configured, administration is disabled
(fail-closed).
"""

from __future__ import annotations

import hmac
import os
from typing import Dict, Optional


def load_admin_token() -> Optional[str]:
    inline = os.getenv("PROXY_ADMIN_TOKEN")
    if inline:
        return inline.strip()
    path = os.getenv("PROXY_ADMIN_TOKEN_FILE")
    if path and os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            value = f.read().strip()
        return value or None
    return None


def check_bearer(authorization: Optional[str]) -> str:
    """Return "ok", "admin_disabled" (no secret configured) or "unauthorized"."""
    expected = load_admin_token()
    if not expected:
        return "admin_disabled"
    prefix = "Bearer "
    if not authorization or not authorization.startswith(prefix):
        return "unauthorized"
    presented = authorization[len(prefix):].strip()
    if hmac.compare_digest(presented.encode(), expected.encode()):
        return "ok"
    return "unauthorized"


def admin_headers() -> Dict[str, str]:
    """Headers the orchestrator sends with every administration request."""
    token = load_admin_token()
    if not token:
        raise RuntimeError("PROXY_ADMIN_TOKEN(_FILE) not configured; run tools/generate_keys.py")
    return {"Authorization": f"Bearer {token}"}
