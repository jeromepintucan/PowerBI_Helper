"""
Minimal WebSocket client (RFC 6455), standard library only.

Just enough to talk to Microsoft Edge's DevTools endpoint on 127.0.0.1:
plain ws:// (no TLS), text messages, fragmented frames, ping/pong, close,
and messages of any size (DevTools responses can be several MB). Written
in-house so this app needs no extra packages beyond pywebview - the
alternative (Playwright) adds ~100 MB to the packaged .exe.
"""
from __future__ import annotations

import base64
import hashlib
import os
import socket
import struct
import threading
import urllib.parse

_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


class WebSocketClosed(Exception):
    pass


def _mask(data: bytes, key: bytes) -> bytes:
    n = len(data)
    if n == 0:
        return data
    k = (key * (n // 4 + 1))[:n]
    return (int.from_bytes(data, "big") ^ int.from_bytes(k, "big")).to_bytes(n, "big")


class WebSocket:
    def __init__(self, url: str, timeout: float = 10.0):
        u = urllib.parse.urlparse(url)
        if u.scheme != "ws":
            raise ValueError(f"Only ws:// URLs are supported, got: {url}")
        host = u.hostname or "127.0.0.1"
        port = u.port or 80
        path = (u.path or "/") + (f"?{u.query}" if u.query else "")

        self._sock = socket.create_connection((host, port), timeout=timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        request = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        )
        self._sock.sendall(request.encode("ascii"))

        self._buf = b""
        while b"\r\n\r\n" not in self._buf:
            chunk = self._sock.recv(4096)
            if not chunk:
                raise ConnectionError("DevTools closed the connection during the WebSocket handshake")
            self._buf += chunk
        head, self._buf = self._buf.split(b"\r\n\r\n", 1)
        lines = head.decode("latin-1").split("\r\n")
        if len(lines[0].split()) < 2 or lines[0].split()[1] != "101":
            raise ConnectionError(f"WebSocket handshake refused: {lines[0]}")
        headers = {}
        for line in lines[1:]:
            if ":" in line:
                k, v = line.split(":", 1)
                headers[k.strip().lower()] = v.strip()
        expected = base64.b64encode(hashlib.sha1((key + _GUID).encode()).digest()).decode()
        if headers.get("sec-websocket-accept") != expected:
            raise ConnectionError("WebSocket handshake failed: bad Sec-WebSocket-Accept")

        self._sock.settimeout(None)
        self._send_lock = threading.Lock()
        self.closed = False

    # ---- low level ----
    def _recv_exact(self, n: int) -> bytes:
        while len(self._buf) < n:
            chunk = self._sock.recv(max(65536, n - len(self._buf)))
            if not chunk:
                self.closed = True
                raise WebSocketClosed("connection closed")
            self._buf += chunk
        out, self._buf = self._buf[:n], self._buf[n:]
        return out

    def _send_frame(self, opcode: int, payload: bytes) -> None:
        header = bytearray([0x80 | opcode])
        n = len(payload)
        if n < 126:
            header.append(0x80 | n)
        elif n < 65536:
            header.append(0x80 | 126)
            header += struct.pack("!H", n)
        else:
            header.append(0x80 | 127)
            header += struct.pack("!Q", n)
        key = os.urandom(4)
        with self._send_lock:
            self._sock.sendall(bytes(header) + key + _mask(payload, key))

    # ---- public ----
    def send(self, text: str) -> None:
        if self.closed:
            raise WebSocketClosed("connection closed")
        self._send_frame(0x1, text.encode("utf-8"))

    def recv(self) -> str:
        """Blocks until one complete text message arrives."""
        parts: list[bytes] = []
        while True:
            b1, b2 = self._recv_exact(2)
            fin, opcode = b1 & 0x80, b1 & 0x0F
            masked, n = b2 & 0x80, b2 & 0x7F
            if n == 126:
                n = struct.unpack("!H", self._recv_exact(2))[0]
            elif n == 127:
                n = struct.unpack("!Q", self._recv_exact(8))[0]
            key = self._recv_exact(4) if masked else None
            payload = self._recv_exact(n) if n else b""
            if key:
                payload = _mask(payload, key)

            if opcode == 0x8:  # close
                self.closed = True
                try:
                    self._send_frame(0x8, b"")
                except OSError:
                    pass
                raise WebSocketClosed("closed by DevTools")
            if opcode == 0x9:  # ping
                self._send_frame(0xA, payload)
                continue
            if opcode == 0xA:  # pong
                continue
            parts.append(payload)
            if fin:
                return b"".join(parts).decode("utf-8", errors="replace")

    def close(self) -> None:
        if not self.closed:
            self.closed = True
            try:
                self._send_frame(0x8, b"")
            except OSError:
                pass
        try:
            self._sock.close()
        except OSError:
            pass
