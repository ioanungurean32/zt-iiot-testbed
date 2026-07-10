"""Application-level OPC UA Policy Enforcement Point (PEP).

This prototype exposes HTTP endpoints for representative OPC UA operations and
forwards allowed read/write requests to a minimal OPC UA server. It mirrors the
Modbus and MQTT PEP pipelines so measurements are comparable:
- local JWT verification
- OPA/PDP authorization
- local decision cache for low-risk read decisions
- fail-safe behavior when PDP is unavailable
- CSV metrics for latency/jitter analysis

It is intentionally an application-level gateway, not a transparent OPC UA proxy.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import os
import time
import uuid
from typing import Any, Dict, Optional, Tuple

import requests
from asyncua import Client, ua
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from src.auth import TokenError, decode_authorization_header
from src.metrics import log_metric, metrics_path

app = FastAPI(title="ZT-IIoT OPC UA PEP", version="0.1")

OPCUA_ENDPOINT = os.getenv("OPCUA_ENDPOINT", "opc.tcp://localhost:4840/zt-iiot/opcua/server/")
OPA_URL = os.getenv("OPA_URL", "http://localhost:8181/v1/data/zt_iiot/authz/decision")
OPA_CONNECT_TIMEOUT = float(os.getenv("OPA_CONNECT_TIMEOUT", "0.25"))
OPA_READ_TIMEOUT = float(os.getenv("OPA_READ_TIMEOUT", "0.75"))
POLICY_VERSION = os.getenv("POLICY_VERSION", "v0.1-opcua")

# requests' timeout=(connect, read) tuple does not bound DNS resolution
# (socket.getaddrinfo), which can block for several seconds when the OPA
# Docker Compose service is stopped. opa_decision() runs the call in this
# executor and enforces a hard wall-clock deadline so the PEP still responds
# within the configured budget even when DNS resolution itself hangs.
_OPA_EXECUTOR = concurrent.futures.ThreadPoolExecutor(max_workers=8, thread_name_prefix="opa-call")

# key -> (expires_at, decision_dict)
DECISION_CACHE: Dict[str, Tuple[float, Dict[str, Any]]] = {}


class OPCUAReadRequest(BaseModel):
    node_id: str = Field(default="ns=2;s=Line1.Temperature", min_length=1, max_length=256)
    maintenance_window: bool = False
    shift: str = "day"


class OPCUAWriteRequest(BaseModel):
    node_id: str = Field(default="ns=2;s=Line1.SpeedSetpoint", min_length=1, max_length=256)
    value: Any = 1600
    value_type: str = Field(default="int", pattern="^(int|float|bool|string)$")
    maintenance_window: bool = True
    shift: str = "day"


class OPCUAMethodAuthRequest(BaseModel):
    object_id: str = Field(default="ns=2;s=Line1", min_length=1, max_length=256)
    method_id: str = Field(default="ns=2;s=Line1.StartMotor", min_length=1, max_length=256)
    maintenance_window: bool = True
    shift: str = "day"


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
            str(resource.get("node_id")),
            str(resource.get("method_id", "")),
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
    # Do not cache write or method-call decisions. These operations can change
    # process state and must not fail open under PDP outage.
    if action in {"write", "call_method"}:
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
        "protocol": "opcua",
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


def parse_value(value: Any, value_type: str) -> Any:
    if value_type == "int":
        return int(value)
    if value_type == "float":
        return float(value)
    if value_type == "bool":
        if isinstance(value, bool):
            return value
        return str(value).lower() in {"1", "true", "yes", "on"}
    return str(value)


async def _opcua_read(node_id: str) -> Dict[str, Any]:
    async with Client(url=OPCUA_ENDPOINT) as client:
        node = client.get_node(node_id)
        value = await node.read_value()
        return {"node_id": node_id, "value": value}


async def _opcua_write(node_id: str, value: Any, value_type: str) -> Dict[str, Any]:
    parsed = parse_value(value, value_type)
    async with Client(url=OPCUA_ENDPOINT) as client:
        node = client.get_node(node_id)
        await node.write_value(parsed)
        read_back = await node.read_value()
        return {"node_id": node_id, "written": True, "value": read_back}


def forward_opcua_read(node_id: str) -> Dict[str, Any]:
    return asyncio.run(_opcua_read(node_id))


def forward_opcua_write(node_id: str, value: Any, value_type: str) -> Dict[str, Any]:
    return asyncio.run(_opcua_write(node_id, value, value_type))


def forward_opcua_method_authorization(object_id: str, method_id: str) -> Dict[str, Any]:
    # Minimal experimental prototype: the method-call endpoint
    # measures authorization for OPC UA methods. The actual method body is
    # represented as an authorized control action rather than a server-side
    # custom method implementation.
    return {"authorized": True, "object_id": object_id, "method_id": method_id, "method_invoked": "simulated"}


def common_pipeline(
    *,
    action: str,
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
    opcua_forward_ms = 0.0
    decision: Dict[str, Any] = {"allow": False, "mode": "deny", "reason": "not_evaluated", "ttl": 0}
    pdp_available: Any = False
    decision_source = "none"
    claims: Dict[str, Any] = {}
    cache_status = "none"
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

        t_opcua = now_ms()
        opcua_result = forward_fn()
        opcua_forward_ms = now_ms() - t_opcua
        status_code = 200

        return {
            "request_id": request_id,
            "decision": decision,
            "cache_status": cache_status,
            "decision_source": decision_source,
            "pdp_available": pdp_available,
            "opcua_result": opcua_result,
        }

    except TokenError as exc:
        token_status = str(exc)
        status_code = 401
        decision = {"allow": False, "mode": "deny", "reason": token_status, "ttl": 0}
        raise HTTPException(status_code=status_code, detail={"decision": decision, "request_id": request_id}) from exc

    except Exception as exc:
        if isinstance(exc, HTTPException):
            raise exc
        status_code = 502
        decision = {"allow": False, "mode": "error", "reason": str(exc), "ttl": 0}
        raise HTTPException(status_code=status_code, detail={"decision": decision, "request_id": request_id}) from exc

    finally:
        end_to_end_latency_ms = now_ms() - t0
        log_metric(
            {
                "request_id": request_id,
                "protocol": "opcua",
                "action": action,
                "node_id": resource.get("node_id"),
                "method_id": resource.get("method_id"),
                "decision": decision.get("mode", "unknown"),
                "reason": decision.get("reason", ""),
                "cache_status": cache_status,
                "decision_source": decision_source,
                "pdp_available": pdp_available,
                "token_status": token_status,
                "token_verification_ms": round(token_verification_ms, 6),
                "cache_lookup_ms": round(cache_lookup_ms, 6),
                "pdp_decision_ms": round(pdp_decision_ms, 6),
                "opcua_forward_ms": round(opcua_forward_ms, 6),
                "end_to_end_latency_ms": round(end_to_end_latency_ms, 6),
                "risk_score": claims.get("risk_score", ""),
                "attestation_state": claims.get("attestation_state", ""),
                "policy_version": POLICY_VERSION,
            }
        )


@app.get("/health")
def health() -> Dict[str, Any]:
    return {
        "status": "ok",
        "opcua_endpoint": OPCUA_ENDPOINT,
        "opa_url": OPA_URL,
        "policy_version": POLICY_VERSION,
        "metrics_path": str(metrics_path()),
        "cache_entries": len(DECISION_CACHE),
    }


@app.post("/cache/clear")
def clear_cache() -> Dict[str, Any]:
    DECISION_CACHE.clear()
    return {"cleared": True}


@app.post("/opcua/read")
def opcua_read(payload: OPCUAReadRequest, authorization: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    resource = {"protocol": "opcua", "node_id": payload.node_id}
    environment = {"maintenance_window": payload.maintenance_window, "shift": payload.shift}
    return common_pipeline(
        action="read",
        resource=resource,
        environment=environment,
        authorization=authorization,
        forward_fn=lambda: forward_opcua_read(payload.node_id),
    )


@app.post("/opcua/write")
def opcua_write(payload: OPCUAWriteRequest, authorization: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    resource = {"protocol": "opcua", "node_id": payload.node_id}
    environment = {"maintenance_window": payload.maintenance_window, "shift": payload.shift}
    return common_pipeline(
        action="write",
        resource=resource,
        environment=environment,
        authorization=authorization,
        forward_fn=lambda: forward_opcua_write(payload.node_id, payload.value, payload.value_type),
    )


@app.post("/opcua/call-method")
def opcua_call_method(payload: OPCUAMethodAuthRequest, authorization: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    resource = {"protocol": "opcua", "node_id": payload.object_id, "method_id": payload.method_id}
    environment = {"maintenance_window": payload.maintenance_window, "shift": payload.shift}
    return common_pipeline(
        action="call_method",
        resource=resource,
        environment=environment,
        authorization=authorization,
        forward_fn=lambda: forward_opcua_method_authorization(payload.object_id, payload.method_id),
    )
