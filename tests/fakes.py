"""Fake transports for driving the reader without real sockets or serial ports."""
import asyncio


class FakeWriter:
    """Minimal stand-in for an asyncio StreamWriter."""

    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True

    def is_closing(self):
        return self.closed

    async def wait_closed(self):
        return None

    def get_extra_info(self, name, default=None):
        return ("127.0.0.1", 502) if name in ("sockname", "peername") else default


class FakeTransport:
    """Hands out (reader, writer) pairs and records each connection."""

    def __init__(self):
        self.connections: list[tuple[asyncio.StreamReader, FakeWriter]] = []
        self.connected = asyncio.Event()

    async def open(self, *args, **kwargs):
        reader = asyncio.StreamReader()
        writer = FakeWriter()
        self.connections.append((reader, writer))
        self.connected.set()
        return reader, writer

    @property
    def reader(self) -> asyncio.StreamReader:
        return self.connections[-1][0]


async def wait_for(condition, timeout=2.0):
    """Wait until condition() is true, yielding to the event loop."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not condition():
        if loop.time() > deadline:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(0.01)
