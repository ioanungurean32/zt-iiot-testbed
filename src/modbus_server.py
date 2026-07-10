"""Minimal Modbus/TCP server with holding registers for the testbed.

Logical mapping used in the paper/prototype:
- register 40001 -> internal offset 0
- register 40002 -> internal offset 1
- ...
"""

from __future__ import annotations

import logging
import os

from pymodbus.datastore import ModbusSequentialDataBlock, ModbusServerContext, ModbusSlaveContext
from pymodbus.server import StartTcpServer

logging.basicConfig(level=logging.INFO)
LOG = logging.getLogger("modbus-server")


def main() -> None:
    host = os.getenv("MODBUS_HOST", "0.0.0.0")
    port = int(os.getenv("MODBUS_PORT", "5020"))

    # 100 holding registers. offset 0 corresponds to logical 40001.
    initial_holding_registers = [0] * 100
    initial_holding_registers[0] = 210  # e.g., temperature setpoint = 21.0 C
    initial_holding_registers[1] = 100  # e.g., pressure setpoint
    initial_holding_registers[4] = 1500 # e.g., motor speed setpoint

    store = ModbusSlaveContext(
        di=ModbusSequentialDataBlock(0, [0] * 100),
        co=ModbusSequentialDataBlock(0, [0] * 100),
        hr=ModbusSequentialDataBlock(0, initial_holding_registers),
        ir=ModbusSequentialDataBlock(0, [0] * 100),
    )
    context = ModbusServerContext(slaves=store, single=True)

    LOG.info("Starting Modbus/TCP server on %s:%s", host, port)
    StartTcpServer(context=context, address=(host, port))


if __name__ == "__main__":
    main()
