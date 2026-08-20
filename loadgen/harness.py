"""Unified latency / throughput harness for the ZT-IIoT prototype.

This harness supports four measurement dimensions:

  * Matched targets: --target direct | passthrough | pep
      direct       native protocol client -> server/broker (no gateway)
      passthrough  HTTP gateway with identical translation/forwarding but no
                   token verification, cache, or policy (PASSTHROUGH_MODE)
      pep          full ZT-IIoT PEP (token + cache + OPA)
    The passthrough->pep difference isolates the incremental security cost.

  * Independent repetition and warm-up: --trials, --warmup.
    Every measured request is written to a raw per-request CSV so that all
    reported statistics are recomputable and no table is derived from a
    cumulative, heterogeneous log.

  * Workload diversity: --profile
      steady        one cacheable read repeated (high cache-hit)
      cold          a distinct device per request (cold-cache, all misses)
      mixed         alternating cacheable read / non-cacheable write
      hitratio:P    reuse the cacheable key with probability P, else a new
                    device (controlled cache-hit ratio, 0.0 <= P <= 1.0)

  * Concurrency / throughput: --concurrency C issues requests from
    C worker threads; the raw CSV plus wall-clock span support requests/sec and
    tail-latency-under-load analysis (see analyze.py).

Raw output columns:
  trial, seq, protocol, target, profile, concurrency,
  latency_ms, status, cache_status
"""

from __future__ import annotations

import argparse
import csv
import os
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import requests

from src.auth import mint_token

RESULTS_DIR = Path(os.getenv("RESULTS_DIR", "/app/results"))

PEP_BASE = {
    "modbus": os.getenv("PEP_URL", "http://modbus-pep:8080"),
    "mqtt": os.getenv("MQTT_PEP_URL", "http://mqtt-pep:8081"),
    "opcua": os.getenv("OPCUA_PEP_URL", "http://opcua-pep:8082"),
}
GW_BASE = {
    "modbus": os.getenv("MODBUS_GW_URL", "http://modbus-gw:8090"),
    "mqtt": os.getenv("MQTT_GW_URL", "http://mqtt-gw:8091"),
    "opcua": os.getenv("OPCUA_GW_URL", "http://opcua-gw:8092"),
}

_thread_local = threading.local()


# --------------------------------------------------------------------------- #
# Token cache (minting is off the timed path)
# --------------------------------------------------------------------------- #
_token_cache: Dict[tuple, str] = {}
_token_lock = threading.Lock()


def token_for(device: str, role: str, risk: int, attestation: str = "valid") -> str:
    key = (device, role, risk, attestation)
    with _token_lock:
        tok = _token_cache.get(key)
        if tok is None:
            tok = mint_token(subject=device, role=role, risk_score=risk,
                             attestation_state=attestation, ttl_seconds=3600)
            _token_cache[key] = tok
        return tok


# --------------------------------------------------------------------------- #
# HTTP session per worker thread (connection reuse)
# --------------------------------------------------------------------------- #
def _session() -> requests.Session:
    s = getattr(_thread_local, "session", None)
    if s is None:
        s = requests.Session()
        _thread_local.session = s
    return s


def http_request(base: str, path: str, payload: Dict[str, Any], token: Optional[str]):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    t0 = time.perf_counter()
    r = _session().post(f"{base}{path}", json=payload, headers=headers, timeout=10)
    latency_ms = (time.perf_counter() - t0) * 1000.0
    cache_status = "none"
    if r.status_code == 200:
        try:
            cache_status = r.json().get("cache_status", "none")
        except Exception:
            pass
    return latency_ms, r.status_code, cache_status


# --------------------------------------------------------------------------- #
# Native direct adapters (thread-local persistent clients where applicable)
# --------------------------------------------------------------------------- #
def modbus_direct(params: Dict[str, Any]):
    from pymodbus.client import ModbusTcpClient
    client = getattr(_thread_local, "modbus", None)
    if client is None:
        host = os.getenv("MODBUS_HOST", "modbus-server")
        port = int(os.getenv("MODBUS_PORT", "5020"))
        client = ModbusTcpClient(host, port=port)
        client.connect()
        _thread_local.modbus = client
    addr = params["register_address"]
    offset = addr - 40001 if addr >= 40001 else addr
    t0 = time.perf_counter()
    resp = client.read_holding_registers(address=offset, count=params.get("count", 1), slave=1)
    latency_ms = (time.perf_counter() - t0) * 1000.0
    return latency_ms, (502 if resp.isError() else 200), "direct"


def mqtt_direct(params: Dict[str, Any]):
    import uuid
    import paho.mqtt.client as mqtt
    client = getattr(_thread_local, "mqtt", None)
    if client is None:
        host = os.getenv("MQTT_HOST", "mqtt-broker")
        port = int(os.getenv("MQTT_PORT", "1883"))
        client = mqtt.Client(client_id=f"zt-harness-{uuid.uuid4()}", clean_session=True)
        client.connect(host, port, keepalive=30)
        client.loop_start()
        _thread_local.mqtt = client
    t0 = time.perf_counter()
    info = client.publish(params["topic"], payload=params.get("payload", "{}"), qos=params.get("qos", 0))
    info.wait_for_publish(timeout=5)
    latency_ms = (time.perf_counter() - t0) * 1000.0
    return latency_ms, (200 if info.rc == mqtt.MQTT_ERR_SUCCESS else 502), "direct"


def opcua_direct(params: Dict[str, Any]):
    import asyncio
    from asyncua import Client
    endpoint = os.getenv("OPCUA_ENDPOINT", "opc.tcp://opcua-server:4840/zt-iiot/opcua/server/")

    async def _read():
        async with Client(url=endpoint) as client:
            node = client.get_node(params["node_id"])
            return await node.read_value()

    t0 = time.perf_counter()
    asyncio.run(_read())
    latency_ms = (time.perf_counter() - t0) * 1000.0
    return latency_ms, 200, "direct"


# --------------------------------------------------------------------------- #
# Request builder: (protocol, target) -> callable(params) -> (lat, status, cache)
# --------------------------------------------------------------------------- #
def build_caller(protocol: str, target: str) -> Callable[[Dict[str, Any]], Any]:
    if target == "direct":
        return {"modbus": modbus_direct, "mqtt": mqtt_direct, "opcua": opcua_direct}[protocol]

    base = (GW_BASE if target == "passthrough" else PEP_BASE)[protocol]

    if protocol == "modbus":
        def call(p):
            token = None if target == "passthrough" else token_for(p["device"], p["role"], p["risk"])
            path = "/modbus/write" if p.get("write") else "/modbus/read"
            payload = {"register_address": p["register_address"], "maintenance_window": p.get("maintenance_window", False)}
            if p.get("write"):
                payload["value"] = p.get("value", 1600)
            else:
                payload["count"] = p.get("count", 1)
            return http_request(base, path, payload, token)
        return call

    if protocol == "mqtt":
        def call(p):
            token = None if target == "passthrough" else token_for(p["device"], p["role"], p["risk"])
            payload = {"topic": p["topic"], "payload": p.get("payload", "{}"), "qos": p.get("qos", 0),
                       "maintenance_window": p.get("maintenance_window", False)}
            return http_request(base, "/mqtt/publish", payload, token)
        return call

    if protocol == "opcua":
        def call(p):
            token = None if target == "passthrough" else token_for(p["device"], p["role"], p["risk"])
            path = "/opcua/write" if p.get("write") else "/opcua/read"
            payload = {"node_id": p["node_id"], "maintenance_window": p.get("maintenance_window", False)}
            if p.get("write"):
                payload["value"] = p.get("value", 1600)
            return http_request(base, path, payload, token)
        return call

    raise ValueError(protocol)


# --------------------------------------------------------------------------- #
# Workload generation
# --------------------------------------------------------------------------- #
def _base_read(protocol: str, device: str) -> Dict[str, Any]:
    p = {"device": device, "role": "operator", "risk": 20, "maintenance_window": False, "write": False}
    if protocol == "modbus":
        p["register_address"] = 40001
    elif protocol == "mqtt":
        p["topic"] = "factory/line1/plc1/status"
    else:
        p["node_id"] = "ns=2;s=Line1.Temperature"
    return p


def _write_req(protocol: str, device: str) -> Dict[str, Any]:
    p = {"device": device, "role": "maintenance_engineer", "risk": 20, "maintenance_window": True, "write": True}
    if protocol == "modbus":
        p["register_address"] = 40005
    elif protocol == "mqtt":
        p["topic"] = "factory/line1/plc1/cmd"
        p["write"] = False  # publish endpoint; command topic is non-cacheable
    else:
        p["node_id"] = "ns=2;s=Line1.SpeedSetpoint"
    return p


def generate_plan(protocol: str, profile: str, n: int, seed: int) -> List[Dict[str, Any]]:
    rnd = random.Random(seed)
    plan: List[Dict[str, Any]] = []
    if profile == "steady":
        for _ in range(n):
            plan.append(_base_read(protocol, "dev_steady"))
    elif profile == "cold":
        for i in range(n):
            plan.append(_base_read(protocol, f"dev_cold_{i}"))
    elif profile == "mixed":
        for i in range(n):
            if i % 2 == 0:
                plan.append(_base_read(protocol, "dev_mixed"))
            else:
                plan.append(_write_req(protocol, "dev_mixed"))
    elif profile.startswith("hitratio:"):
        p_hit = float(profile.split(":", 1)[1])
        for i in range(n):
            if rnd.random() < p_hit:
                plan.append(_base_read(protocol, "dev_hit"))
            else:
                plan.append(_base_read(protocol, f"dev_miss_{i}"))
    else:
        raise ValueError(f"unknown profile: {profile}")
    return plan


# --------------------------------------------------------------------------- #
# Runner
# --------------------------------------------------------------------------- #
def run_trial(caller, plan: List[Dict[str, Any]], concurrency: int) -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = [None] * len(plan)  # type: ignore

    def worker(idx_params):
        idx, params = idx_params
        try:
            lat, status, cache = caller(params)
            results[idx] = {"latency_ms": lat, "status": status, "cache_status": cache}
        except Exception as exc:  # a transient error must not abort the whole trial
            results[idx] = {"latency_ms": 0.0, "status": 0, "cache_status": f"error:{type(exc).__name__}"}

    if concurrency <= 1:
        for item in enumerate(plan):
            worker(item)
    else:
        with ThreadPoolExecutor(max_workers=concurrency) as ex:
            list(ex.map(worker, enumerate(plan)))
    return results


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--protocol", choices=["modbus", "mqtt", "opcua"], required=True)
    ap.add_argument("--target", choices=["direct", "passthrough", "pep"], required=True)
    ap.add_argument("--profile", default="steady")
    ap.add_argument("--n", type=int, default=1000, help="measured requests per trial")
    ap.add_argument("--warmup", type=int, default=50, help="warm-up requests per trial (excluded)")
    ap.add_argument("--trials", type=int, default=5)
    ap.add_argument("--concurrency", type=int, default=1)
    ap.add_argument("--clear-cache", action="store_true", help="clear PEP cache before each trial")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    if args.target == "direct" and args.concurrency > 1:
        print("note: forcing concurrency=1 for direct native target")
        args.concurrency = 1

    caller = build_caller(args.protocol, args.target)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    tag = f"{args.protocol}_{args.target}_{args.profile.replace(':', '')}_c{args.concurrency}"
    out = Path(args.out) if args.out else RESULTS_DIR / f"raw_{tag}.csv"

    all_rows: List[Dict[str, Any]] = []
    for trial in range(args.trials):
        if args.clear_cache and args.target == "pep":
            try:
                _session().post(f"{PEP_BASE[args.protocol]}/cache/clear", timeout=5)
            except requests.RequestException:
                pass
        # warm-up (discarded)
        if args.warmup > 0:
            run_trial(caller, generate_plan(args.protocol, args.profile, args.warmup, seed=1000 + trial), args.concurrency)
        # measured
        span0 = time.perf_counter()
        results = run_trial(caller, generate_plan(args.protocol, args.profile, args.n, seed=trial), args.concurrency)
        span = time.perf_counter() - span0
        for seq, res in enumerate(results):
            all_rows.append({
                "trial": trial, "seq": seq, "protocol": args.protocol, "target": args.target,
                "profile": args.profile, "concurrency": args.concurrency,
                "latency_ms": round(res["latency_ms"], 6), "status": res["status"],
                "cache_status": res["cache_status"],
            })
        rps = args.n / span if span > 0 else 0.0
        print(f"trial {trial}: n={args.n} span={span:.3f}s throughput={rps:.1f} req/s")

    with out.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(all_rows[0].keys()))
        writer.writeheader()
        writer.writerows(all_rows)
    print(f"Wrote {out} ({len(all_rows)} rows)")


if __name__ == "__main__":
    main()
