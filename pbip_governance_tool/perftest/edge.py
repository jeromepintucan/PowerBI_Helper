"""
Find, launch and connect to Microsoft Edge with its DevTools port open.

Edge runs with its OWN profile folder (edge_profile\\ next to the app), not
your everyday Edge profile: DevTools can't attach to an Edge that's
already running with your normal profile, and keeping them separate means
the test never touches your tabs, history or extensions. You sign in to
Power BI once in that profile; it stays signed in for later runs.
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from typing import Optional


class EdgeError(Exception):
    pass


def find_edge() -> Optional[str]:
    candidates = []
    for env in ("ProgramFiles(x86)", "ProgramFiles", "LocalAppData"):
        base = os.environ.get(env)
        if base:
            candidates.append(os.path.join(base, "Microsoft", "Edge", "Application", "msedge.exe"))
    if sys.platform.startswith("win"):
        try:
            import winreg
            for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
                try:
                    with winreg.OpenKey(hive, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\msedge.exe") as k:
                        candidates.insert(0, winreg.QueryValue(k, None))
                except OSError:
                    pass
        except ImportError:
            pass
    for c in candidates:
        if c and os.path.isfile(c):
            return c
    return None


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _http_json(url: str, method: str = "GET", timeout: float = 3.0):
    req = urllib.request.Request(url, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _devtools_port_file(profile_dir: str) -> str:
    # Chromium/Edge writes this when started with --remote-debugging-port:
    # line 1 = the actual port, line 2 = browser websocket path.
    return os.path.join(profile_dir, "DevToolsActivePort")


def existing_devtools_port(profile_dir: str) -> Optional[int]:
    """Port of an Edge already running on this profile with DevTools open
    (e.g. left over from a previous test), or None."""
    try:
        with open(_devtools_port_file(profile_dir), encoding="utf-8") as f:
            port = int(f.readline().strip())
        _http_json(f"http://127.0.0.1:{port}/json/version", timeout=2)
        return port
    except Exception:
        return None


def _no_window_flags() -> int:
    return 0x08000000 if sys.platform.startswith("win") else 0  # CREATE_NO_WINDOW


def kill_profile_edge(profile_dir: str) -> int:
    """Closes any Edge processes using THIS app's profile folder (never your
    normal Edge). Needed because an Edge already running on the profile
    without DevTools makes a new launch hand over to it and ignore the
    DevTools port - which is what produced 'connection refused'."""
    if not sys.platform.startswith("win"):
        return 0
    needle = os.path.abspath(profile_dir).replace("'", "''")
    ps = ("$n = '" + needle + "'; $k = 0; "
          "Get-CimInstance Win32_Process -Filter \"Name='msedge.exe'\" | "
          "Where-Object { $_.CommandLine -and $_.CommandLine.Contains($n) } | "
          "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue; $k++ }; $k")
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                             capture_output=True, text=True, timeout=30, creationflags=_no_window_flags())
        return int((out.stdout or "0").strip().splitlines()[-1] or 0)
    except Exception:
        return 0


def start_edge(profile_dir: str, timeout: float = 30.0, log=lambda m: None) -> tuple[Optional[subprocess.Popen], int]:
    """Returns (process or None if reused, DevTools port)."""
    port = existing_devtools_port(profile_dir)
    if port:
        log(f"Reusing the Edge window already open on the test profile (port {port}).")
        return None, port

    exe = find_edge()
    if not exe:
        raise EdgeError("Microsoft Edge wasn't found on this PC (looked in Program Files and the registry).")
    os.makedirs(profile_dir, exist_ok=True)
    killed = kill_profile_edge(profile_dir)
    if killed:
        log(f"Closed {killed} leftover Edge process(es) on the test profile.")
        time.sleep(1.5)
    try:
        os.remove(_devtools_port_file(profile_dir))
    except OSError:
        pass

    args = [exe, "--remote-debugging-port=0", f"--user-data-dir={profile_dir}",
            "--no-first-run", "--no-default-browser-check", "--new-window", "about:blank"]
    proc = subprocess.Popen(args, creationflags=_no_window_flags())

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        port = existing_devtools_port(profile_dir)
        if port:
            return proc, port
        time.sleep(0.5)
    raise EdgeError(
        "Edge opened, but its DevTools connection never became available. Most likely an Edge "
        "window from an earlier test is still open on the tester's profile - close every Edge window "
        "that opened from this app (or end 'Microsoft Edge' in Task Manager) and try again. If it still "
        "happens, your organisation may block remote debugging in Edge (check edge://policy for "
        "'RemoteDebuggingAllowed')."
    )


def close_edge(port: Optional[int], proc: Optional[subprocess.Popen], profile_dir: Optional[str]) -> None:
    """Closes the test Edge completely (all its processes), so the next test
    starts clean."""
    if port:
        try:
            from .cdp import CDP
            info = _http_json(f"http://127.0.0.1:{port}/json/version", timeout=2)
            browser = CDP(info["webSocketDebuggerUrl"])
            browser.send_nowait("Browser.close")
            time.sleep(1.0)
            browser.close()
        except Exception:
            pass
    if proc and proc.poll() is None:
        try:
            proc.terminate()
        except Exception:
            pass
    if profile_dir:
        time.sleep(0.5)
        if existing_devtools_port(profile_dir) is None:
            kill_profile_edge(profile_dir)   # stragglers (background processes), this profile only


def page_websocket_url(port: int) -> str:
    """WebSocket URL of a normal tab in that Edge window (opens one if needed)."""
    for _ in range(20):
        try:
            targets = _http_json(f"http://127.0.0.1:{port}/json/list")
        except Exception:
            targets = []
        for t in targets:
            if t.get("type") == "page" and t.get("webSocketDebuggerUrl"):
                return t["webSocketDebuggerUrl"]
        try:  # newer Chromium requires PUT for /json/new
            t = _http_json(f"http://127.0.0.1:{port}/json/new?about:blank", method="PUT")
            if t.get("webSocketDebuggerUrl"):
                return t["webSocketDebuggerUrl"]
        except Exception:
            pass
        time.sleep(0.5)
    raise EdgeError("Couldn't find a tab to control in the Edge window.")
