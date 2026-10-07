"""
Chrome DevTools Protocol (CDP) connection - the same protocol Edge's own F12
DevTools uses to show the Network tab. Commands are request/response
(matched by id); everything else arrives as events, delivered to listeners
on a background reader thread.

Supports "flattened" child sessions (Target.setAutoAttach flatten=true), so
network traffic from workers / out-of-process frames is captured too - the
same requests DevTools would list for the page.
"""
from __future__ import annotations

import json
import threading
from typing import Callable, Optional

from .ws import WebSocket


class CDPError(Exception):
    pass


Listener = Callable[[str, dict, Optional[str]], None]  # (method, params, session_id)


class CDP:
    def __init__(self, ws_url: str):
        self._ws = WebSocket(ws_url)
        self._next_id = 0
        self._lock = threading.Lock()
        self._pending: dict[int, dict] = {}
        self._listeners: list[Listener] = []
        self.closed = False
        self.close_reason = ""
        self._reader = threading.Thread(target=self._loop, name="cdp-reader", daemon=True)
        self._reader.start()

    def _loop(self) -> None:
        try:
            while True:
                msg = json.loads(self._ws.recv())
                if "id" in msg:
                    with self._lock:
                        slot = self._pending.pop(msg["id"], None)
                    if slot is not None:
                        slot["msg"] = msg
                        slot["event"].set()
                    continue
                method = msg.get("method")
                if not method:
                    continue
                for cb in list(self._listeners):
                    try:
                        cb(method, msg.get("params") or {}, msg.get("sessionId"))
                    except Exception:
                        pass
        except Exception as e:  # socket closed, Edge exited, etc.
            self.closed = True
            self.close_reason = str(e) or e.__class__.__name__
            with self._lock:
                pending, self._pending = self._pending, {}
            for slot in pending.values():
                slot["msg"] = {"error": {"message": f"DevTools connection closed: {self.close_reason}"}}
                slot["event"].set()

    def add_listener(self, cb: Listener) -> None:
        self._listeners.append(cb)

    def call(self, method: str, params: Optional[dict] = None, session_id: Optional[str] = None,
             timeout: float = 30.0) -> dict:
        if self.closed:
            raise CDPError(f"DevTools connection closed: {self.close_reason}")
        with self._lock:
            self._next_id += 1
            mid = self._next_id
            slot = {"event": threading.Event(), "msg": None}
            self._pending[mid] = slot
        msg = {"id": mid, "method": method, "params": params or {}}
        if session_id:
            msg["sessionId"] = session_id
        self._ws.send(json.dumps(msg))
        if not slot["event"].wait(timeout):
            with self._lock:
                self._pending.pop(mid, None)
            raise CDPError(f"{method} timed out after {timeout:.0f}s")
        reply = slot["msg"] or {}
        if "error" in reply:
            raise CDPError(f"{method}: {reply['error'].get('message', reply['error'])}")
        return reply.get("result") or {}

    def send_nowait(self, method: str, params: Optional[dict] = None, session_id: Optional[str] = None) -> None:
        """Fire-and-forget - safe to call from inside a listener (the reader
        thread can't block waiting on its own reply)."""
        if self.closed:
            return
        with self._lock:
            self._next_id += 1
            mid = self._next_id
        msg = {"id": mid, "method": method, "params": params or {}}
        if session_id:
            msg["sessionId"] = session_id
        try:
            self._ws.send(json.dumps(msg))
        except Exception:
            pass

    def close(self) -> None:
        self._ws.close()
