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

ESPHOME_SCHEME = "esphome-hass://"


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

        self._connect_attempts = 0
        self._frames_ok = 0
        self._frames_rejected = 0

        _LOGGER.debug(
            "Reader created for %s (payload version %s, connect timeout %ss, "
            "no-data timeout %ss, reconnect delay %ss)",
            self.describe(), payload_version, CONNECT_TIMEOUT, NO_DATA_TIMEOUT, RECONNECT_DELAY,
        )

    # ---- Description helpers ----

    @property
    def uses_serial(self) -> bool:
        return is_serial_url(self._serial_port)

    @property
    def uses_esphome_proxy(self) -> bool:
        return bool(self._serial_port) and self._serial_port.startswith(ESPHOME_SCHEME)

    def describe(self) -> str:
        """Human readable description of the configured transport."""
        if self.uses_esphome_proxy:
            return f"ESPHome serial proxy {self._serial_port} at {self._baudrate} baud"
        if self.uses_serial:
            return f"serial port {self._serial_port} at {self._baudrate} baud"
        return f"TCP bridge {self._host}:{self._port}"

    @property
    def connected(self) -> bool:
        return self._writer is not None and not self._writer.is_closing()

    # ---- Connection lifecycle ----

    async def async_connect(self) -> None:
        """Open the transport. Raises on failure."""
        self._connect_attempts += 1
        started = time.monotonic()
        _LOGGER.debug("Connect attempt #%s to %s", self._connect_attempts, self.describe())

        try:
            if self.uses_serial:
                reader, writer = await asyncio.wait_for(
                    serialx.open_serial_connection(self._serial_port, baudrate=self._baudrate),
                    CONNECT_TIMEOUT,
                )
            else:
                reader, writer = await asyncio.wait_for(
                    asyncio.open_connection(self._host, self._port),
                    CONNECT_TIMEOUT,
                )
        except asyncio.TimeoutError:
            _LOGGER.debug(
                "Connect attempt #%s to %s timed out after %ss",
                self._connect_attempts, self.describe(), CONNECT_TIMEOUT,
            )
            raise
        except Exception as error:
            _LOGGER.debug(
                "Connect attempt #%s to %s failed after %.0fms: %s: %s",
                self._connect_attempts, self.describe(),
                (time.monotonic() - started) * 1000, type(error).__name__, error,
                exc_info=True,
            )
            raise

        self._reader = reader
        self._writer = writer

        if self.uses_serial:
            extra = f"transport={getattr(writer, 'transport', None)!r}"
        else:
            extra = f"local={writer.get_extra_info('sockname')} peer={writer.get_extra_info('peername')}"
        _LOGGER.debug(
            "Connected to %s in %.0fms (%s)",
            self.describe(), (time.monotonic() - started) * 1000, extra,
        )

    async def async_disconnect(self) -> None:
        writer = self._writer
        self._reader = None
        self._writer = None
        if writer is None:
            _LOGGER.debug("Disconnect requested but no connection is open")
            return
        _LOGGER.debug("Closing connection to %s", self.describe())
        try:
            writer.close()
            await writer.wait_closed()
            _LOGGER.debug("Connection to %s closed", self.describe())
        except Exception:  # noqa: BLE001 - closing a broken transport must never raise
            _LOGGER.debug("Error while closing connection to %s", self.describe(), exc_info=True)

    def start(self) -> None:
        """Start the background receive loop."""
        if self._task is None:
            _LOGGER.debug("Starting receive loop for %s", self.describe())
            self._task = asyncio.create_task(self._run(), name="foxess_tseries_reader")
        else:
            _LOGGER.debug("Receive loop already running for %s", self.describe())

    async def async_stop(self) -> None:
        _LOGGER.debug(
            "Stopping receive loop for %s (frames ok=%s rejected=%s, connect attempts=%s)",
            self.describe(), self._frames_ok, self._frames_rejected, self._connect_attempts,
        )
        task = self._task
        self._task = None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        await self.async_disconnect()
        _LOGGER.debug("Receive loop stopped for %s", self.describe())

    # ---- Receive loop ----

    async def _run(self) -> None:
        while True:
            if not self.connected:
                try:
                    await self.async_connect()
                except Exception as error:  # noqa: BLE001
                    _LOGGER.debug(
                        "%s unreachable (%s: %s), retrying in %ss",
                        self.describe(), type(error).__name__, error, RECONNECT_DELAY,
                    )
                    self._check_lost()
                    await asyncio.sleep(RECONNECT_DELAY)
                    continue

            try:
                data = await asyncio.wait_for(self._read_frame(), NO_DATA_TIMEOUT)
            except asyncio.TimeoutError:
                _LOGGER.debug(
                    "No complete frame from %s in the last %ss (%.0fs since last valid frame)",
                    self.describe(), NO_DATA_TIMEOUT, self._seconds_since_last_message(),
                )
                self._check_lost()
                if not self.uses_serial:
                    # A silent TCP bridge may hold a dead connection open; cycle it.
                    _LOGGER.debug("Cycling the TCP connection to %s:%s after silence", self._host, self._port)
                    await self.async_disconnect()
                continue
            except asyncio.IncompleteReadError as error:
                _LOGGER.debug(
                    "%s closed the connection (EOF) with %s partial bytes buffered: %s; reconnecting in %ss",
                    self.describe(), len(error.partial), error.partial.hex(), RECONNECT_DELAY,
                )
                await self.async_disconnect()
                self._check_lost()
                await asyncio.sleep(RECONNECT_DELAY)
                continue
            except (ConnectionError, OSError, serialx.SerialException) as error:
                _LOGGER.debug(
                    "Connection to %s lost (%s: %s); reconnecting in %ss",
                    self.describe(), type(error).__name__, error, RECONNECT_DELAY,
                    exc_info=True,
                )
                await self.async_disconnect()
                self._check_lost()
                await asyncio.sleep(RECONNECT_DELAY)
                continue
            except asyncio.LimitOverrunError as error:
                _LOGGER.warning(
                    "Stream from %s exceeded %s bytes without an end marker, discarding buffer and reconnecting",
                    self.describe(), error.consumed,
                )
                await self.async_disconnect()
                continue

            self._handle_frame(data)

    async def _read_frame(self) -> bytes:
        reader = self._reader
        if reader is None:
            raise ConnectionError("Not connected")

        data = await reader.readuntil(END_MARKER)
        _LOGGER.debug("Read %s bytes up to end marker: %s", len(data), data.hex())

        start_index = data.find(START_MARKER)
        if start_index == -1:
            _LOGGER.debug("End marker hit with no start marker, discarding %s bytes", len(data))
            self._frames_rejected += 1
            return b""
        if start_index > 0:
            _LOGGER.debug(
                "Discarding %s bytes before start marker: %s", start_index, data[:start_index].hex()
            )
        return data[start_index:]

    def _handle_frame(self, data: bytes) -> None:
        if not data:
            return

        if not validate_inverter_payload(data):
            self._frames_rejected += 1
            _LOGGER.debug(
                "Rejected frame of %s bytes (total rejected=%s): %s",
                len(data), self._frames_rejected, data.hex(),
            )
            return

        parsed_payload = parse_inverter_payload(data, self._payload_version)
        if not parsed_payload:
            self._frames_rejected += 1
            _LOGGER.debug(
                "Frame of %s bytes validated but could not be parsed with payload version %s "
                "(total rejected=%s): %s",
                len(data), self._payload_version, self._frames_rejected, data.hex(),
            )
            return

        self._frames_ok += 1
        _LOGGER.debug(
            "Valid frame #%s from %s: %s bytes, declared length %s, inverter timestamp %s, %s",
            self._frames_ok, self.describe(), len(data), int.from_bytes(data[7:9], "big"),
            parsed_payload["timestamp"],
            "first frame since connect" if self._frames_ok == 1
            else f"{self._seconds_since_last_message():.1f}s since previous valid frame",
        )
        _LOGGER.debug("Parsed values: %s", parsed_payload)

        self._last_message = time.monotonic()
        if self._values_zeroed:
            _LOGGER.debug("Data resumed after values were zeroed")
        self._values_zeroed = False
        self.on_payload(parsed_payload)

    def _seconds_since_last_message(self) -> float:
        return time.monotonic() - self._last_message

    def _check_lost(self) -> None:
        if self._values_zeroed:
            return
        silence = self._seconds_since_last_message()
        if silence > NO_DATA_TIMEOUT:
            _LOGGER.debug(
                "No valid frame for %.0fs (limit %ss), zeroing live values", silence, NO_DATA_TIMEOUT
            )
            self._values_zeroed = True
            self.on_lost()
        else:
            _LOGGER.debug("%.0fs since last valid frame, values kept until %ss", silence, NO_DATA_TIMEOUT)
