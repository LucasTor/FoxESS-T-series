"""Tests for splitting the byte stream into frames."""
import asyncio
import struct

import pytest

from custom_components.foxess_tseries.helpers.inverter_payload import calculate_crc
from custom_components.foxess_tseries.reader import InverterReader, extract_frame

SHORTS = [0, 495, 0, 2401, 20, 6004, 495] + [0] * 8 + [2002, 9, 180, 2737, 10, 273] + [0] * 6 + [33, 33, 30, 165]


def data_frame(total_yield: int = 277696) -> bytes:
    data = struct.pack(">31hi", *SHORTS, total_yield) + b"\x00" * 88
    body = b"\x02" + struct.pack(">i", 0x6AFC165A) + struct.pack(">H", len(data)) + data
    return b"\x7e\x7e" + body + calculate_crc(body) + b"\xe7\xe7"


def has_early_end_marker(frame: bytes) -> bool:
    return frame.find(b"\xe7\xe7") != len(frame) - 2


# Total yields whose frame contains e7e7 before the real end marker, in the
# checksum or the data. About 1 frame in 250 looks like this.
EARLY_MARKER_YIELDS = [y for y in range(277000, 280000) if has_early_end_marker(data_frame(y))]

# A frame of another type (rejected later by its function code) whose length
# field is not trusted.
OTHER_FRAME = bytes.fromhex("7e7e01") + b"\x00\x00\x00\x00\xff\xff" + b"\x11\x22" + b"\xe7\xe7"


def make_reader():
    reader = InverterReader(
        host="192.168.0.50", port=502, serial_port=None, baudrate=9600, payload_version=0
    )
    parsed = []
    reader.on_payload = parsed.append
    return reader, parsed


async def read_all(reader, *chunks):
    stream = asyncio.StreamReader()
    for chunk in chunks:
        stream.feed_data(chunk)
    stream.feed_eof()
    reader._reader = stream
    frames = []
    while True:
        try:
            frames.append(await reader._read_frame())
        except asyncio.IncompleteReadError:
            return frames


def test_early_marker_cases_exist():
    assert len(EARLY_MARKER_YIELDS) > 0


def test_extract_frame_uses_declared_length():
    frame = data_frame(EARLY_MARKER_YIELDS[0])
    assert extract_frame(frame + data_frame()) == (frame, len(frame))


def test_extract_frame_waits_for_more_data():
    frame = data_frame()
    for cut in (1, 5, 9, len(frame) - 1):
        assert extract_frame(frame[:cut]) == (None, 0)


def test_extract_frame_skips_bytes_before_start():
    assert extract_frame(b"\x00\x01" + data_frame()) == (None, 2)
    assert extract_frame(b"\x00\x01\x02") == (None, 3)
    # A trailing 0x7e may be half of the next start marker.
    assert extract_frame(b"\x00\x01\x7e") == (None, 2)


@pytest.mark.parametrize("total_yield", EARLY_MARKER_YIELDS[:10])
async def test_frames_with_early_end_marker_are_parsed(total_yield):
    reader, parsed = make_reader()
    frames = await read_all(reader, data_frame(total_yield) + data_frame())
    for frame in frames:
        reader._handle_frame(frame)
    assert len(parsed) == 2
    assert parsed[0]["total_yield"] == total_yield / 10
    assert reader._frames_rejected == 0


async def test_no_frames_lost_over_many_frames():
    reader, parsed = make_reader()
    stream = b"".join(data_frame(y) for y in range(277000, 280000))
    for frame in await read_all(reader, stream):
        reader._handle_frame(frame)
    assert len(parsed) == 3000
    assert reader._frames_rejected == 0


async def test_frame_split_across_reads():
    reader, parsed = make_reader()
    frame = data_frame(EARLY_MARKER_YIELDS[0])
    chunks = [frame[i:i + 7] for i in range(0, len(frame), 7)]
    for frame in await read_all(reader, *chunks):
        reader._handle_frame(frame)
    assert len(parsed) == 1


async def test_garbage_and_other_frame_types_between_frames():
    reader, parsed = make_reader()
    frames = await read_all(
        reader, b"\x00\x13\x37" + data_frame() + OTHER_FRAME + b"\xaa" + data_frame()
    )
    assert frames == [data_frame(), OTHER_FRAME, data_frame()]
    for frame in frames:
        reader._handle_frame(frame)
    assert len(parsed) == 2
    assert reader._frames_rejected == 1


async def test_corrupt_length_falls_back_to_end_marker():
    reader, _ = make_reader()
    good = data_frame()
    corrupt = good[:7] + b"\x00\x10" + good[9:]  # wrong declared length
    frames = await read_all(reader, corrupt + good)
    assert frames[-1] == good


async def test_timeout_mid_frame_keeps_buffered_bytes():
    reader, parsed = make_reader()
    stream = asyncio.StreamReader()
    reader._reader = stream
    frame = data_frame()
    stream.feed_data(frame[:50])
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(reader._read_frame(), 0.05)
    stream.feed_data(frame[50:])
    assert await reader._read_frame() == frame
