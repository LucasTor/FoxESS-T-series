"""Tests for the reader's connection lifecycle and receive loop."""
import asyncio
from unittest.mock import patch

import pytest

from custom_components.foxess_tseries import reader as reader_module
from custom_components.foxess_tseries.reader import InverterReader

from .fakes import FakeTransport, wait_for
from .test_inverter_payload import build_frame


def make_reader(**kwargs):
    options = {
        "host": "192.168.0.50", "port": 502, "serial_port": None,
        "baudrate": 9600, "payload_version": 0,
    }
    options.update(kwargs)
    reader = InverterReader(**options)
    payloads, lost = [], []
    reader.on_payload = payloads.append
    reader.on_lost = lambda: lost.append(True)
    return reader, payloads, lost


@pytest.fixture
def fast_timers(monkeypatch):
    """Shrink the reader's delays so loop tests run in milliseconds."""
    monkeypatch.setattr(reader_module, "RECONNECT_DELAY", 0.01)
    monkeypatch.setattr(reader_module, "NO_DATA_TIMEOUT", 0.05)
    monkeypatch.setattr(reader_module, "CONNECT_TIMEOUT", 0.5)


@pytest.fixture
async def running():
    """Track readers so their loops are stopped even when a test fails."""
    readers = []

    def track(made):
        readers.append(made[0])
        return made

    yield track
    for reader in readers:
        await reader.async_stop()


@pytest.fixture
def tcp():
    transport = FakeTransport()
    with patch.object(reader_module.asyncio, "open_connection", transport.open):
        yield transport


def test_describe_each_transport():
    assert make_reader()[0].describe() == "TCP bridge 192.168.0.50:502"
    usb = make_reader(serial_port="/dev/ttyUSB0")[0]
    assert usb.uses_serial and not usb.uses_esphome_proxy
    assert usb.describe() == "serial port /dev/ttyUSB0 at 9600 baud"
    proxy = make_reader(serial_port="esphome-hass://abc/0")[0]
    assert proxy.uses_esphome_proxy
    assert proxy.describe().startswith("ESPHome serial proxy esphome-hass://abc/0")


async def test_receives_frames_over_tcp(tcp, fast_timers, running):
    reader, payloads, _ = running(make_reader())
    reader.start()
    reader.start()  # a second start is ignored
    await asyncio.wait_for(tcp.connected.wait(), 1)

    tcp.reader.feed_data(build_frame(0) + build_frame(0))
    await wait_for(lambda: len(payloads) == 2)
    assert payloads[0]["grid_frequency_R"] == 60.04
    assert len(tcp.connections) == 1

    await reader.async_stop()
    assert tcp.connections[0][1].closed
    assert not reader.connected


async def test_serial_connect_uses_serialx(fast_timers, running):
    transport = FakeTransport()
    with patch.object(reader_module.serialx, "open_serial_connection", transport.open) as opened:
        reader, payloads, _ = running(make_reader(serial_port="/dev/ttyUSB0", baudrate=19200))
        await reader.async_connect()
        assert reader.connected
        reader.start()
        transport.reader.feed_data(build_frame(0))
        await wait_for(lambda: payloads)
        await reader.async_stop()
    assert len(transport.connections) == 1


async def test_reconnects_after_eof(tcp, fast_timers, running):
    reader, payloads, _ = running(make_reader())
    reader.start()
    await asyncio.wait_for(tcp.connected.wait(), 1)
    tcp.reader.feed_data(build_frame(0))
    tcp.reader.feed_eof()

    await wait_for(lambda: len(tcp.connections) == 2)
    tcp.reader.feed_data(build_frame(0))
    await wait_for(lambda: len(payloads) == 2)
    await reader.async_stop()


async def test_reconnects_after_connection_error(tcp, fast_timers, running):
    reader, _, _ = running(make_reader())
    reader.start()
    await asyncio.wait_for(tcp.connected.wait(), 1)
    tcp.reader.set_exception(ConnectionResetError("reset by peer"))
    await wait_for(lambda: len(tcp.connections) == 2)
    await reader.async_stop()


async def test_retries_when_unreachable(fast_timers, running):
    attempts = []

    async def refuse(*args, **kwargs):
        attempts.append(True)
        raise OSError("connection refused")

    with patch.object(reader_module.asyncio, "open_connection", refuse):
        reader, _, _ = running(make_reader())
        with pytest.raises(OSError):
            await reader.async_connect()
        reader.start()
        await wait_for(lambda: len(attempts) >= 3)
        await reader.async_stop()


async def test_connect_timeout(fast_timers, monkeypatch):
    monkeypatch.setattr(reader_module, "CONNECT_TIMEOUT", 0.01)

    async def hang(*args, **kwargs):
        await asyncio.sleep(10)

    with patch.object(reader_module.asyncio, "open_connection", hang):
        reader, _, _ = make_reader()
        with pytest.raises(asyncio.TimeoutError):
            await reader.async_connect()


async def feed_until_received(transport, payloads, count, frame):
    """Feed frame to the current connection until count payloads arrived.

    The short test timeout can cycle the TCP connection while data is being
    fed, so keep feeding whichever connection is current.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + 2
    fed = None
    while len(payloads) < count:
        assert loop.time() < deadline, "frame not received in time"
        if fed is not transport.reader:
            fed = transport.reader
            fed.feed_data(frame)
        await asyncio.sleep(0.005)


async def test_silence_zeroes_values_once_and_cycles_tcp(tcp, fast_timers, running):
    reader, payloads, lost = running(make_reader())
    reader.start()
    await asyncio.wait_for(tcp.connected.wait(), 1)
    await feed_until_received(tcp, payloads, 1, build_frame(0))

    # No more data: after the no-data timeout the values are zeroed and the
    # TCP connection is cycled.
    await wait_for(lambda: lost and len(tcp.connections) >= 2)
    await asyncio.sleep(0.15)
    assert len(lost) == 1  # zeroed only once while the silence lasts

    # Data resuming clears the zeroed state so a later silence zeroes again.
    await feed_until_received(tcp, payloads, 2, build_frame(0))
    assert not reader._values_zeroed
    await wait_for(lambda: len(lost) == 2)


async def test_serial_silence_keeps_connection(fast_timers, running):
    transport = FakeTransport()
    with patch.object(reader_module.serialx, "open_serial_connection", transport.open):
        reader, _, lost = running(make_reader(serial_port="/dev/ttyUSB0"))
        reader.start()
        await wait_for(lambda: lost)
        assert len(transport.connections) == 1
        await reader.async_stop()


async def test_invalid_frames_are_counted_not_delivered(tcp, fast_timers, running):
    reader, payloads, _ = running(make_reader())
    reader.start()
    await asyncio.wait_for(tcp.connected.wait(), 1)
    frame = bytearray(build_frame(0))
    frame[20] ^= 0xFF  # corrupt the data so the checksum no longer matches
    tcp.reader.feed_data(bytes(frame) + build_frame(0))
    await wait_for(lambda: payloads)
    assert reader._frames_rejected == 1
    await reader.async_stop()


async def test_disconnect_survives_broken_transport():
    reader, _, _ = make_reader()
    await reader.async_disconnect()  # nothing open: no-op

    class BrokenWriter:
        def close(self):
            raise OSError("already gone")

    reader._writer = BrokenWriter()
    await reader.async_disconnect()
    assert reader._writer is None
