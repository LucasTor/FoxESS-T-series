"""Async connection handling for the FoxESS T Series inverter.

Supports three transports, all driven by the same frame reader:

* RS485 to TCP bridges (plain TCP socket)
* Local RS485 USB adapters (serialx, e.g. ``/dev/serial/by-id/...``)
* ESPHome serial proxies (serialx ``esphome-hass://`` URLs, resolved by the
  Home Assistant ESPHome integration)
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from typing import Any

import serialx

from .helpers.inverter_payload import parse_inverter_payload, validate_inverter_payload

_LOGGER = logging.getLogger(__name__)

START_MARKER = b"\x7e\x7e"
END_MARKER = b"\xe7\xe7"

CONNECT_TIMEOUT = 10
# Seconds without a complete frame before the values are zeroed. The inverter
# goes to sleep at night and stops sending anything.
NO_DATA_TIMEOUT = 300
RECONNECT_DELAY = 60


def is_serial_url(serial_port: str | None) -> bool:
    """Return True when the configured port should be opened through serialx."""
    return bool(serial_port)


class InverterReader:
    """Keeps a connection to the inverter and pushes parsed payloads."""

    def __init__(
        self,
        *,
        host: str | None,
        port: int | None,
        serial_port: str | None,
        baudrate: int,
        payload_version: int,
        on_payload: Callable[[dict[str, Any]], None] | None = None,
        on_lost: Callable[[], None] | None = None,
    ) -> None:
        self._host = host
        self._port = port
        self._serial_port = serial_port
        self._baudrate = baudrate
        self._payload_version = payload_version
        # Set by the sensor platform once the entities exist.
        self.on_payload: Callable[[dict[str, Any]], None] = on_payload or (lambda payload: None)
        self.on_lost: Callable[[], None] = on_lost or (lambda: None)

        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._task: asyncio.Task | None = None
        self._last_message = time.monotonic()
        self._values_zeroed = False

    @property
    def uses_serial(self) -> bool:
        return is_serial_url(self._serial_port)

    @property
    def connected(self) -> bool:
        return self._writer is not None and not self._writer.is_closing()

    async def async_connect(self) -> None:
        """Open the transport. Raises on failure."""
        if self.uses_serial:
            _LOGGER.debug("Opening serial port %s at %s baud", self._serial_port, self._baudrate)
            reader, writer = await asyncio.wait_for(
                serialx.open_serial_connection(
                    self._serial_port,
                    baudrate=self._baudrate,
                ),
                CONNECT_TIMEOUT,
            )
        else:
            _LOGGER.debug("Connecting to FoxESS T Series on %s:%s", self._host, self._port)
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(self._host, self._port),
                CONNECT_TIMEOUT,
            )

        self._reader = reader
        self._writer = writer
        _LOGGER.debug("Connected to inverter")

    async def async_disconnect(self) -> None:
        writer = self._writer
        self._reader = None
        self._writer = None
        if writer is None:
            return
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:  # noqa: BLE001 - closing a broken transport must never raise
            _LOGGER.debug("Error while closing inverter connection", exc_info=True)

    def start(self) -> None:
        """Start the background receive loop."""
        if self._task is None:
            self._task = asyncio.create_task(self._run())

    async def async_stop(self) -> None:
        task = self._task
        self._task = None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        await self.async_disconnect()

    async def _run(self) -> None:
        while True:
            if not self.connected:
                try:
                    await self.async_connect()
                except Exception as error:  # noqa: BLE001
                    _LOGGER.debug("Inverter unreachable, retrying in %ss: %s", RECONNECT_DELAY, error)
                    self._check_lost()
                    await asyncio.sleep(RECONNECT_DELAY)
                    continue

            try:
                data = await asyncio.wait_for(self._read_frame(), NO_DATA_TIMEOUT)
            except asyncio.TimeoutError:
                _LOGGER.debug("No frame received in the last %ss", NO_DATA_TIMEOUT)
                self._check_lost()
                if not self.uses_serial:
                    # A silent TCP bridge may hold a dead connection open; cycle it.
                    await self.async_disconnect()
                continue
            except (asyncio.IncompleteReadError, ConnectionError, OSError, serialx.SerialException) as error:
                _LOGGER.debug("Inverter connection lost: %s", error)
                await self.async_disconnect()
                self._check_lost()
                await asyncio.sleep(RECONNECT_DELAY)
                continue
            except asyncio.LimitOverrunError:
                _LOGGER.warning("Serial stream flooding without an end marker, discarding buffer")
                await self.async_disconnect()
                continue

            self._handle_frame(data)

    async def _read_frame(self) -> bytes:
        reader = self._reader
        if reader is None:
            raise ConnectionError("Not connected")

        data = await reader.readuntil(END_MARKER)
        start_index = data.find(START_MARKER)
        if start_index == -1:
            _LOGGER.debug("Message end marker hit with no start marker.")
            return b""
        return data[start_index:]

    def _handle_frame(self, data: bytes) -> None:
        if not data:
            return

        if not validate_inverter_payload(data):
            _LOGGER.debug("Invalid payload.")
            return

        parsed_payload = parse_inverter_payload(data, self._payload_version)
        if not parsed_payload:
            _LOGGER.debug("Empty parsed payload?")
            return

        _LOGGER.debug("Received new inverter payload at %s", parsed_payload["timestamp"])
        _LOGGER.debug(data.hex())

        self._last_message = time.monotonic()
        self._values_zeroed = False
        self.on_payload(parsed_payload)

    def _check_lost(self) -> None:
        if self._values_zeroed:
            return
        if time.monotonic() - self._last_message > NO_DATA_TIMEOUT:
            _LOGGER.debug("No message received in the last 5 minutes, zeroing values.")
            self._values_zeroed = True
            self.on_lost()
