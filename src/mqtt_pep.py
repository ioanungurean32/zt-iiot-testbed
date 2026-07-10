"""Application-level MQTT Policy Enforcement Point (PEP).

This prototype exposes HTTP endpoints for representative MQTT operations and
forwards allowed publish requests to a Mosquitto broker. It mirrors the Modbus
PEP pipeline so the paper can report comparable measurements:
- local JWT verification
- OPA/PDP authorization
- local decision cache for low-risk topic decisions
- fail-safe behavior when PDP is unavailable
- CSV metrics for latency/jitter analysis

It is intentionally an application-level gateway, not a transparent MQTT proxy.
"""

from __future__ import annotations

import concurrent.futures
import os
import time
import uuid
import threading
from typing import Any, Dict, Optional, Tuple

import paho.mqtt.client as mqtt
import requests
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from src.auth import TokenError, decode_authorization_header
from src.metrics import log_metric, metrics_path

app = FastAPI(title="ZT-IIoT MQTT PEP", version="0.1")

MQTT_HOST = os.getenv("MQTT_HOST", "localhost")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
OPA_URL = os.getenv("OPA_URL", "http://localhost:8181/v1/data/zt_iiot/authz/decision")
OPA_CONNECT_TIMEOUT = float(os.getenv("OPA_CONNECT_TIMEOUT", "0.25"))
OPA_READ_TIMEOUT = float(os.getenv("OPA_READ_TIMEOUT", "0.75"))
POLICY_VERSION = os.getenv("POLICY_VERSION", "v0.1-mqtt")

# requests' timeout=(connect, read) tuple does not bound DNS resolution
# (socket.getaddrinfo), which can block for several seconds when the OPA
# Docker Compose service is stopped. opa_decision() runs the call in this
# executor and enforces a hard wall-clock deadline so the PEP still responds
# within the configured budget even when DNS resolution itself hangs.
_OPA_EXECUTOR = concurrent.futures.ThreadPoolExecutor(max_workers=8, thread_name_prefix="opa-call")

# key -> (expires_at, decision_dict)
DECISION_CACHE: Dict[str, Tuple[float, Dict[str, Any]]] = {}

# Persistent MQTT client used by the PEP. Creating a client and starting/stopping
# the network loop for every publish can add roughly one second per request on
# some Docker Desktop / Windows setups. A persistent client better represents an
# edge gateway and makes MQTT measurements comparable with the Modbus/OPC UA
# PEP paths.
MQTT_CLIENT: Optional[mqtt.Client] = None
MQTT_LOCK = threading.Lock()


class MQTTPublishRequest(BaseModel):
    topic: str = Field(default="factory/line1/plc1/status", min_length=1, max_length=256)
    payload: str = Field(default="{\"temperature\":21.0}")
    qos: int = Field(default=0, ge=0, le=1)
    retain: bool = False
    maintenance_window: bool = False
    shift: str = "day"


class MQTTSubscribeAuthRequest(BaseModel):
    topic: str = Field(default="factory/line1/plc1/status", min_length=1, max_length=256)
    qos: int = Field(default=0, ge=0, le=1)
    maintenance_window: bool = False
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
            str(resource.get("topic")),
            str(resource.get("qos")),
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


def store_cached_decision(key: str, decision: Dict[str, Any], action: str, resource: Dict[str, Any]) -> None:
    # Do not cache command/setpoint publish decisions. This keeps actuator-like
    # operations conservative under control-plane degradation.
    topic = str(resource.get("topic", ""))
    if action == "publish" and ("/setpoint" in topic or "/cmd" in topic or "/command" in topic):
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
        "protocol": "mqtt",
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
    store_cached_decision(key, decision, opa_input["action"], opa_input["resource"])
    decision["_pdp_decision_ms"] = pdp_decision_ms
    return decision, "miss", pdp_available, cache_lookup_ms, "pdp"


def forward_mqtt_publish(topic: str, payload: str, qos: int, retain: bool) -> Dict[str, Any]:
    # Reuse a persistent MQTT client instead of reconnecting and restarting the
    # Paho network loop for each request. The lock keeps publish/wait operations
    # deterministic for the single-threaded benchmark workload.
    with MQTT_LOCK:
        client = ensure_mqtt_client()
        info = client.publish(topic=topic, payload=payload, qos=qos, retain=retain)
        info.wait_for_publish(timeout=5)
        if info.rc != mqtt.MQTT_ERR_SUCCESS:
            raise RuntimeError(f"mqtt_publish_failed_rc_{info.rc}")
        return {"published": True, "topic": topic, "qos": qos, "payload_size_bytes": len(payload.encode("utf-8"))}


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
    mqtt_forward_ms = 0.0
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

        t_mqtt = now_ms()
        mqtt_result = forward_fn()
        mqtt_forward_ms = now_ms() - t_mqtt
        status_code = 200

        return {
            "request_id": request_id,
            "decision": decision,
            "cache_status": cache_status,
            "decision_source": decision_source,
            "pdp_available": pdp_available,
            "mqtt_result": mqtt_result,
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
                "protocol": "mqtt",
                "action": action,
                "topic": resource.get("topic"),
                "qos": resource.get("qos"),
                "payload_size_bytes": resource.get("payload_size_bytes"),
                "decision": decision.get("mode", "unknown"),
                "reason": decision.get("reason", ""),
                "cache_status": cache_status,
                "decision_source": decision_source,
                "pdp_available": pdp_available,
                "token_status": token_status,
                "token_verification_ms": round(token_verification_ms, 6),
                "cache_lookup_ms": round(cache_lookup_ms, 6),
                "pdp_decision_ms": round(pdp_decision_ms, 6),
                "mqtt_forward_ms": round(mqtt_forward_ms, 6),
                "end_to_end_latency_ms": round(end_to_end_latency_ms, 6),
                "risk_score": claims.get("risk_score", ""),
                "attestation_state": claims.get("attestation_state", ""),
                "policy_version": POLICY_VERSION,
            }
        )


def ensure_mqtt_client() -> mqtt.Client:
    global MQTT_CLIENT
    if MQTT_CLIENT is None:
        MQTT_CLIENT = mqtt.Client(client_id=f"zt-mqtt-pep-{uuid.uuid4()}", clean_session=True)
        MQTT_CLIENT.connect(MQTT_HOST, MQTT_PORT, keepalive=30)
        MQTT_CLIENT.loop_start()
    return MQTT_CLIENT


@app.on_event("startup")
def startup_mqtt_client() -> None:
    ensure_mqtt_client()


@app.on_event("shutdown")
def shutdown_mqtt_client() -> None:
    global MQTT_CLIENT
    if MQTT_CLIENT is not None:
        try:
            MQTT_CLIENT.disconnect()
            MQTT_CLIENT.loop_stop()
        finally:
            MQTT_CLIENT = None


@app.get("/health")
def health() -> Dict[str, Any]:
    return {
        "status": "ok",
        "mqtt_host": MQTT_HOST,
        "mqtt_port": MQTT_PORT,
        "opa_url": OPA_URL,
        "policy_version": POLICY_VERSION,
        "metrics_path": str(metrics_path()),
        "cache_entries": len(DECISION_CACHE),
    }


@app.post("/cache/clear")
def clear_cache() -> Dict[str, Any]:
    DECISION_CACHE.clear()
    return {"cleared": True}


@app.post("/mqtt/publish")
def mqtt_publish(payload: MQTTPublishRequest, authorization: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    resource = {
        "protocol": "mqtt",
        "topic": payload.topic,
        "qos": payload.qos,
        "payload_size_bytes": len(payload.payload.encode("utf-8")),
    }
    environment = {"maintenance_window": payload.maintenance_window, "shift": payload.shift}
    return common_pipeline(
        action="publish",
        resource=resource,
        environment=environment,
        authorization=authorization,
        forward_fn=lambda: forward_mqtt_publish(payload.topic, payload.payload, payload.qos, payload.retain),
    )


@app.post("/mqtt/authorize-subscribe")
def mqtt_authorize_subscribe(payload: MQTTSubscribeAuthRequest, authorization: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    resource = {
        "protocol": "mqtt",
        "topic": payload.topic,
        "qos": payload.qos,
        "payload_size_bytes": 0,
    }
    environment = {"maintenance_window": payload.maintenance_window, "shift": payload.shift}
    return common_pipeline(
        action="subscribe",
        resource=resource,
        environment=environment,
        authorization=authorization,
        forward_fn=lambda: {"authorized": True, "topic": payload.topic, "qos": payload.qos},
    )
