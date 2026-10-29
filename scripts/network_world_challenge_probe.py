"""Observe the local 3.3.5 world challenge without logging in or sending data."""

import argparse
import datetime
import hashlib
import json
import socket
import struct
import time
from pathlib import Path


def recv_exact(sock, size):
    data = bytearray()
    while len(data) < size:
        chunk = sock.recv(size - len(data))
        if not chunk:
            raise EOFError(f"peer closed after {len(data)}/{size} bytes")
        data.extend(chunk)
    return bytes(data)


def probe():
    result = {
        "probe_id": "local_world_auth_challenge_v1",
        "observed_at_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "endpoint": "127.0.0.1:8085",
        "scope": "unauthenticated_server_greeting_only",
        "application_bytes_sent": 0,
        "authenticated": False,
        "entered_world": False,
    }
    start = time.perf_counter()
    try:
        with socket.create_connection(("127.0.0.1", 8085), timeout=5) as sock:
            sock.settimeout(5)
            # 3.3.5 server framing: big-endian size, little-endian opcode.
            prefix = recv_exact(sock, 2)
            if prefix[0] & 0x80:
                raise ValueError("unexpected extended header for initial challenge")
            size = int.from_bytes(prefix, "big")
            if not 2 <= size <= 4096:
                raise ValueError(f"unexpected initial packet size: {size}")
            opcode_bytes = recv_exact(sock, 2)
            opcode = int.from_bytes(opcode_bytes, "little")
            payload = recv_exact(sock, size - 2)
            result.update({
                "packet_size_field": size,
                "opcode": f"0x{opcode:03X}",
                "payload_bytes": len(payload),
                "frame_bytes": size + 2,
                "frame_sha256": hashlib.sha256(prefix + opcode_bytes + payload).hexdigest(),
                "challenge_version": struct.unpack_from("<I", payload)[0] if len(payload) >= 4 else None,
            })
            result["status"] = "PASS" if opcode == 0x1EC and len(payload) == 40 and result["challenge_version"] == 1 else "UNEXPECTED_PACKET"
    except (OSError, EOFError, ValueError) as exc:
        result.update(status="ERROR", error=f"{type(exc).__name__}: {exc}")
    result["elapsed_ms"] = round((time.perf_counter() - start) * 1000, 3)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = probe()
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(text + "\n", encoding="utf-8")
    print(text)
    raise SystemExit(0 if result["status"] == "PASS" else 1)
