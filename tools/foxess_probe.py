#!/usr/bin/env python3
"""Sniff the inverter's RS485 traffic, and optionally test register reads.

Usage (needs Python 3.8+, no extra packages):

    python3 tools/foxess_probe.py <EW11 IP> [--port 502] [--listen SECONDS] [--send]
    python3 tools/foxess_probe.py <EW11 IP> --scan 10000-12000
    python3 tools/foxess_probe.py <EW11 IP> --tables 10000,10100,10700

By default it only LISTENS (10 minutes; 40 seconds with --send; not at all with --scan) and prints every frame on the bus,
including the factory Wi-Fi dongle's requests and the inverter's replies if
the dongle is connected in parallel. Listening never transmits anything.

--send also transmits a few Modbus "read registers" requests (function 0x04
or 0x03) after listening. These cannot change inverter settings. Each one is
sent 2 seconds after an inverter frame, in the quiet part of its 30-second
cycle, so it is unlikely to collide with traffic on a shared bus.

--scan START-END reads that register range (Modbus function 0x04, S-series
7F7F markers, which a T-series SIW200G answers), in blocks of 10, sending only
in the quiet gap after each inverter frame,
finding the size of each register table (exception 6 marks a table start
read with the wrong count), and reading registers one by one at the edges
of readable ranges. It then compares every register
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


def receive(sock, seconds, buffer=b"", until_frame=False, collected=None):
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
        if collected is not None:
            collected += frames
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
SCAN_CHUNK = 10
SCAN_GAP_SECONDS = 18  # stop sending this long after an inverter frame
SCAN_REPLY_SECONDS = 3
SCAN_REQUEST_PAUSE = 0.3  # between requests, so the inverter keeps up
MAX_COUNT = 125  # Modbus limit for one read
DATA_FRAME_WAIT = 100  # the 0x02 frame comes every 90 seconds
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


def run_requests(sock, buffer, queue, function, data_frames, label, on_result=None):
    """Send the (start, count) reads in queue, only in the quiet gaps.

    Returns the buffer and {(start, count): result}, where result is a list of
    register values, "exception N" or "no reply".
    """
    results = {}
    attempts = {}
    total = len(queue)
    reported = 0
    while queue:
        frames, buffer = read_frames(sock, buffer, 45, stop=is_push)
        data_frames += [f for f in frames if f[2] == 0x02 and crc_ok(f)]
        if not any(is_push(f) for f in frames):
            print("  no inverter frame in 45s, is it daylight?")
            continue
        gap_start = time.monotonic()
        time.sleep(SEND_DELAY_SECONDS)
        while queue and time.monotonic() - gap_start < SCAN_GAP_SECONDS:
            block = queue.pop(0)
            register, count = block
            sock.sendall(build_request(*S_SERIES_MARKERS, function, register, count))
            frames, buffer = read_frames(sock, buffer, SCAN_REPLY_SECONDS, stop=lambda f: f[2] == 0x91)
            data_frames += [f for f in frames if f[2] == 0x02 and crc_ok(f)]
            replies = [f for f in frames if f[2] == 0x91 and crc_ok(f)]
            data = replies[-1][9:-4] if replies else b""
            tries = attempts.get(block, 0)
            if not replies:
                if tries < 1:
                    attempts[block] = tries + 1
                    queue.append(block)
                else:
                    results[block] = "no reply"
            elif len(data) >= 3 and data[1] == function:
                results[block] = [int.from_bytes(data[3 + i:5 + i], "big") for i in range(0, data[2], 2)]
                nonzero = sum(1 for v in results[block] if v)
                print(f"  {register}+{count}: ok, {nonzero} non-zero")
            elif len(data) >= 3 and data[1] & 0x80:
                # Exception 6 comes back for valid table starts read with the
                # wrong count, so it is a result, not a reason to retry.
                results[block] = f"exception {data[2]}"
            if block in results and on_result is not None:
                on_result(block, results[block], queue)
            if len(results) - reported >= 20 or not queue:
                reported = len(results)
                print(f"  {label}: {len(results)}/{total} done")
            time.sleep(SCAN_REQUEST_PAUSE)
            if any(is_push(f) for f in frames):
                break  # the inverter started its next frame; wait for the next gap
    return buffer, results


def find_table_sizes(sock, buffer, starts, function, data_frames, results):
    """Try counts 1..MAX_COUNT at each table start until one is accepted."""
    sizes = {}
    print(f"\n  Finding the size of {len(starts)} tables at {', '.join(map(str, starts))} "
          f"(trying counts 1-{MAX_COUNT}):")

    def on_result(block, result, queue):
        register, count = block
        if isinstance(result, list):
            sizes[register] = max(sizes.get(register, 0), count)
            # Also check whether a longer read is accepted.
            if count < MAX_COUNT and (register, count + 1) not in results:
                queue.insert(0, (register, count + 1))
            return
        if result == "exception 6" and register not in sizes and count < MAX_COUNT:
            queue.insert(0, (register, count + 1))
        elif result != "exception 6":
            print(f"  {register}: {result} at count {count}, giving up on this table")

    queue = []
    for register in starts:
        count = 1
        while (register, count) in results and count < MAX_COUNT:
            count += 1  # skip counts already tried by the block scan
        queue.append((register, count))
    buffer, found = run_requests(sock, buffer, queue, function, data_frames, "size probes", on_result)
    results.update(found)
    for register, count in sizes.items():
        print(f"  table {register}: size {count}")
    return buffer, sizes


def scan(sock, buffer, start, end, function, data_frames):
    blocks = [(a, min(SCAN_CHUNK, end - a)) for a in range(start, end, SCAN_CHUNK)]
    print(f"\n2) Scanning registers {start}-{end - 1} with function 0x{function:02x} in "
          f"{len(blocks)} blocks of {SCAN_CHUNK} (only in the quiet gap after inverter frames):")
    buffer, results = run_requests(sock, buffer, list(blocks), function, data_frames, "blocks")

    # A block is refused as a whole if any of its registers is missing, so read
    # registers one at a time where a refused block touches a readable one.
    ok = {b for b, r in results.items() if isinstance(r, list)}
    singles = []
    for index, block in enumerate(blocks):
        if block in ok or results.get(block) == "no reply":
            continue
        neighbours = blocks[max(0, index - 1):index] + blocks[index + 1:index + 2]
        if any(n in ok for n in neighbours):
            singles += [(block[0] + i, 1) for i in range(block[1])]
    if singles:
        print(f"\n  Reading {len(singles)} registers one by one at the edges of readable ranges:")
        buffer, single_results = run_requests(sock, buffer, singles, function, data_frames, "registers")
        results.update(single_results)

    # The inverter keeps its values in fixed-size tables: a read must start at a
    # table start with exactly its size. Exception 6 marks a table start read
    # with the wrong count, so find the size by trying counts.
    starts = sorted(b[0] for b, r in results.items() if r == "exception 6")
    if starts:
        buffer, sizes = find_table_sizes(sock, buffer, starts, function, data_frames, results)
        print("\n  Tables: " + (", ".join(f"{a}+{n}" for a, n in sorted(sizes.items())) or "none sized"))

    registers = {}
    for (register, _), result in results.items():
        if isinstance(result, list):
            registers.update({register + i: v for i, v in enumerate(result)})

    if not data_frames:
        print(f"\n  Waiting up to {DATA_FRAME_WAIT}s for a 0x02 data frame to compare with:")
        frames, buffer = read_frames(sock, buffer, DATA_FRAME_WAIT, stop=lambda f: f[2] == 0x02)
        data_frames += [f for f in frames if f[2] == 0x02 and crc_ok(f)]
    return buffer, registers, results


def signed(value):
    return value - 0x10000 if value & 0x8000 else value


def ranges(addresses):
    """Group sorted addresses into (first, last) runs."""
    runs = []
    for address in sorted(addresses):
        if runs and address == runs[-1][1] + 1:
            runs[-1][1] = address
        else:
            runs.append([address, address])
    return runs


def report(registers, results, data_frames, payload_version, path):
    print("\n3) Results")
    readable = sorted(registers)
    if readable:
        text = ", ".join(f"{a}-{b} ({b - a + 1})" if a != b else str(a) for a, b in ranges(readable))
        print(f"  Readable registers: {text}")
        print("  Non-zero values:")
        for address in readable:
            value = registers[address]
            if value:
                print(f"    {address}: {value:6d}  0x{value:04x}  signed {signed(value):6d}")
    else:
        print("  No readable registers in this range.")
    counts = {}
    for result in results.values():
        if not isinstance(result, list):
            counts[result] = counts.get(result, 0) + 1
    if counts:
        print("  Refused reads: " + ", ".join(f"{n}x {kind}" for kind, n in sorted(counts.items())))

    matches = {}
    if data_frames and readable:
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
    elif not data_frames:
        print("\n  No 0x02 data frame was captured, so nothing to match against.")

    with open(path, "w") as file:
        json.dump({
            "registers": {str(k): v for k, v in sorted(registers.items())},
            "refused": [{"start": b[0], "count": b[1], "result": r}
                        for b, r in sorted(results.items()) if not isinstance(r, list)],
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
                        help="seconds to listen first (default 600, 40 with --send, 0 with --scan)")
    parser.add_argument("--send", action="store_true", help="also send read requests after listening")
    parser.add_argument("--scan", type=parse_range, metavar="START-END",
                        help="read this input register range and match it with the data frame")
    parser.add_argument("--tables", type=lambda v: [int(x) for x in v.split(",") if x.strip()],
                        metavar="START,START,...",
                        help="skip the block scan and only find the size of tables at these starts")
    parser.add_argument("--function", type=lambda v: int(v, 0), default=0x04,
                        help="Modbus read function for --scan (default 0x04)")
    parser.add_argument("--payload-version", type=int, default=0, choices=(0, 1))
    args = parser.parse_args()
    if args.listen is None:
        args.listen = 0 if (args.scan or args.tables) else SEND_LISTEN_SECONDS if args.send else LISTEN_SECONDS

    print(f"Connecting to {args.host}:{args.port} ...")
    sock = socket.create_connection((args.host, args.port), timeout=10)
    print("Connected.\n")

    listened = []
    buffer = b""
    if args.listen:
        then = ", then sending read requests" if (args.send or args.scan) else " (nothing is sent)"
        print(f"1) Listening for {args.listen}s to everything on the bus{then}:")
        buffer = receive(sock, args.listen, collected=listened)

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
        data_frames = [f for f in listened if f[2] == 0x02 and crc_ok(f)]
        buffer, registers, results = scan(sock, buffer, start, end, args.function, data_frames)
        report(registers, results, data_frames, args.payload_version,
               f"foxess_scan_{start}-{end - 1}_{time.strftime('%Y%m%d-%H%M%S')}.json")

    if args.tables:
        data_frames = [f for f in listened if f[2] == 0x02 and crc_ok(f)]
        results = {}
        buffer, sizes = find_table_sizes(sock, buffer, sorted(set(args.tables)), args.function,
                                         data_frames, results)
        registers = {}
        for (register, _), result in results.items():
            if isinstance(result, list):
                registers.update({register + i: v for i, v in enumerate(result)})
        if not data_frames:
            print(f"\n  Waiting up to {DATA_FRAME_WAIT}s for a 0x02 data frame to compare with:")
            frames, buffer = read_frames(sock, buffer, DATA_FRAME_WAIT, stop=lambda f: f[2] == 0x02)
            data_frames += [f for f in frames if f[2] == 0x02 and crc_ok(f)]
        report(registers, results, data_frames, args.payload_version,
               f"foxess_tables_{time.strftime('%Y%m%d-%H%M%S')}.json")

    sock.close()
    print("\nDone. Frames with function 0x11 are requests (e.g. from the factory dongle);")
    print("REPLY lines are the inverter's answers.")


if __name__ == "__main__":
    main()
