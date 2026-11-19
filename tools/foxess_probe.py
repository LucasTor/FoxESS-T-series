#!/usr/bin/env python3
"""Check whether a FoxESS inverter answers register read requests.

Read-only: it only sends Modbus "read registers" requests (function 0x04 or
0x03), which cannot change any inverter setting.

Usage (needs Python 3.8+, no extra packages):

    python3 tools/foxess_probe.py <EW11 IP address> [port]

Run it during daylight, while the inverter is on. It takes about 2 minutes:
1. it listens for 70 seconds and prints every frame the inverter sends;
2. it sends a few read requests in the format captured from an S-series
   inverter (issue #1) and prints any replies.

Paste the whole output into the GitHub issue.
"""
import socket
import sys
import time

LISTEN_SECONDS = 70
REPLY_WAIT_SECONDS = 6
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


def receive(sock, seconds, buffer=b""):
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
    return buffer


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    host = sys.argv[1]
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 502

    print(f"Connecting to {host}:{port} ...")
    sock = socket.create_connection((host, port), timeout=10)
    print("Connected.\n")

    print(f"1) Listening for {LISTEN_SECONDS}s to everything the inverter sends:")
    buffer = receive(sock, LISTEN_SECONDS)

    print("\n2) Sending read requests:")
    for name, (start_marker, end_marker) in MARKERS.items():
        for function, register, count in READS:
            request = build_request(start_marker, end_marker, function, register, count)
            print(f"\n  -> {name}, Modbus function 0x{function:02x}, registers {register}+{count}")
            print(f"      sent: {request.hex()}")
            sock.sendall(request)
            buffer = receive(sock, REPLY_WAIT_SECONDS, buffer)

    sock.close()
    print("\nDone. A line starting with REPLY means the inverter answered.")


if __name__ == "__main__":
    main()
