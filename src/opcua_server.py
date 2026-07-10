"""Minimal OPC UA server for ZT-IIoT prototype experiments.

The server exposes a small Line1 namespace with representative process variables:
- Line1.Temperature: read-only telemetry variable
- Line1.SpeedSetpoint: writable setpoint variable
- Line1.MotorRunning: writable Boolean actuator state

This is a laboratory simulator, not a production OPC UA deployment.
"""

from __future__ import annotations

import asyncio
import os

from asyncua import Server, ua

OPCUA_HOST = os.getenv("OPCUA_HOST", "0.0.0.0")
OPCUA_PORT = int(os.getenv("OPCUA_PORT", "4840"))
ENDPOINT = f"opc.tcp://{OPCUA_HOST}:{OPCUA_PORT}/zt-iiot/opcua/server/"
NAMESPACE_URI = "urn:zt-iiot:opcua:line1"


async def main() -> None:
    server = Server()
    await server.init()
    server.set_endpoint(ENDPOINT)
    server.set_server_name("ZT-IIoT Minimal OPC UA Server")

    idx = await server.register_namespace(NAMESPACE_URI)

    line1 = await server.nodes.objects.add_object(ua.NodeId("Line1", idx), "Line1")

    # Explicit string NodeIds keep the benchmark scripts stable and readable.
    temperature = await line1.add_variable(ua.NodeId("Line1.Temperature", idx), "Temperature", 21.5)

    speed = await line1.add_variable(ua.NodeId("Line1.SpeedSetpoint", idx), "SpeedSetpoint", 1500)
    await speed.set_writable()

    motor = await line1.add_variable(ua.NodeId("Line1.MotorRunning", idx), "MotorRunning", False)
    await motor.set_writable()

    print(f"OPC UA server started at {ENDPOINT}")
    print(f"Namespace index: {idx}")
    print('Useful NodeIds: ns=2;s=Line1.Temperature, ns=2;s=Line1.SpeedSetpoint, ns=2;s=Line1.MotorRunning')

    async with server:
        while True:
            await asyncio.sleep(1)


if __name__ == "__main__":
    asyncio.run(main())
