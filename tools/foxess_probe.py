#!/usr/bin/env python3
"""Sniff the inverter's RS485 traffic, and optionally test register reads.

Usage (needs Python 3.8+, no extra packages):

    python3 tools/foxess_probe.py <EW11 IP> [--port 502] [--listen SECONDS] [--send]
    python3 tools/foxess_probe.py <EW11 IP> --scan 10000-12000

By default it only LISTENS (10 minutes, or 40 seconds with --send) and prints every frame on the bus,
including the factory Wi-Fi dongle's requests and the inverter's replies if
the dongle is connected in parallel. Listening never transmits anything.

--send also transmits a few Modbus "read registers" requests (function 0x04
or 0x03) after listening. These cannot change inverter settings. Each one is
sent 2 seconds after an inverter frame, in the quiet part of its 30-second
cycle, so it is unlikely to collide with traffic on a shared bus.

--scan START-END reads that register range (Modbus function 0x04, S-series
7F7F markers, which a T-series SIW200G answers), in blocks of 50, sending only
in the quiet gap after each inverter frame. It then compares every register
with the inverter's own 0x02 data frame captured during the scan, prints the
likely matches (e.g. which register holds the grid voltage) and saves all raw
results to a JSON file.

Run it during daylight, while the inverter is on, and paste the whole output
into the GitHub issue. Replace the serial number before posting it publicly.
"""
import argparse
import json
import socket
import struct
import time

LISTEN_SECONDS = 600
SEND_LISTEN_SECONDS = 40
REPLY_WAIT_SECONDS = 10
SEND_DELAY_SECONDS = 2
REQUEST_ID = bytes.fromhex("C5ECC56C")  # the ID seen in the S-series capture

MARKERS = {
    "T-series markers (7E7E/E7E7)": (b"\x7e\x7e", b"\xe7\xe7"),
    "S-series markers (7F7F/F7F7)": (b"\x7f\x7f", b"\xf7\xf7"),
}
# (Modbus function, start register, register count)
READS = [
    (0x04, 10100, 24),  # the read captured from the S-series cloud
    (0x03, 10100, 24),
]


def crc16(data: bytes) -> bytes:
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc.to_bytes(2, "little")


def build_request(start_marker, end_marker, function, register, count):
    pdu = bytes([0x01, function]) + register.to_bytes(2, "big") + count.to_bytes(2, "big")
    body = b"\x11" + REQUEST_ID + len(pdu).to_bytes(2, "big") + pdu
    return start_marker + body + crc16(body) + end_marker


def describe(frame: bytes) -> str:
    if len(frame) < 13:
        return f"short frame ({len(frame)} bytes)"
    function = frame[2]
    length = int.from_bytes(frame[7:9], "big")
    crc_ok = frame[-4:-2] == crc16(frame[2:-4])
    text = f"function 0x{function:02x}, {len(frame)} bytes, declared length {length}, checksum {'ok' if crc_ok else 'BAD'}"
    if function == 0x06 and crc_ok:
        text += f"\n      SERIAL NUMBER: {frame[9:-4].decode('ascii', 'replace')}"
    if function == 0x01 and crc_ok:
        parts = [part.decode("ascii", "replace") for part in frame[9:-4].split(b"\x00") if part]
        text += f"\n      INFO: {parts}"
    if function == 0x11 and crc_ok:
        data = frame[9:-4]
        if len(data) >= 6:
            register = int.from_bytes(data[2:4], "big")
            count = int.from_bytes(data[4:6], "big")
            text += (f"\n      REQUEST: unit {data[0]}, Modbus function 0x{data[1]:02x}, "
                     f"registers {register}-{register + count - 1} ({count}), ID {frame[3:7].hex()}")
    if function in (0x91, 0x92) and crc_ok:
        data = frame[9:-4]
        if len(data) >= 3 and data[1] in (0x03, 0x04):
            registers = [int.from_bytes(data[3 + i:5 + i], "big") for i in range(0, data[2], 2)]
            text += f"\n      REPLY: unit {data[0]}, Modbus function 0x{data[1]:02x}, registers: {registers}"
        elif len(data) >= 3 and data[1] & 0x80:
            text += f"\n      REPLY: Modbus error, exception code {data[2]}"
    return text


def split_frames(buffer: bytes):
    """Yield complete frames for either marker pair, return leftover bytes."""
    frames = []
    while True:
        starts = [i for i in (buffer.find(b"\x7e\x7e"), buffer.find(b"\x7f\x7f")) if i != -1]
        if not starts:
            return frames, buffer[-1:]
        start = min(starts)
        end_marker = b"\xe7\xe7" if buffer[start] == 0x7E else b"\xf7\xf7"
        buffer = buffer[start:]
        if len(buffer) >= 9:
            length = int.from_bytes(buffer[7:9], "big")
            total = length + 13
            if length <= 1024 and len(buffer) >= total and buffer[total - 2:total] == end_marker:
                frames.append(buffer[:total])
                buffer = buffer[total:]
                continue
        end = buffer.find(end_marker, 2)
        if end == -1:
            return frames, buffer
        frames.append(buffer[:end + 2])
        buffer = buffer[end + 2:]


def receive(sock, seconds, buffer=b"", until_frame=False):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        sock.settimeout(max(0.1, deadline - time.monotonic()))
        try:
            chunk = sock.recv(1024)
        except socket.timeout:
            break
        if not chunk:
            print("  connection closed by the EW11")
            break
        buffer += chunk
        frames, buffer = split_frames(buffer)
        for frame in frames:
            print(f"  [{time.strftime('%H:%M:%S')}] {describe(frame)}")
            print(f"      hex: {frame.hex()}")
        if until_frame and frames:
            break
    return buffer


# Field order of the 0x02 data frame, as decoded by the integration.
DATA_FIELDS = [
    "grid_power", "gen_power", "load_power",
    "grid_voltage_R", "grid_current_R", "grid_frequency_R", "grid_power_R",
    "grid_voltage_S", "grid_current_S", "grid_frequency_S", "grid_power_S",
    "grid_voltage_T", "grid_current_T", "grid_frequency_T", "grid_power_T",
    "PV1_voltage", "PV1_current", "PV1_power", "PV2_voltage", "PV2_current", "PV2_power",
    "PV3_voltage", "PV3_current", "PV3_power", "PV4_voltage", "PV4_current", "PV4_power",
    "boost_temperature", "inverter_temperature", "ambient_temperature", "todays_yield",
]
SCAN_CHUNK = 50
SCAN_GAP_SECONDS = 18  # stop sending this long after an inverter frame
SCAN_REPLY_SECONDS = 3
S_SERIES_MARKERS = (b"\x7f\x7f", b"\xf7\xf7")


def decode_data_frame(frame: bytes, payload_version: int) -> dict:
    """Raw (unscaled) field values from a 0x02 data frame."""
    offset = 9 if payload_version == 0 else 15
    values = struct.unpack(">31hi", frame[offset:offset + 66])
    raw = dict(zip(DATA_FIELDS, values[:31]))
    raw["total_yield"] = values[31]
    return raw


def read_frames(sock, buffer, seconds, stop=lambda frame: False):
    """Read frames for up to seconds, returning early once stop(frame) is true."""
    frames = []
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        sock.settimeout(max(0.05, deadline - time.monotonic()))
        try:
            chunk = sock.recv(1024)
        except socket.timeout:
            break
        if not chunk:
            raise ConnectionError("connection closed by the bridge")
        buffer += chunk
        new, buffer = split_frames(buffer)
        frames += new
        if any(stop(frame) for frame in new):
            break
    return frames, buffer


def is_push(frame):
    return frame[2] in (0x01, 0x02, 0x06)


def crc_ok(frame):
    return len(frame) >= 13 and frame[-4:-2] == crc16(frame[2:-4])


def scan(sock, buffer, start, end, function, payload_version):
    blocks = [(a, min(SCAN_CHUNK, end - a)) for a in range(start, end, SCAN_CHUNK)]
    retried = set()
    registers, errors, data_frames = {}, [], []
    total = len(blocks)
    print(f"\n2) Scanning registers {start}-{end - 1} with function 0x{function:02x} "
          f"in {total} blocks of up to {SCAN_CHUNK} (only in the quiet gap after inverter frames):")

    while blocks:
        frames, buffer = read_frames(sock, buffer, 45, stop=is_push)
        data_frames += [f for f in frames if f[2] == 0x02 and crc_ok(f)]
        if not any(is_push(f) for f in frames):
            print("  no inverter frame in 45s, is it daylight?")
            continue
        gap_start = time.monotonic()
        time.sleep(SEND_DELAY_SECONDS)
        while blocks and time.monotonic() - gap_start < SCAN_GAP_SECONDS:
            register, count = blocks.pop(0)
            sock.sendall(build_request(*S_SERIES_MARKERS, function, register, count))
            frames, buffer = read_frames(sock, buffer, SCAN_REPLY_SECONDS, stop=lambda f: f[2] == 0x91)
            data_frames += [f for f in frames if f[2] == 0x02 and crc_ok(f)]
            replies = [f for f in frames if f[2] == 0x91 and crc_ok(f)]
            done = total - len(blocks)
            if not replies:
                if register not in retried:
                    retried.add(register)
                    blocks.append((register, count))
                    print(f"  [{done}/{total}] {register}+{count}: no reply, will retry")
                else:
                    errors.append({"start": register, "count": count, "error": "no reply"})
                    print(f"  [{done}/{total}] {register}+{count}: no reply")
                continue
            data = replies[-1][9:-4]
            if len(data) >= 3 and data[1] == function:
                values = [int.from_bytes(data[3 + i:5 + i], "big") for i in range(0, data[2], 2)]
                registers.update({register + i: v for i, v in enumerate(values)})
                nonzero = sum(1 for v in values if v)
                print(f"  [{done}/{total}] {register}+{count}: ok, {nonzero} non-zero")
            elif len(data) >= 3 and data[1] & 0x80:
                errors.append({"start": register, "count": count, "error": f"exception {data[2]}"})
                print(f"  [{done}/{total}] {register}+{count}: Modbus exception {data[2]}")
            if any(is_push(f) for f in frames):
                break  # the inverter started its next frame; wait for the next gap
    return buffer, registers, errors, data_frames


def signed(value):
    return value - 0x10000 if value & 0x8000 else value


def report(registers, errors, data_frames, payload_version, path):
    print("\n3) Results")
    readable = sorted(registers)
    if readable:
        print(f"  readable registers: {len(readable)}, non-zero:")
        for address in readable:
            value = registers[address]
            if value:
                print(f"    {address}: {value:6d}  0x{value:04x}  signed {signed(value):6d}")
    for error in errors:
        print(f"  {error['start']}+{error['count']}: {error['error']}")

    matches = {}
    if data_frames:
        raw = decode_data_frame(data_frames[-1], payload_version)
        print("\n  Likely matches with the inverter's 0x02 data frame "
              "(values move a little between the frame and the scan):")
        for field, value in raw.items():
            if not value:
                continue
            found = []
            if field == "total_yield":
                for address in readable:
                    if address + 1 in registers:
                        pair = (registers[address] << 16) | registers[address + 1]
                        if abs(pair - value) <= 10:
                            found.append(f"{address}-{address + 1} ({pair})")
            else:
                tolerance = max(2, abs(value) * 0.05)
                for address in readable:
                    if abs(signed(registers[address]) - value) <= tolerance:
                        found.append(f"{address} ({signed(registers[address])})")
            matches[field] = found
            print(f"    {field:22} frame value {value:7d}: {', '.join(found[:6]) if found else '-'}"
                  f"{' ...' if len(found) > 6 else ''}")
    else:
        print("\n  No 0x02 data frame was captured during the scan, so nothing to match against.")

    with open(path, "w") as file:
        json.dump({
            "registers": {str(k): v for k, v in sorted(registers.items())},
            "errors": errors,
            "data_frames": [f.hex() for f in data_frames],
            "matches": matches,
        }, file, indent=1)
    print(f"\n  Raw results saved to {path}")


def parse_range(text):
    start, _, end = text.partition("-")
    start, end = int(start), int(end) + 1
    if not 0 <= start < end <= 0x10000 or end - start > 5000:
        raise argparse.ArgumentTypeError("use START-END, at most 5000 registers, e.g. 10000-12000")
    return start, end


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("host", help="IP address of the RS485-to-TCP bridge (e.g. the EW11)")
    parser.add_argument("--port", type=int, default=502)
    parser.add_argument("--listen", type=int, default=None,
                        help="seconds to listen first (default 600, or 40 with --send)")
    parser.add_argument("--send", action="store_true", help="also send read requests after listening")
    parser.add_argument("--scan", type=parse_range, metavar="START-END",
                        help="read this input register range and match it with the data frame")
    parser.add_argument("--function", type=lambda v: int(v, 0), default=0x04,
                        help="Modbus read function for --scan (default 0x04)")
    parser.add_argument("--payload-version", type=int, default=0, choices=(0, 1))
    args = parser.parse_args()
    if args.listen is None:
        args.listen = SEND_LISTEN_SECONDS if (args.send or args.scan) else LISTEN_SECONDS

    print(f"Connecting to {args.host}:{args.port} ...")
    sock = socket.create_connection((args.host, args.port), timeout=10)
    print("Connected.\n")

    then = ", then sending read requests" if (args.send or args.scan) else " (nothing is sent)"
    print(f"1) Listening for {args.listen}s to everything on the bus{then}:")
    buffer = receive(sock, args.listen)

    if args.send:
        print("\n2) Sending read requests:")
        for name, (start_marker, end_marker) in MARKERS.items():
            for function, register, count in READS:
                request = build_request(start_marker, end_marker, function, register, count)
                # The inverter pushes a frame every 30 seconds. Send right after
                # one so the request lands in the quiet gap instead of colliding.
                print("\n  (waiting for the next inverter frame before sending)")
                buffer = receive(sock, 45, buffer, until_frame=True)
                time.sleep(SEND_DELAY_SECONDS)
                print(f"\n  -> {name}, Modbus function 0x{function:02x}, registers {register}+{count}")
                print(f"      sent: {request.hex()}")
                sock.sendall(request)
                buffer = receive(sock, REPLY_WAIT_SECONDS, buffer)

    if args.scan:
        start, end = args.scan
        buffer, registers, errors, data_frames = scan(
            sock, buffer, start, end, args.function, args.payload_version
        )
        report(registers, errors, data_frames, args.payload_version,
               f"foxess_scan_{start}-{end - 1}_{time.strftime('%Y%m%d-%H%M%S')}.json")

    sock.close()
    print("\nDone. Frames with function 0x11 are requests (e.g. from the factory dongle);")
    print("REPLY lines are the inverter's answers.")


if __name__ == "__main__":
    main()
