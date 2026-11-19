#!/usr/bin/env python3
"""Sniff the inverter's RS485 traffic, and optionally test register reads.

Usage (needs Python 3.8+, no extra packages):

    python3 tools/foxess_probe.py <EW11 IP> [--port 502] [--listen SECONDS] [--send]

By default it only LISTENS (10 minutes) and prints every frame on the bus,
including the factory Wi-Fi dongle's requests and the inverter's replies if
the dongle is connected in parallel. Listening never transmits anything.

--send also transmits a few Modbus "read registers" requests (function 0x04
or 0x03) after listening. These cannot change inverter settings. Each one is
sent 2 seconds after an inverter frame, in the quiet part of its 30-second
cycle, so it is unlikely to collide with traffic on a shared bus.

Run it during daylight, while the inverter is on, and paste the whole output
into the GitHub issue.
"""
import argparse
import socket
import time

LISTEN_SECONDS = 600
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
        text += f"\n      INFO: {[part.decode('ascii', 'replace') for part in frame[9:-4].split(b'\0') if part]}"
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


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("host", help="IP address of the RS485-to-TCP bridge (e.g. the EW11)")
    parser.add_argument("--port", type=int, default=502)
    parser.add_argument("--listen", type=int, default=LISTEN_SECONDS, help="seconds to listen (default 600)")
    parser.add_argument("--send", action="store_true", help="also send read requests after listening")
    args = parser.parse_args()

    print(f"Connecting to {args.host}:{args.port} ...")
    sock = socket.create_connection((args.host, args.port), timeout=10)
    print("Connected.\n")

    print(f"1) Listening for {args.listen}s to everything on the bus (nothing is sent):")
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

    sock.close()
    print("\nDone. Frames with function 0x11 are requests (e.g. from the factory dongle);")
    print("REPLY lines are the inverter's answers.")


if __name__ == "__main__":
    main()
