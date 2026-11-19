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

from .helpers.inverter_payload import (
    looks_plausible,
    parse_inverter_payload,
    suggest_payload_version,
    validate_inverter_payload,
)

_LOGGER = logging.getLogger(__name__)

START_MARKER = b"\x7e\x7e"
END_MARKER = b"\xe7\xe7"
# Function code of the periodic data frame the integration decodes.
DATA_FUNCTION_CODE = 0x02
# Start marker, function code, 4-byte timestamp and 2-byte data length.
HEADER_LENGTH = 9
# Header plus 2-byte checksum and end marker around the data.
FRAME_OVERHEAD = HEADER_LENGTH + 4
# Declared data lengths above this are treated as corrupt.
MAX_DATA_LENGTH = 1024
# Bytes buffered without finding a frame before the stream is reset.
MAX_BUFFER = 8192
READ_CHUNK = 512

CONNECT_TIMEOUT = 10
# Seconds without a complete frame before the values are zeroed. The inverter
# goes to sleep at night and stops sending anything.
NO_DATA_TIMEOUT = 300
RECONNECT_DELAY = 60

ESPHOME_SCHEME = "esphome-hass://"


def extract_frame(buffer: bytes) -> tuple[bytes | None, int]:
    """Find the next frame in buffer.

    Returns (frame, consumed): the frame bytes (None when more data is needed)
    and how many bytes to drop from the front of the buffer.

    Frames are cut using the data length in their header, because the end
    marker bytes can also appear inside the data or the checksum. When the
    declared length does not end on an end marker (a corrupt frame), or for
    other frame types whose layout is unknown, fall back to the first end
    marker after the start marker.
    """
    start = buffer.find(START_MARKER)
    if start == -1:
        # Keep a trailing byte that could be the first half of a start marker.
        keep = 1 if buffer.endswith(START_MARKER[:1]) else 0
        return None, len(buffer) - keep
    if start > 0:
        return None, start

    if len(buffer) < HEADER_LENGTH:
        return None, 0

    data_length = int.from_bytes(buffer[7:9], "big")
    if buffer[2] == DATA_FUNCTION_CODE and data_length <= MAX_DATA_LENGTH:
        frame_length = data_length + FRAME_OVERHEAD
        if len(buffer) < frame_length:
            return None, 0
        if buffer[frame_length - 2:frame_length] == END_MARKER:
            return buffer[:frame_length], frame_length

    end = buffer.find(END_MARKER, len(START_MARKER))
    if end == -1:
        return None, 0
    return buffer[:end + 2], end + 2


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
        self._buffer = b""
        self._writer: asyncio.StreamWriter | None = None
        self._task: asyncio.Task | None = None
        self._last_message = time.monotonic()
        self._values_zeroed = False

        self._connect_attempts = 0
        self._frames_ok = 0
        self._frames_rejected = 0
        self._payload_version_checked = False

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
        self._buffer = b""
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
                    "Stream from %s sent %s bytes without a complete frame, discarding buffer and reconnecting",
                    self.describe(), error.consumed,
                )
                await self.async_disconnect()
                continue

            self._handle_frame(data)

    async def _read_frame(self) -> bytes:
        reader = self._reader
        if reader is None:
            raise ConnectionError("Not connected")

        # The buffer lives on the instance so a timeout cancelling this read
        # does not lose bytes already received.
        while True:
            frame, consumed = extract_frame(self._buffer)
            if consumed:
                if frame is None:
                    _LOGGER.debug(
                        "Discarding %s bytes outside a frame: %s",
                        consumed, self._buffer[:consumed].hex(),
                    )
                self._buffer = self._buffer[consumed:]
            if frame is not None:
                _LOGGER.debug("Read %s byte frame: %s", len(frame), frame.hex())
                return frame
            if consumed:
                continue

            if len(self._buffer) > MAX_BUFFER:
                consumed = len(self._buffer)
                self._buffer = b""
                raise asyncio.LimitOverrunError(
                    "No complete frame in buffered data", consumed
                )

            chunk = await reader.read(READ_CHUNK)
            if not chunk:
                partial, self._buffer = self._buffer, b""
                raise asyncio.IncompleteReadError(partial, None)
            self._buffer += chunk

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

        self._check_payload_version(data)

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

    def _check_payload_version(self, data: bytes) -> None:
        """Warn once if the frame only makes sense with the other payload version."""
        if self._payload_version_checked:
            return

        if looks_plausible(parse_inverter_payload(data, self._payload_version)):
            _LOGGER.debug("Payload version %s looks right for %s", self._payload_version, self.describe())
            self._payload_version_checked = True
            return

        suggested = suggest_payload_version(data, self._payload_version)
        if suggested is None:
            # Neither version decodes plausibly (e.g. the grid is down); check the next frame.
            return
        self._payload_version_checked = True
        _LOGGER.warning(
            "Frames from %s do not decode plausibly with payload version %s but do with "
            "version %s (grid frequency is out of range otherwise). Change the payload "
            "version from the integration's Configure button. Frame: %s",
            self.describe(), self._payload_version, suggested, data.hex(),
        )

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
