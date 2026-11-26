"""Transparent native-wire Modbus/TCP Policy Enforcement Point (PEP).

Unlike the application-level HTTP gateway (``modbus_pep.py``), this PEP is an
**in-line transparent TCP proxy**: unmodified Modbus/TCP clients connect to it as
if it were the PLC, and it parses the actual Modbus function code and register
address from the wire (MBAP header + PDU). The requested action is *derived from
the parsed function code*, not from a client-supplied field, then evaluated
against the ABAC/OPA policy (with the same local decision cache and fail-safe
behaviour as the HTTP PEP), and only an allowed frame is forwarded to the
upstream Modbus server. A denied request is answered with a native Modbus
exception response; the client never reaches the device.

This closes the attribute-to-operation binding gap of the HTTP gateway:
because the operation is read from the wire, an
authenticated client cannot present a benign action label while inducing a
different native operation -- the enforced action *is* the operation on the wire.

Non-bypassability is demonstrated at the deployment level: the protected Modbus
server (``modbus-plc``) is placed on an internal Docker network reachable only by
this proxy, so a client that tries to reach the device directly is refused
(``modbus_proxy_tests.py`` includes the bypass check).

Device identity: Modbus/TCP carries no authentication field, so the proxy keeps
a table of identity bindings, each mapping a client network address (the source
IP of the TCP connection) to an ES256 trust token supplied out-of-band via the
admin endpoint ``/admin/token`` (the issuer, which holds the private key, pushes
the token; the proxy holds only the public verification key, so it verifies but
never mints). Every frame is evaluated with the token bound to the address of
the connection that carried it; a connection from an address with no binding is
denied, and there is no process-wide fallback identity. The binding is looked up
per frame, so removing it takes effect on connections that are already open.
The binding is address-based, not an authentication of the TCP client: in
production it would be a client certificate over Modbus/TCP Security (TLS) or
an equivalent channel. Operational-context (environment) attributes describe
the zone the proxy protects (one proxy instance per protected device) and are
shared by all connections; they are supplied by the deployment via
``/admin/context`` -- not by the untrusted client -- which is stricter than the
HTTP gateway, where the client supplied ``maintenance_window`` itself.

Administration: the admin endpoints are a test-only provisioning interface for
the experiment orchestrator, not a Zero Trust management plane. The admin port
is not published to the host, and every state-changing request must present the
shared secret described in ``src/admin_auth.py``; requests without it are
rejected (HTTP 401). Every state-changing admin request, accepted or rejected,
is appended to an audit log.

PASSTHROUGH_MODE: running the same image with ``PASSTHROUGH_MODE=true`` yields a
native pass-through proxy baseline (identical parse/forward path, no token
verification, cache, or OPA), so the ``native-pep`` minus ``native-passthrough``
difference isolates the native-wire security-processing cost (an additional
native pass-through baseline).
"""

from __future__ import annotations

import concurrent.futures
import csv
import ipaddress
import json
import os
import socket
import socketserver
import struct
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import requests

from src.admin_auth import check_bearer
from src.auth import TokenError, decode_token
from src.resource_stats import resource_stats

# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #
LISTEN_HOST = os.getenv("PROXY_LISTEN_HOST", "0.0.0.0")
LISTEN_PORT = int(os.getenv("PROXY_LISTEN_PORT", "5030"))
ADMIN_PORT = int(os.getenv("PROXY_ADMIN_PORT", "8083"))
UPSTREAM_HOST = os.getenv("MODBUS_UPSTREAM_HOST", "modbus-plc")
UPSTREAM_PORT = int(os.getenv("MODBUS_UPSTREAM_PORT", "5020"))
OPA_URL = os.getenv("OPA_URL", "http://opa:8181/v1/data/zt_iiot/authz/decision")
OPA_CONNECT_TIMEOUT = float(os.getenv("OPA_CONNECT_TIMEOUT", "0.10"))
OPA_READ_TIMEOUT = float(os.getenv("OPA_READ_TIMEOUT", "0.30"))
POLICY_VERSION = os.getenv("POLICY_VERSION", "v0.1-modbus")
CACHE_WRITES = os.getenv("CACHE_WRITES", "false").lower() == "true"
PASSTHROUGH_MODE = os.getenv("PASSTHROUGH_MODE", "false").lower() == "true"
RESULTS_DIR = Path(os.getenv("RESULTS_DIR", "/app/results"))

# Modbus/TCP framing and function-code classes.
_MBAP_HEADER_LEN = 6  # transaction(2) + protocol(2) + length(2); unit id follows
READ_FCS = {1, 2, 3, 4}       # read coils / discrete inputs / holding / input regs
WRITE_FCS = {5, 6, 15, 16}    # write single/multiple coils / registers
HOLDING_FCS = {3, 6, 16}      # function codes that address holding registers
EXC_ILLEGAL_FUNCTION = 0x01   # policy deny / token failure on the wire
EXC_GATEWAY_NO_RESPONSE = 0x0B  # fail-closed on PDP unavailability / upstream loss

# --------------------------------------------------------------------------- #
# Mutable runtime state (identity bindings + operational context), settable via
# the admin endpoints. Modbus has no auth or context fields on the wire, so both
# are provisioned out-of-band rather than trusted from the client. Bindings map a
# client address to its token; there is deliberately no process-wide token.
# --------------------------------------------------------------------------- #
_STATE_LOCK = threading.Lock()
_BINDINGS: Dict[str, str] = {}
_ENV_CONTEXT: Dict[str, Any] = {
    "maintenance_window": os.getenv("MAINTENANCE_WINDOW", "false").lower() == "true",
    "shift": os.getenv("SHIFT", "day"),
}
# When disabled, every request is evaluated by the PDP (no cache reuse). This is
# used to measure the cold/all-miss latency path on the native wire, where a
# single provisioned identity and a bounded register space would otherwise turn
# repeated reads into cache hits.
_CACHE_ENABLED: bool = os.getenv("CACHE_ENABLED", "true").lower() == "true"


def get_bound_token(client_addr: str) -> Optional[str]:
    with _STATE_LOCK:
        return _BINDINGS.get(client_addr)


def set_binding(client_addr: str, token: Optional[str]) -> None:
    """Bind a token to a client address; a null token removes the binding."""
    with _STATE_LOCK:
        if token:
            _BINDINGS[client_addr] = token
        else:
            _BINDINGS.pop(client_addr, None)


def binding_count() -> int:
    with _STATE_LOCK:
        return len(_BINDINGS)


def cache_enabled() -> bool:
    with _STATE_LOCK:
        return _CACHE_ENABLED


def set_cache_enabled(enabled: bool) -> None:
    global _CACHE_ENABLED
    with _STATE_LOCK:
        _CACHE_ENABLED = bool(enabled)


def get_env_context() -> Dict[str, Any]:
    with _STATE_LOCK:
        return dict(_ENV_CONTEXT)


def set_env_context(update: Dict[str, Any]) -> None:
    with _STATE_LOCK:
        if "maintenance_window" in update:
            _ENV_CONTEXT["maintenance_window"] = bool(update["maintenance_window"])
        if "shift" in update:
            _ENV_CONTEXT["shift"] = str(update["shift"])


# --------------------------------------------------------------------------- #
# PDP client + decision cache (mirrors modbus_pep.py so the two enforcement
# points share identical policy, cache-key, and fail-safe semantics).
# --------------------------------------------------------------------------- #
DECISION_CACHE: Dict[str, Tuple[float, Dict[str, Any]]] = {}
CACHE_LOCK = threading.Lock()
_OPA_EXECUTOR = concurrent.futures.ThreadPoolExecutor(max_workers=8, thread_name_prefix="opa-call")


def now_ms() -> float:
    return time.perf_counter() * 1000.0


def env_context(environment: Dict[str, Any]) -> str:
    return "|".join(f"{k}={environment[k]}" for k in sorted(environment or {}))


def cache_key(subject: Dict[str, Any], resource: Dict[str, Any], action: str, environment: Dict[str, Any]) -> str:
    risk_bucket = int(subject.get("risk_score", 100)) // 25
    return ":".join(
        [
            str(subject.get("device_id")),
            str(subject.get("role")),
            str(subject.get("attestation_state")),
            str(risk_bucket),
            action,
            str(resource.get("function_code")),
            str(resource.get("register_address")),
            env_context(environment),
            POLICY_VERSION,
        ]
    )


def get_cached_decision(key: str) -> Optional[Dict[str, Any]]:
    with CACHE_LOCK:
        entry = DECISION_CACHE.get(key)
        if not entry:
            return None
        expires_at, decision = entry
        if time.time() >= expires_at:
            DECISION_CACHE.pop(key, None)
            return None
        return decision


def store_cached_decision(key: str, decision: Dict[str, Any], action: str) -> None:
    if action == "write" and not CACHE_WRITES:
        return
    if decision.get("context_dependent"):
        return
    ttl = int(decision.get("ttl", 0) or 0)
    if ttl <= 0:
        return
    with CACHE_LOCK:
        DECISION_CACHE[key] = (time.time() + ttl, decision)


def _post_to_opa(opa_input: Dict[str, Any]) -> Dict[str, Any]:
    response = requests.post(OPA_URL, json={"input": opa_input}, timeout=(OPA_CONNECT_TIMEOUT, OPA_READ_TIMEOUT))
    response.raise_for_status()
    return response.json()


def opa_decision(opa_input: Dict[str, Any]) -> Tuple[Dict[str, Any], bool, float]:
    start = now_ms()
    deadline = OPA_CONNECT_TIMEOUT + OPA_READ_TIMEOUT
    future = _OPA_EXECUTOR.submit(_post_to_opa, opa_input)
    try:
        data = future.result(timeout=deadline)
        return data.get("result", {"allow": False, "mode": "deny", "ttl": 0, "reason": "missing_opa_result"}), True, now_ms() - start
    except (requests.RequestException, concurrent.futures.TimeoutError, OSError):
        return {"allow": False, "mode": "deny", "ttl": 0, "reason": "pdp_unavailable_fail_safe"}, False, now_ms() - start


def build_opa_input(claims: Dict[str, Any], resource: Dict[str, Any], action: str, environment: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "protocol": "modbus",
        "action": action,
        "subject": {
            "device_id": claims.get("device_id") or claims.get("sub"),
            "role": claims.get("role"),
            "zone": claims.get("zone"),
            "attestation_state": claims.get("attestation_state"),
            "risk_score": int(claims.get("risk_score", 100)),
        },
        "resource": resource,
        "environment": environment,
        "policy_version": POLICY_VERSION,
    }


def evaluate_request(opa_input: Dict[str, Any]) -> Tuple[Dict[str, Any], str, Any, float, str]:
    key = cache_key(opa_input["subject"], opa_input["resource"], opa_input["action"], opa_input.get("environment", {}))
    cache_lookup_ms = 0.0
    caching = cache_enabled()
    if caching:
        t_cache = now_ms()
        cached = get_cached_decision(key)
        cache_lookup_ms = now_ms() - t_cache
        if cached is not None:
            return cached, "hit", "not_queried", cache_lookup_ms, "cache"
    decision, pdp_available, pdp_decision_ms = opa_decision(opa_input)
    if caching:
        store_cached_decision(key, decision, opa_input["action"])
    decision["_pdp_decision_ms"] = pdp_decision_ms
    return decision, "miss", pdp_available, cache_lookup_ms, "pdp"


# --------------------------------------------------------------------------- #
# Modbus/TCP wire helpers
# --------------------------------------------------------------------------- #
def recv_exact(sock: socket.socket, n: int) -> Optional[bytes]:
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            return None
        buf += chunk
    return buf


def read_frame(sock: socket.socket) -> Optional[bytes]:
    """Read one complete Modbus/TCP ADU: 6-byte MBAP prefix then `length` more
    bytes (unit id + PDU), using the MBAP length field to frame the message."""
    header = recv_exact(sock, _MBAP_HEADER_LEN)
    if header is None:
        return None
    length = struct.unpack(">H", header[4:6])[0]
    if length < 1 or length > 260:  # MBAP length is bounded; guard against junk
        return None
    rest = recv_exact(sock, length)
    if rest is None:
        return None
    return header + rest


def fc_action(fc: int) -> str:
    if fc in READ_FCS:
        return "read"
    if fc in WRITE_FCS:
        return "write"
    return "unknown"


def parse_request(frame: bytes) -> Tuple[int, int, Optional[int]]:
    """Return (unit_id, function_code, address_offset_or_None) from the wire."""
    unit = frame[6]
    pdu = frame[7:]
    fc = pdu[0] if pdu else 0
    addr = struct.unpack(">H", pdu[1:3])[0] if len(pdu) >= 3 else None
    return unit, fc, addr


def exception_response(frame: bytes, exc_code: int) -> bytes:
    """Build a native Modbus exception response mirroring the request's
    transaction id and unit id, with function code | 0x80 and the exception code."""
    txn = frame[0:2]
    unit = frame[6:7]
    fc = frame[7] if len(frame) > 7 else 0
    pdu = bytes([(fc | 0x80) & 0xFF, exc_code & 0xFF])
    length = struct.pack(">H", len(unit) + len(pdu))
    return txn + b"\x00\x00" + length + unit + pdu


# --------------------------------------------------------------------------- #
# Per-request metrics (own file, kept separate from the HTTP PEP's log)
# --------------------------------------------------------------------------- #
_METRIC_FIELDS = [
    "timestamp_unix", "request_id", "pep_type", "action", "function_code",
    "register_address", "decision", "reason", "cache_status", "decision_source",
    "pdp_available", "token_status", "token_verification_ms", "cache_lookup_ms",
    "pdp_decision_ms", "modbus_forward_ms", "end_to_end_latency_ms", "risk_score",
    "attestation_state", "policy_version",
]
_METRIC_LOCK = threading.Lock()


def _metrics_path() -> Path:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    return RESULTS_DIR / "modbus_proxy_metrics.csv"


def log_proxy_metric(row: Dict[str, Any]) -> None:
    path = _metrics_path()
    record = {f: row.get(f, "") for f in _METRIC_FIELDS}
    record["timestamp_unix"] = record.get("timestamp_unix") or time.time()
    with _METRIC_LOCK:
        needs_header = not path.exists() or path.stat().st_size == 0
        with path.open("a", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=_METRIC_FIELDS)
            if needs_header:
                writer.writeheader()
            writer.writerow(record)


# --------------------------------------------------------------------------- #
# Connection handling
# --------------------------------------------------------------------------- #
def process_frame(client: socket.socket, frame: bytes, handler: "ModbusPEPHandler") -> None:
    t0 = now_ms()
    request_id = uuid.uuid4().hex
    unit, fc, addr = parse_request(frame)
    action = fc_action(fc)
    logical = (40001 + addr) if (fc in HOLDING_FCS and addr is not None) else addr
    resource = {"protocol": "modbus", "function_code": fc, "register_address": logical, "unit_id": unit}

    token_status = "ok"
    token_verification_ms = 0.0
    cache_lookup_ms = 0.0
    pdp_decision_ms = 0.0
    modbus_forward_ms = 0.0
    cache_status = "none"
    decision_source = "none"
    pdp_available: Any = False
    claims: Dict[str, Any] = {}
    decision: Dict[str, Any] = {"allow": False, "mode": "deny", "reason": "not_evaluated", "ttl": 0}
    out: Optional[bytes] = None

    try:
        if PASSTHROUGH_MODE:
            cache_status = "passthrough"
            decision = {"allow": True, "mode": "passthrough", "reason": "passthrough_baseline", "ttl": 0}
            decision_source = "passthrough"
            pdp_available = "n/a"
        else:
            token = get_bound_token(handler.client_addr)
            if not token:
                token_status = "no_identity_binding"
                decision = {"allow": False, "mode": "deny", "reason": token_status, "ttl": 0}
                out = exception_response(frame, EXC_ILLEGAL_FUNCTION)
            else:
                t_token = now_ms()
                try:
                    claims = decode_token(token)
                    token_verification_ms = now_ms() - t_token
                except TokenError as exc:
                    token_status = str(exc)
                    token_verification_ms = now_ms() - t_token
                    decision = {"allow": False, "mode": "deny", "reason": token_status, "ttl": 0}
                    out = exception_response(frame, EXC_ILLEGAL_FUNCTION)

            if out is None and token_status == "ok":
                environment = get_env_context()
                opa_input = build_opa_input(claims, resource, action, environment)
                decision, cache_status, pdp_available, cache_lookup_ms, decision_source = evaluate_request(opa_input)
                pdp_decision_ms = float(decision.pop("_pdp_decision_ms", 0.0)) if cache_status == "miss" else 0.0
                if not decision.get("allow", False):
                    code = EXC_GATEWAY_NO_RESPONSE if decision.get("reason") == "pdp_unavailable_fail_safe" else EXC_ILLEGAL_FUNCTION
                    out = exception_response(frame, code)

        # Forward an allowed frame to the protected device; relay its response.
        if out is None and decision.get("allow", False):
            t_fwd = now_ms()
            upstream = handler.upstream()
            upstream.sendall(frame)
            response = read_frame(upstream)
            modbus_forward_ms = now_ms() - t_fwd
            out = response if response is not None else exception_response(frame, EXC_GATEWAY_NO_RESPONSE)

        if out is None:  # defensive: never leave a request unanswered
            out = exception_response(frame, EXC_ILLEGAL_FUNCTION)

        client.sendall(out)
    finally:
        log_proxy_metric(
            {
                "request_id": request_id,
                "pep_type": "modbus_native_passthrough" if PASSTHROUGH_MODE else "modbus_native_pep",
                "action": action,
                "function_code": fc,
                "register_address": logical,
                "decision": decision.get("mode", "unknown"),
                "reason": decision.get("reason", ""),
                "cache_status": cache_status,
                "decision_source": decision_source,
                "pdp_available": pdp_available,
                "token_status": token_status,
                "token_verification_ms": round(token_verification_ms, 6),
                "cache_lookup_ms": round(cache_lookup_ms, 6),
                "pdp_decision_ms": round(pdp_decision_ms, 6),
                "modbus_forward_ms": round(modbus_forward_ms, 6),
                "end_to_end_latency_ms": round(now_ms() - t0, 6),
                "risk_score": claims.get("risk_score", ""),
                "attestation_state": claims.get("attestation_state", ""),
                "policy_version": POLICY_VERSION,
            }
        )


class ModbusPEPHandler(socketserver.BaseRequestHandler):
    """One thread per client connection; parses and enforces each Modbus frame
    and lazily opens a dedicated upstream socket for this connection."""

    def setup(self) -> None:
        self._upstream: Optional[socket.socket] = None
        # Identity bindings are keyed by the source address of this connection.
        self.client_addr: str = self.client_address[0]

    def upstream(self) -> socket.socket:
        if self._upstream is None:
            self._upstream = socket.create_connection((UPSTREAM_HOST, UPSTREAM_PORT), timeout=5)
        return self._upstream

    def handle(self) -> None:
        client = self.request
        client.settimeout(120)
        try:
            while True:
                frame = read_frame(client)
                if frame is None:
                    break
                try:
                    process_frame(client, frame, self)
                except Exception:  # a bad frame must not silently drop the client
                    try:
                        client.sendall(exception_response(frame, EXC_GATEWAY_NO_RESPONSE))
                    except OSError:
                        break
                    # Upstream may be broken; force a reconnect on the next frame.
                    self._close_upstream()
        except (socket.timeout, OSError):
            pass
        finally:
            self._close_upstream()

    def _close_upstream(self) -> None:
        if self._upstream is not None:
            try:
                self._upstream.close()
            finally:
                self._upstream = None


class ThreadingTCPServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


# --------------------------------------------------------------------------- #
# Admin HTTP endpoints (health, resource stats, cache clear, identity-binding and
# context provisioning). Modbus is a binary protocol with no place for these, so
# a small test-only side channel is exposed on a separate, unpublished port.
# GET /health and GET /resource are read-only; every POST requires the shared
# admin secret (src/admin_auth.py) and is audit-logged, accepted or rejected.
# --------------------------------------------------------------------------- #
_AUDIT_FIELDS = ["timestamp_unix", "source", "method", "path", "status", "outcome", "client"]
_AUDIT_LOCK = threading.Lock()


def log_admin_audit(row: Dict[str, Any]) -> None:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    path = RESULTS_DIR / "modbus_proxy_admin_audit.csv"
    record = {f: row.get(f, "") for f in _AUDIT_FIELDS}
    record["timestamp_unix"] = time.time()
    with _AUDIT_LOCK:
        needs_header = not path.exists() or path.stat().st_size == 0
        with path.open("a", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=_AUDIT_FIELDS)
            if needs_header:
                writer.writeheader()
            writer.writerow(record)


class AdminHandler(BaseHTTPRequestHandler):
    def _send(self, code: int, obj: Dict[str, Any], *, outcome: str = "", client: str = "") -> None:
        if self.command == "POST":  # audit before answering
            log_admin_audit({
                "source": self.client_address[0], "method": self.command, "path": self.path,
                "status": code, "outcome": outcome or obj.get("error", "ok"), "client": client,
            })
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 (http.server API)
        if self.path == "/health":
            self._send(200, {
                "status": "ok",
                "pep_type": "modbus_native_passthrough" if PASSTHROUGH_MODE else "modbus_native_pep",
                "listen_port": LISTEN_PORT,
                "upstream": f"{UPSTREAM_HOST}:{UPSTREAM_PORT}",
                "opa_url": OPA_URL,
                "policy_version": POLICY_VERSION,
                "passthrough_mode": PASSTHROUGH_MODE,
                "identity_bindings": binding_count(),
                "context": get_env_context(),
                "cache_enabled": cache_enabled(),
                "cache_entries": len(DECISION_CACHE),
            })
        elif self.path == "/resource":
            stats = resource_stats()
            stats["cache_entries"] = len(DECISION_CACHE)
            self._send(200, stats)
        else:
            self._send(404, {"error": "not_found"})

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0") or 0)
        raw = self.rfile.read(length) if length else b""
        auth = check_bearer(self.headers.get("Authorization"))
        if auth != "ok":
            # admin_disabled: no secret configured -> administration fail-closed.
            self._send(503 if auth == "admin_disabled" else 401, {"error": auth})
            return
        try:
            body = json.loads(raw or b"{}")
        except json.JSONDecodeError:
            self._send(400, {"error": "invalid_json"})
            return
        if not isinstance(body, dict):
            self._send(400, {"error": "invalid_json"})
            return
        if self.path == "/cache/clear":
            with CACHE_LOCK:
                DECISION_CACHE.clear()
            self._send(200, {"cleared": True})
        elif self.path == "/admin/token":
            # Bind (or, with a null token, unbind) the token for one client
            # address. Without "client", the binding applies to the address the
            # orchestrator calls from, i.e. a client co-located with it.
            client = str(body.get("client") or self.client_address[0])
            try:
                client = str(ipaddress.ip_address(client))
            except ValueError:
                self._send(400, {"error": "invalid_client_address"}, client=client)
                return
            token = body.get("token")
            if token is not None and not isinstance(token, str):
                self._send(400, {"error": "invalid_token_field"}, client=client)
                return
            set_binding(client, token)
            bound = get_bound_token(client) is not None
            self._send(200, {"ok": True, "client": client, "bound": bound,
                             "identity_bindings": binding_count()},
                       outcome="bound" if bound else "unbound", client=client)
        elif self.path == "/admin/context":
            set_env_context(body)
            self._send(200, {"ok": True, "context": get_env_context()})
        elif self.path == "/admin/cache":
            set_cache_enabled(bool(body.get("enabled", True)))
            with CACHE_LOCK:
                DECISION_CACHE.clear()
            self._send(200, {"ok": True, "cache_enabled": cache_enabled()})
        else:
            self._send(404, {"error": "not_found"})

    def log_message(self, *args: Any) -> None:  # silence default stderr logging
        return


def main() -> None:
    admin = ThreadingHTTPServer(("0.0.0.0", ADMIN_PORT), AdminHandler)
    threading.Thread(target=admin.serve_forever, daemon=True).start()
    server = ThreadingTCPServer((LISTEN_HOST, LISTEN_PORT), ModbusPEPHandler)
    mode = "PASSTHROUGH" if PASSTHROUGH_MODE else "PEP"
    print(f"[modbus-proxy-{mode}] listening on {LISTEN_HOST}:{LISTEN_PORT} -> {UPSTREAM_HOST}:{UPSTREAM_PORT}; admin on :{ADMIN_PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()


if __name__ == "__main__":
    main()
