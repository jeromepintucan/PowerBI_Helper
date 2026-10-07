"""
Test double for Microsoft Edge's DevTools endpoint + a Power BI report.

Speaks the real wire protocols (HTTP /json/version + /json/list, RFC 6455
WebSocket, CDP JSON messages) so the app's actual ws/cdp/runner code is
exercised end to end - only the browser itself is simulated. Each
Page.navigate replays a scripted set of network events with real delays.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import socket
import struct
import threading
import time
import urllib.parse

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
QUERY_HOST = "https://wabi-west-europe-redirect.analysis.windows.net"

# page id -> (display name, hidden, [(start offset s, duration s, via_worker)], redirect_to)
PAGES = {
    "aaaaaaaaaaaaaaaaaaaa": ("Benefit affordability", False, [(0.6, 0.3, False), (0.7, 0.6, False), (0.8, 0.9, True)], None),
    "bbbbbbbbbbbbbbbbbbbb": ("DC", False, [(0.6, 1.2, False), (0.65, 0.4, False)], None),
    "cccccccccccccccccccc": ("Info", True, [], None),
    "dddddddddddddddddddd": ("Drillthrough detail", True, [(0.6, 0.2, False)], "aaaaaaaaaaaaaaaaaaaa"),
}


class MockDevTools:
    def __init__(self, signin: str = "delayed", big_body_kb: int = 300, router: str = "push"):
        """signin: 'none' | 'delayed' (login page, user signs in 1s later) | 'instant' (SSO bounce).
        router: how the simulated Power BI app reacts to an in-report page switch -
        'push' (URL change + popstate works), 'click' (only a Pages-pane click works), 'none'."""
        self.router = router
        self.switches: list[tuple[str, str]] = []
        self.signin_mode = signin
        self.signed_in = signin == "none"
        self.big_body_kb = big_body_kb
        self.sock = socket.socket()
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(5)
        self.port = self.sock.getsockname()[1]
        self.navigations: list[str] = []
        self._rid = 0
        self._send_lock = threading.Lock()
        self.conn = None
        self.href = "about:blank"
        threading.Thread(target=self._accept, daemon=True).start()

    # ---------- HTTP + handshake ----------
    def _accept(self):
        while True:
            c, _ = self.sock.accept()
            threading.Thread(target=self._handle, args=(c,), daemon=True).start()

    def _handle(self, c):
        data = b""
        while b"\r\n\r\n" not in data:
            chunk = c.recv(4096)
            if not chunk:
                return
            data += chunk
        head = data.split(b"\r\n\r\n", 1)[0].decode()
        line = head.split("\r\n")[0]
        path = line.split()[1]
        headers = {k.strip().lower(): v.strip() for k, v in (l.split(":", 1) for l in head.split("\r\n")[1:] if ":" in l)}
        if headers.get("upgrade", "").lower() == "websocket":
            accept = base64.b64encode(hashlib.sha1((headers["sec-websocket-key"] + GUID).encode()).digest()).decode()
            c.sendall(("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                       f"Sec-WebSocket-Accept: {accept}\r\n\r\n").encode())
            self.conn = c
            self._ws_loop(c)
            return
        ws = f"ws://127.0.0.1:{self.port}/devtools/page/1"
        if path.startswith("/json/version"):
            body = {"Browser": "MockEdge/1.0", "webSocketDebuggerUrl": f"ws://127.0.0.1:{self.port}/devtools/browser/x"}
        elif path.startswith("/json/list"):
            body = [{"type": "page", "url": "about:blank", "webSocketDebuggerUrl": ws}]
        else:
            body = {}
        raw = json.dumps(body).encode()
        c.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: "
                  + str(len(raw)).encode() + b"\r\n\r\n" + raw)
        c.close()

    # ---------- websocket framing ----------
    @staticmethod
    def _recv_exact(c, n):
        buf = b""
        while len(buf) < n:
            chunk = c.recv(n - len(buf))
            if not chunk:
                raise ConnectionError
            buf += chunk
        return buf

    def _ws_loop(self, c):
        try:
            while True:
                b1, b2 = self._recv_exact(c, 2)
                n = b2 & 0x7F
                if n == 126:
                    n = struct.unpack("!H", self._recv_exact(c, 2))[0]
                elif n == 127:
                    n = struct.unpack("!Q", self._recv_exact(c, 8))[0]
                assert b2 & 0x80, "client frames must be masked"
                key = self._recv_exact(c, 4)
                payload = bytes(b ^ key[i % 4] for i, b in enumerate(self._recv_exact(c, n)))
                if b1 & 0x0F == 0x8:
                    return
                if b1 & 0x0F == 0x1:
                    self._on_message(json.loads(payload))
        except (ConnectionError, OSError):
            return

    def _frame(self, opcode, payload, fin=True):
        n = len(payload)
        h = bytearray([(0x80 if fin else 0) | opcode])
        if n < 126:
            h.append(n)
        elif n < 65536:
            h.append(126); h += struct.pack("!H", n)
        else:
            h.append(127); h += struct.pack("!Q", n)
        return bytes(h) + payload

    def send(self, obj, fragment=False):
        data = json.dumps(obj).encode()
        with self._send_lock:
            if fragment and len(data) > 1000:
                third = len(data) // 3
                self.conn.sendall(self._frame(0x9, b"ping"))                  # ping mid-stream
                self.conn.sendall(self._frame(0x1, data[:third], fin=False))
                self.conn.sendall(self._frame(0x0, data[third:2 * third], fin=False))
                self.conn.sendall(self._frame(0x0, data[2 * third:], fin=True))
            else:
                self.conn.sendall(self._frame(0x1, data))

    def event(self, method, params, session=None):
        msg = {"method": method, "params": params}
        if session:
            msg["sessionId"] = session
        try:
            self.send(msg)
        except OSError:
            pass   # client already disconnected (end of a test)

    # ---------- CDP ----------
    def _on_message(self, msg):
        mid, method, params = msg.get("id"), msg.get("method"), msg.get("params") or {}
        result = {}
        if method == "Page.navigate":
            self.navigations.append(params["url"])
            result = {"frameId": "MAIN", "loaderId": f"L{len(self.navigations)}"}
            threading.Thread(target=self._play, args=(params["url"],), daemon=True).start()
        elif method == "Network.getResponseBody":
            sections = [{"name": pid, "displayName": v[0], "ordinal": i,
                         "config": json.dumps({"visibility": 1} if v[1] else {})} for i, (pid, v) in enumerate(PAGES.items())]
            body = json.dumps({"models": [{"id": 1}], "exploration": {"sections": sections,
                               "filler": "x" * (self.big_body_kb * 1024)}})
            self.send({"id": mid, "result": {"body": base64.b64encode(body.encode()).decode(), "base64Encoded": True}},
                      fragment=True)
            return
        elif method == "Runtime.evaluate":
            expr = params.get("expression", "")
            import re
            arg = re.search(r'\)\((".*")\)\s*$', expr)
            arg = json.loads(arg.group(1)) if arg else None
            if "pushState" in expr:
                self.switches.append(("push", arg))
                base = self.href.split("?")[0].rsplit("/", 1)[0]
                self.href = f"{base}/{arg}?experience=power-bi"
                if self.router == "push":
                    threading.Thread(target=self._play_inapp, args=(arg,), daemon=True).start()
                value = "pushed"
            elif "not-found" in expr:   # Pages-pane click
                self.switches.append(("click", arg))
                pid = next((k for k, v in PAGES.items() if v[0].lower() == (arg or "").lower() and not v[1]), None)
                if self.router in ("push", "click") and pid:
                    base = self.href.split("?")[0].rsplit("/", 1)[0]
                    self.href = f"{base}/{pid}?experience=power-bi"
                    threading.Thread(target=self._play_inapp, args=(pid,), daemon=True).start()
                    value = "clicked"
                else:
                    value = "not-found"
            else:
                value = self.href
            result = {"result": {"type": "string", "value": value}}
        self.send({"id": mid, "result": result})

    def _req(self, rid, url, method, rtype, ts, session=None):
        self.event("Network.requestWillBeSent", {"requestId": rid, "loaderId": "L", "type": rtype, "timestamp": ts,
                                                  "wallTime": time.time() - (time.monotonic() - ts),
                                                  "request": {"url": url, "method": method}, "frameId": "MAIN"}, session)

    def _done(self, rid, ts, mime="application/json", rtype="XHR", session=None, failed=False):
        if not failed:
            self.event("Network.responseReceived", {"requestId": rid, "type": rtype, "timestamp": ts,
                                                     "response": {"mimeType": mime, "status": 200}}, session)
            self.event("Network.loadingFinished", {"requestId": rid, "timestamp": ts}, session)
        else:
            self.event("Network.loadingFailed", {"requestId": rid, "timestamp": ts, "canceled": True}, session)

    def _new_rid(self):
        self._rid += 1
        return f"R{self._rid}"

    def _play(self, url):
        t0 = time.monotonic()
        at = lambda off: (time.sleep(max(0, t0 + off - time.monotonic())), t0 + off)[1]
        doc = self._new_rid()
        self._req(doc, url, "GET", "Document", t0)
        if not self.signed_in:
            self.href = "https://login.microsoftonline.com/common/oauth2"
            self.event("Page.frameNavigated", {"frame": {"id": "MAIN", "url": self.href}})
            if self.signin_mode == "delayed":
                at(1.0)
            self.signed_in = True
            self.href = "https://app.powerbi.com/home"
            self.event("Page.frameNavigated", {"frame": {"id": "MAIN", "url": self.href}})
            return

        page = urllib.parse.urlparse(url).path.rstrip("/").split("/")[-1]
        name, hidden, queries, redirect = PAGES.get(page, ("?", False, [], None))
        self.href = url if not redirect else url.replace(page, redirect)
        self.event("Page.frameNavigated", {"frame": {"id": "MAIN", "url": url}})
        self._done(doc, at(0.15), mime="text/html", rtype="Document")

        meta = self._new_rid()
        self._req(meta, f"{QUERY_HOST}/explore/reports/R1/modelsAndExploration?preferReadOnlySession=true", "GET", "XHR", at(0.2))
        self._done(meta, at(0.35))
        self.event("Page.loadEventFired", {"timestamp": at(0.5)})

        # noise that must NOT count as queries
        schema = self._new_rid()
        self._req(schema, f"{QUERY_HOST}/explore/conceptualschema", "POST", "XHR", at(0.55))
        pre = self._new_rid()
        self._req(pre, f"{QUERY_HOST}/explore/querydata?synchronous=true", "OPTIONS", "Preflight", at(0.56))
        self._done(pre, at(0.57), rtype="Preflight")

        if any(w for _, _, w in queries):
            self.event("Target.attachedToTarget", {"sessionId": "W1", "targetInfo": {"type": "worker"}})

        timeline = []
        for i, (off, dur, worker) in enumerate(queries):
            rid = self._new_rid()
            path = "/explore/querydata?synchronous=true" if i % 2 == 0 else "/explore/query"
            timeline.append((off, "start", rid, path, worker))
            timeline.append((off + dur, "end", rid, path, worker))
        if page == "aaaaaaaaaaaaaaaaaaaa":   # one cancelled query - counted as failed, not as a result
            timeline.append((0.62, "start", "RX", "/explore/querydata", False))
            timeline.append((0.64, "fail", "RX", "/explore/querydata", False))
        timeline.append((2.5, "schema_end", schema, "", False))   # slow non-query xhr
        for off, kind, rid, path, worker in sorted(timeline):
            ts = at(off)
            sess = "W1" if worker else None
            if kind == "start":
                self._req(rid, QUERY_HOST + path, "POST", "Fetch" if worker else "XHR", ts, sess)
            elif kind == "end":
                self._done(rid, ts, rtype="Fetch" if worker else "XHR", session=sess)
            elif kind == "fail":
                self._done(rid, ts, failed=True)
            else:
                self._done(rid, ts)

    def _play_inapp(self, page):
        """Page switch inside an already-open report: no document, no
        metadata, no load event - just that page's queries (0.5s earlier
        than after a full load, since nothing has to start up)."""
        t0 = time.monotonic()
        at = lambda off: (time.sleep(max(0, t0 + off - time.monotonic())), t0 + off)[1]
        name, hidden, queries, redirect = PAGES.get(page, ("?", False, [], None))
        if redirect:
            base = self.href.split("?")[0].rsplit("/", 1)[0]
            self.href = f"{base}/{redirect}?experience=power-bi"
        timeline = []
        for i, (off, dur, worker) in enumerate(queries):
            rid = self._new_rid()
            timeline.append((off - 0.5, "start", rid, worker))
            timeline.append((off - 0.5 + dur, "end", rid, worker))
        for off, kind, rid, worker in sorted(timeline):
            ts = at(off)
            sess = "W1" if worker else None
            if kind == "start":
                self._req(rid, QUERY_HOST + "/explore/querydata?synchronous=true", "POST", "Fetch" if worker else "XHR", ts, sess)
            else:
                self._done(rid, ts, rtype="Fetch" if worker else "XHR", session=sess)
