"""Application-level Modbus Policy Enforcement Point (PEP).

This first prototype exposes HTTP endpoints and forwards allowed requests to a
Modbus/TCP server. It is intentionally simple and measurable:
- local JWT verification
- OPA/PDP authorization
- local decision cache for read decisions
- fail-safe behavior when PDP is unavailable
- CSV metrics for latency/jitter analysis
"""

from __future__ import annotations

import concurrent.futures
import os
import threading
import time
import uuid
from typing import Any, Dict, Optional, Tuple

import requests
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field
from pymodbus.client import ModbusTcpClient

from src.auth import TokenError, decode_authorization_header
from src.metrics import log_metric, metrics_path

app = FastAPI(title="ZT-IIoT Modbus PEP", version="0.1")

MODBUS_HOST = os.getenv("MODBUS_HOST", "localhost")
MODBUS_PORT = int(os.getenv("MODBUS_PORT", "5020"))
OPA_URL = os.getenv("OPA_URL", "http://localhost:8181/v1/data/zt_iiot/authz/decision")
OPA_CONNECT_TIMEOUT = float(os.getenv("OPA_CONNECT_TIMEOUT", "0.25"))
OPA_READ_TIMEOUT = float(os.getenv("OPA_READ_TIMEOUT", "0.75"))
POLICY_VERSION = os.getenv("POLICY_VERSION", "v0.1-modbus")
CACHE_WRITES = os.getenv("CACHE_WRITES", "false").lower() == "true"

# requests' timeout=(connect, read) tuple does not bound DNS resolution
# (socket.getaddrinfo), which can block for several seconds when the OPA
# Docker Compose service is stopped. opa_decision() runs the call in this
# executor and enforces a hard wall-clock deadline so the PEP still responds
# within the configured budget even when DNS resolution itself hangs.
_OPA_EXECUTOR = concurrent.futures.ThreadPoolExecutor(max_workers=8, thread_name_prefix="opa-call")

# key -> (expires_at, decision_dict)
DECISION_CACHE: Dict[str, Tuple[float, Dict[str, Any]]] = {}

# Persistent Modbus/TCP client used by the PEP. Reconnecting for every request
# adds a full TCP handshake per call and makes PEP-mediated latency incomparable
# to the direct baseline, which connects once and reuses the socket for all
# requests. A persistent client better represents an edge gateway and keeps the
# Modbus baseline/PEP comparison fair (mirrors the MQTT PEP's persistent client).
MODBUS_CLIENT: Optional[ModbusTcpClient] = None
MODBUS_LOCK = threading.Lock()


class ModbusReadRequest(BaseModel):
    unit_id: int = Field(default=1, ge=1, le=247)
    register_address: int = Field(default=40001, ge=0)
    count: int = Field(default=1, ge=1, le=20)
    maintenance_window: bool = False
    shift: str = "day"


class ModbusWriteRequest(BaseModel):
    unit_id: int = Field(default=1, ge=1, le=247)
    register_address: int = Field(default=40005, ge=0)
    value: int = Field(default=123, ge=0, le=65535)
    maintenance_window: bool = True
    shift: str = "day"


def logical_to_modbus_offset(address: int) -> int:
    """Map user-facing register 40001.. to PyModbus zero-based offset."""
    return address - 40001 if address >= 40001 else address


def now_ms() -> float:
    return time.perf_counter() * 1000.0


def cache_key(subject: Dict[str, Any], resource: Dict[str, Any], action: str) -> str:
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
            POLICY_VERSION,
        ]
    )


def get_cached_decision(key: str) -> Optional[Dict[str, Any]]:
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
    ttl = int(decision.get("ttl", 0) or 0)
    if ttl <= 0:
        return
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
    key = cache_key(opa_input["subject"], opa_input["resource"], opa_input["action"])

    t_cache = now_ms()
    cached = get_cached_decision(key)
    cache_lookup_ms = now_ms() - t_cache
    if cached is not None:
        # A cache hit does not contact the PDP, so PDP availability is not observed.
        return cached, "hit", "not_queried", cache_lookup_ms, "cache"

    decision, pdp_available, pdp_decision_ms = opa_decision(opa_input)
    store_cached_decision(key, decision, opa_input["action"])
    decision["_pdp_decision_ms"] = pdp_decision_ms
    return decision, "miss", pdp_available, cache_lookup_ms, "pdp"


def ensure_modbus_client() -> ModbusTcpClient:
    global MODBUS_CLIENT
    if MODBUS_CLIENT is None:
        MODBUS_CLIENT = ModbusTcpClient(MODBUS_HOST, port=MODBUS_PORT)
    # connect() is a cheap no-op if the socket is already open, and reconnects
    # if the peer closed it, so it is safe to call on every request.
    if not MODBUS_CLIENT.connect():
        raise RuntimeError("modbus_connect_failed")
    return MODBUS_CLIENT


def forward_modbus_read(register_address: int, count: int, unit_id: int) -> Dict[str, Any]:
    offset = logical_to_modbus_offset(register_address)
    with MODBUS_LOCK:
        client = ensure_modbus_client()
        result = client.read_holding_registers(address=offset, count=count, slave=unit_id)
        if result.isError():
            raise RuntimeError(str(result))
        return {"registers": result.registers, "offset": offset}


def forward_modbus_write(register_address: int, value: int, unit_id: int) -> Dict[str, Any]:
    offset = logical_to_modbus_offset(register_address)
    with MODBUS_LOCK:
        client = ensure_modbus_client()
        result = client.write_register(address=offset, value=value, slave=unit_id)
        if result.isError():
            raise RuntimeError(str(result))
        return {"written": True, "offset": offset, "value": value}


def common_pipeline(
    *,
    action: str,
    function_code: int,
    resource: Dict[str, Any],
    environment: Dict[str, Any],
    authorization: Optional[str],
    forward_fn,
) -> Dict[str, Any]:
    request_id = str(uuid.uuid4())
    t0 = now_ms()
    token_status = "ok"
    token_verification_ms = 0.0
    cache_lookup_ms = 0.0
    pdp_decision_ms = 0.0
    modbus_forward_ms = 0.0
    decision: Dict[str, Any] = {"allow": False, "mode": "deny", "reason": "not_evaluated", "ttl": 0}
    pdp_available: Any = False
    decision_source = "none"
    claims: Dict[str, Any] = {}
    status_code = 403

    try:
        t_token = now_ms()
        claims = decode_authorization_header(authorization)
        token_verification_ms = now_ms() - t_token

        opa_input = build_opa_input(claims, resource, action, environment)
        decision, cache_status, pdp_available, cache_lookup_ms, decision_source = evaluate_request(opa_input)
        pdp_decision_ms = float(decision.pop("_pdp_decision_ms", 0.0)) if cache_status == "miss" else 0.0

        if not decision.get("allow", False):
            status_code = 403
            raise HTTPException(status_code=status_code, detail={"decision": decision, "request_id": request_id})

        t_modbus = now_ms()
        modbus_result = forward_fn()
        modbus_forward_ms = now_ms() - t_modbus
        status_code = 200

        return {
            "request_id": request_id,
            "decision": decision,
            "cache_status": cache_status,
            "decision_source": decision_source,
            "pdp_available": pdp_available,
            "modbus_result": modbus_result,
        }

    except TokenError as exc:
        token_status = str(exc)
        status_code = 401
        decision = {"allow": False, "mode": "deny", "reason": token_status, "ttl": 0}
        raise HTTPException(status_code=status_code, detail={"decision": decision, "request_id": request_id}) from exc

    except RuntimeError as exc:
        status_code = 502
        decision = {"allow": False, "mode": "error", "reason": str(exc), "ttl": 0}
        raise HTTPException(status_code=status_code, detail={"decision": decision, "request_id": request_id}) from exc

    finally:
        end_to_end_latency_ms = now_ms() - t0
        log_metric(
            {
                "request_id": request_id,
                "protocol": "modbus",
                "action": action,
                "function_code": function_code,
                "register_address": resource.get("register_address"),
                "decision": decision.get("mode", "unknown"),
                "reason": decision.get("reason", ""),
                "cache_status": locals().get("cache_status", "none"),
                "decision_source": decision_source,
                "pdp_available": pdp_available,
                "token_status": token_status,
                "token_verification_ms": round(token_verification_ms, 6),
                "cache_lookup_ms": round(cache_lookup_ms, 6),
                "pdp_decision_ms": round(pdp_decision_ms, 6),
                "modbus_forward_ms": round(modbus_forward_ms, 6),
                "end_to_end_latency_ms": round(end_to_end_latency_ms, 6),
                "risk_score": claims.get("risk_score", ""),
                "attestation_state": claims.get("attestation_state", ""),
                "policy_version": POLICY_VERSION,
            }
        )


@app.on_event("startup")
def startup_modbus_client() -> None:
    with MODBUS_LOCK:
        ensure_modbus_client()


@app.on_event("shutdown")
def shutdown_modbus_client() -> None:
    global MODBUS_CLIENT
    with MODBUS_LOCK:
        if MODBUS_CLIENT is not None:
            try:
                MODBUS_CLIENT.close()
            finally:
                MODBUS_CLIENT = None


@app.get("/health")
def health() -> Dict[str, Any]:
    return {
        "status": "ok",
        "modbus_host": MODBUS_HOST,
        "modbus_port": MODBUS_PORT,
        "opa_url": OPA_URL,
        "policy_version": POLICY_VERSION,
        "metrics_path": str(metrics_path()),
        "cache_entries": len(DECISION_CACHE),
    }


@app.post("/modbus/read")
def modbus_read(payload: ModbusReadRequest, authorization: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    resource = {
        "protocol": "modbus",
        "function_code": 3,
        "register_address": payload.register_address,
        "count": payload.count,
        "unit_id": payload.unit_id,
    }
    environment = {"maintenance_window": payload.maintenance_window, "shift": payload.shift}
    return common_pipeline(
        action="read",
        function_code=3,
        resource=resource,
        environment=environment,
        authorization=authorization,
        forward_fn=lambda: forward_modbus_read(payload.register_address, payload.count, payload.unit_id),
    )


@app.post("/modbus/write")
def modbus_write(payload: ModbusWriteRequest, authorization: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    resource = {
        "protocol": "modbus",
        "function_code": 6,
        "register_address": payload.register_address,
        "unit_id": payload.unit_id,
    }
    environment = {"maintenance_window": payload.maintenance_window, "shift": payload.shift}
    return common_pipeline(
        action="write",
        function_code=6,
        resource=resource,
        environment=environment,
        authorization=authorization,
        forward_fn=lambda: forward_modbus_write(payload.register_address, payload.value, payload.unit_id),
    )


@app.post("/cache/clear")
def clear_cache() -> Dict[str, Any]:
    DECISION_CACHE.clear()
    return {"cleared": True}
