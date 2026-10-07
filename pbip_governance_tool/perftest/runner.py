"""
Performance test runner.

Automates the manual check (open the page, DevTools > Network, type xhr,
sort by Time, find the "query" rows) for every page of a published report:

  1. Opens Edge (own profile, DevTools port open) and loads the report link
     once as a warm-up - this is where you sign in if needed, and where the
     page list is captured. The warm-up isn't counted.
  2. For each run: opens the report (not counted), then SWITCHES to every
     page inside the already-open report - the same thing a viewer does by
     clicking a page in the Pages pane. That's what the user's own manual
     check measures (1-2 s per page). The alternative, open_mode="link",
     reopens each page from its URL; every load then includes Power BI
     starting up from scratch (~13 s on the real DC dashboard), which hid
     the page's own time - kept only as an option.
  3. For each load, records - from the same DevTools network events the
     F12 Network tab uses - every visual query request (XHR/fetch whose URL
     path ends in /query or /querydata; CORS preflight OPTIONS rows are
     skipped), and waits until queries stop arriving.

Per load:
  page load time  = the page's SLOWEST visual query - the number you'd read
                    off the top of the Network tab sorted by Time. A page's
                    queries run in parallel, so the page has finished loading
                    when its slowest one finishes: queries of 0.5 s, 1 s and
                    1.5 s mean the page loaded in 1.5 s, not 3 s. (Agreed
                    with the user after comparing against their manual check;
                    an earlier "from navigation to last query" measure also
                    counted Power BI's own ~13 s start-up on every page.)
  query count
"""
from __future__ import annotations

import base64
import csv
import json
import os
import subprocess
import threading
import time
import urllib.parse
from dataclasses import dataclass, field
from typing import Callable, Optional

from .cdp import CDP, CDPError
from . import edge as edge_mod
from .pages import Page, ReportUrl, pages_from_metadata, pages_from_pbip, parse_report_url

_QUERY_PATH_ENDINGS = ("/query", "/querydata")
_LOGIN_HOSTS = ("login.microsoftonline.com", "login.microsoft.com", "login.windows.net", "login.live.com")
_METADATA_HINTS = ("exploration", "modelsandexploration", "/explore/reports/", "sections", "/reports/")

# Switch page the way the Power BI web app itself does: change the URL's
# page segment in place and tell the app's router (popstate) - no reload.
_SWITCH_PUSH_JS = r"""(function(pid){
  var u = new URL(location.href), parts = u.pathname.split('/'), i = parts.indexOf('reports');
  if (i < 0 || parts.length < i + 2) return 'no-report-url';
  var path = parts.slice(0, i + 2).concat([pid]).join('/');
  if (u.pathname === path) return 'same';
  history.pushState(history.state, '', path + u.search + u.hash);
  window.dispatchEvent(new PopStateEvent('popstate', {state: history.state}));
  return 'pushed';
})(%s)"""

# Fallback: click the page's name in the Pages pane (visible pages only).
_SWITCH_CLICK_JS = r"""(function(name){
  var want = name.trim().toLowerCase();
  function txt(el){ return ((el.getAttribute && el.getAttribute('aria-label')) || el.textContent || '').trim().toLowerCase(); }
  function vis(el){ return el.offsetParent !== null; }
  var panes = Array.prototype.slice.call(document.querySelectorAll('[aria-label*="page" i], [class*="pagesNav" i], [class*="page-navigation" i], [role="tablist"], [role="tree"]'));
  for (var p = 0; p < panes.length; p++) {
    var els = panes[p].querySelectorAll('[role="tab"], [role="treeitem"], [role="listitem"], [role="option"], button, a, li, span');
    for (var k = 0; k < els.length; k++) {
      var el = els[k];
      if (el.children.length <= 3 && txt(el) === want && vis(el)) { (el.closest('[role="tab"],[role="treeitem"],a,button,li') || el).click(); return 'clicked'; }
    }
  }
  return 'not-found';
})(%s)"""


def is_query_request(url: str, method: str, rtype: str) -> bool:
    if rtype not in ("XHR", "Fetch") or (method or "").upper() == "OPTIONS":
        return False
    path = urllib.parse.urlparse(url).path.rstrip("/").lower()
    return path.endswith(_QUERY_PATH_ENDINGS)


@dataclass
class LoadResult:
    page_load_s: Optional[float] = None
    slowest_query_s: Optional[float] = None
    query_count: int = 0
    failed_queries: int = 0
    status: str = "ok"          # ok | no-queries | timeout | redirected | error | no-switch
    note: str = ""
    method: str = ""            # switched | link


@dataclass
class PageRow:
    order: int
    page_id: str
    name: str
    hidden: Optional[bool]
    runs: list = field(default_factory=list)   # list[LoadResult]

    def summary(self) -> dict:
        ok = [r for r in self.runs if r.page_load_s is not None]
        first = self.runs[0] if self.runs else None
        avg = lambda xs: round(sum(xs) / len(xs), 2) if xs else None
        statuses = {r.status for r in self.runs}
        if not self.runs:
            status = "pending"
        elif statuses == {"ok"}:
            status = "ok"
        else:
            status = ", ".join(sorted(statuses - {"ok"}))
        notes = "; ".join(dict.fromkeys(r.note for r in self.runs if r.note))
        methods = sorted({r.method for r in self.runs if r.method})
        return {
            "order": self.order,
            "page_id": self.page_id,
            "name": self.name,
            "hidden": self.hidden,
            "runs_done": len(self.runs),
            "first_load_s": first.page_load_s if first else None,
            "avg_load_s": avg([r.page_load_s for r in ok]),
            "first_slowest_s": first.slowest_query_s if first else None,
            "avg_slowest_s": avg([r.slowest_query_s for r in ok if r.slowest_query_s is not None]),
            "queries": first.query_count if first else None,
            "loads_s": [r.page_load_s for r in self.runs],
            "status": status,
            "note": notes,
            "opened_by": " + ".join({"switched": "switched in report", "link": "reopened link"}.get(m, m) for m in methods),
        }


class _Collector:
    """Thread-safe buffer for DevTools events (filled on the reader thread)."""

    def __init__(self):
        self._lock = threading.Lock()
        self._items: list = []

    def __call__(self, method, params, session_id):
        with self._lock:
            self._items.append((method, params, session_id))

    def drain(self) -> list:
        with self._lock:
            items, self._items = self._items, []
        return items


class PerfRunner:
    def __init__(self, report_url: str, runs: int = 3, pbip_folder: Optional[str] = None,
                 profile_dir: str = "edge_profile", results_dir: str = "results",
                 settle_s: float = 5.0, no_query_grace_s: float = 20.0, max_page_s: float = 180.0,
                 signin_timeout_s: float = 600.0, devtools_port: Optional[int] = None,
                 open_mode: str = "link", switch_timeout_s: float = 8.0,
                 log: Callable[[str], None] = print):
        self.url = parse_report_url(report_url)
        self.raw_url = report_url.strip()
        self.runs = max(1, min(10, int(runs)))
        self.pbip_folder = pbip_folder or None
        self.profile_dir = profile_dir
        self.results_dir = results_dir
        self.settle_s, self.no_query_grace_s, self.max_page_s = settle_s, no_query_grace_s, max_page_s
        self.signin_timeout_s = signin_timeout_s
        # "link" (default, the one verified on the real service): open each
        # page by its URL. "in_report" (switching pages inside the open
        # report) is kept but not offered in the UI - it isn't needed now
        # that page load = slowest query, which doesn't include start-up.
        self.open_mode = "in_report" if open_mode == "in_report" else "link"
        self.switch_timeout_s = switch_timeout_s
        self._switch_order = ["push", "click"]    # reordered by what actually works
        self._clock_offset: Optional[float] = None   # browser wallTime - monotonic timestamp
        self._external_port = devtools_port   # tests: connect to an already-running DevTools
        self.log = log

        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._state = {"state": "starting", "message": "Starting...", "rows": [], "pages_source": None,
                       "progress": {"done": 0, "total": 0}, "csv_path": None, "error": None}
        self.rows: list[PageRow] = []
        self._cdp: Optional[CDP] = None
        self._edge_proc: Optional[subprocess.Popen] = None
        self._port: Optional[int] = None
        self._collector = _Collector()
        self._metadata_candidates: list = []   # (session_id, requestId) of JSON responses
        self._discovering = True

    # ---------- status for the UI ----------
    def _set(self, **kw):
        with self._lock:
            self._state.update(kw)

    def status(self) -> dict:
        with self._lock:
            s = dict(self._state)
        s["rows"] = [r.summary() for r in self.rows]
        return s

    def stop(self):
        self._stop.set()

    # ---------- main ----------
    def run(self) -> None:
        try:
            self._connect()
            self._warmup_and_discover()
            self._measure_all()
            if self._stop.is_set():
                self._finish("stopped", "Stopped - results so far are shown below.")
            else:
                self._finish("done", "Finished.")
        except Exception as e:
            self.log(f"Performance test failed: {e}")
            self._finish("error", str(e), error=str(e))
        finally:
            self._shutdown()

    def _finish(self, state, message, error=None):
        path = None
        if any(r.runs for r in self.rows):
            try:
                path = self.save_csv()
            except Exception as e:
                self.log(f"Could not save results CSV: {e}")
        self._set(state=state, message=message, error=error, csv_path=path)

    # ---------- Edge / DevTools ----------
    def _connect(self):
        if self._external_port:
            port = self._external_port
        else:
            self._set(state="starting", message="Opening Microsoft Edge...")
            self._edge_proc, port = edge_mod.start_edge(self.profile_dir, log=self.log)
            self._port = port
        ws_url = edge_mod.page_websocket_url(port)
        self._cdp = CDP(ws_url)
        self._cdp.add_listener(self._collector)
        self._cdp.add_listener(self._on_attach)
        self._cdp.add_listener(self._on_clock)
        self._cdp.call("Page.enable")
        self._cdp.call("Network.enable", {"maxTotalBufferSize": 100_000_000, "maxResourceBufferSize": 20_000_000})
        try:
            self._cdp.call("Target.setAutoAttach", {"autoAttach": True, "waitForDebuggerOnStart": False, "flatten": True})
        except CDPError:
            pass  # older builds - main-frame traffic is still captured

    def _on_attach(self, method, params, session_id):
        # Runs on the reader thread - must not block, so fire-and-forget.
        if method == "Target.attachedToTarget":
            sid = params.get("sessionId")
            if sid:
                self._cdp.send_nowait("Network.enable", {}, session_id=sid)

    def _on_clock(self, method, params, session_id):
        # DevTools timestamps are on the browser's own monotonic clock; each
        # request also carries wall-clock time. Their difference lets an
        # in-report page switch (triggered from here, on the wall clock) be
        # placed on the same timeline as the network events.
        if method == "Network.requestWillBeSent" and session_id is None:
            wt, ts = params.get("wallTime"), params.get("timestamp")
            if isinstance(wt, (int, float)) and isinstance(ts, (int, float)):
                self._clock_offset = wt - ts

    def _shutdown(self):
        try:
            if self._cdp:
                self._cdp.close()
        except Exception:
            pass
        if not self._external_port:
            edge_mod.close_edge(self._port, self._edge_proc, self.profile_dir)

    # ---------- one page load ----------
    def _load(self, page_url: Optional[str], expect_page_id: Optional[str], allow_signin: bool,
              switch: Optional[Callable[[], tuple]] = None) -> LoadResult:
        """Measures one load. With `switch`, the page is switched to inside
        the already-open report (no reload) and timing starts at the switch;
        otherwise `page_url` is opened and timing starts at the document
        request. Returns LoadResult (status "no-switch" if the switch didn't
        make the page send any queries), or raises _SignedIn if the load went
        via a sign-in page (caller re-runs)."""
        self._collector.drain()
        reqs: dict = {}
        nav_start = load_ts = None
        main_url = None
        start_wall = time.monotonic()
        last_activity = start_wall
        load_wall = None
        went_through_login = False
        signin_completed = False
        switch_deadline = None

        if switch:
            t0_wall, how = switch()
            if how not in ("pushed", "clicked"):
                return LoadResult(status="no-switch", note=str(how))
            if self._clock_offset is not None:
                nav_start = t0_wall - self._clock_offset
            switch_deadline = time.monotonic() + self.switch_timeout_s
        else:
            self._cdp.call("Page.navigate", {"url": page_url})

        while True:
            if self._stop.is_set():
                return LoadResult(status="error", note="stopped")
            if self._cdp.closed:
                raise RuntimeError("The Edge window was closed, or lost its DevTools connection.")
            now = time.monotonic()
            for method, p, sid in self._collector.drain():
                key = f"{sid or ''}:{p.get('requestId', '')}"
                if method == "Network.requestWillBeSent":
                    req = p.get("request") or {}
                    rtype = p.get("type", "")
                    if rtype == "Document" and sid is None and nav_start is None and not switch:
                        nav_start = p.get("timestamp")
                    if key in reqs:          # redirect of the same request - keep original start
                        continue
                    reqs[key] = {"url": req.get("url", ""), "method": req.get("method", ""), "type": rtype,
                                 "start": p.get("timestamp"), "end": None, "failed": False, "mime": ""}
                    if is_query_request(req.get("url", ""), req.get("method", ""), rtype):
                        last_activity = now
                elif method == "Network.responseReceived":
                    r = reqs.get(key)
                    if r is not None:
                        r["mime"] = (p.get("response") or {}).get("mimeType", "")
                        if p.get("type") and not r["type"]:
                            r["type"] = p["type"]
                elif method in ("Network.loadingFinished", "Network.loadingFailed"):
                    r = reqs.get(key)
                    if r is not None and r["end"] is None:
                        r["end"] = p.get("timestamp")
                        r["failed"] = method == "Network.loadingFailed"
                        if is_query_request(r["url"], r["method"], r["type"]):
                            last_activity = now
                        if (self._discovering and not r["failed"] and r["type"] in ("XHR", "Fetch") and "json" in r["mime"]
                                and any(h in r["url"].lower() for h in _METADATA_HINTS)):
                            self._metadata_candidates.append((sid, p.get("requestId"), r["url"]))
                elif method == "Page.frameNavigated":
                    fr = p.get("frame") or {}
                    if not fr.get("parentId") and sid is None:
                        main_url = fr.get("url", "")
                        host = urllib.parse.urlparse(main_url).hostname or ""
                        if any(host.endswith(h) for h in _LOGIN_HOSTS):
                            went_through_login = True
                            signin_completed = False
                        elif went_through_login and "powerbi" in host:
                            signin_completed = True   # SSO bounced straight back
                elif method == "Page.loadEventFired" and sid is None:
                    load_ts = p.get("timestamp")
                    load_wall = now

            if went_through_login:
                if not allow_signin:
                    return LoadResult(status="error", note="Power BI asked to sign in again mid-test")
                if not signin_completed:
                    self._wait_for_signin()
                raise _SignedIn()

            queries = [r for r in reqs.values() if is_query_request(r["url"], r["method"], r["type"])
                       and (nav_start is None or (r["start"] or 0) >= nav_start)]
            inflight = [q for q in queries if q["end"] is None]

            if queries and not inflight and now - last_activity >= self.settle_s:
                break
            if switch and not queries and now >= switch_deadline:
                return LoadResult(status="no-switch", note="no queries after switching")
            if not queries and load_wall is not None and now - load_wall >= self.no_query_grace_s:
                break
            if now - start_wall >= self.max_page_s:
                res = self._summarise(queries, nav_start, load_ts)
                res.status = "timeout"
                res.note = f"still loading after {self.max_page_s:.0f}s"
                return res
            time.sleep(0.2)

        if nav_start is None:  # start time unknown - fall back to the earliest request seen
            starts = [r["start"] for r in (queries if switch else reqs.values()) if r["start"] is not None]
            nav_start = min(starts) if starts else None
        res = self._summarise(queries, nav_start, load_ts)
        if expect_page_id:
            try:
                href = self._cdp.call("Runtime.evaluate", {"expression": "location.href", "returnByValue": True})
                href = (href.get("result") or {}).get("value") or main_url or ""
            except CDPError:
                href = main_url or ""
            if href and expect_page_id.lower() not in href.lower():
                res.status = "redirected"
                res.note = "Power BI opened a different page instead (hidden/drillthrough pages may not open directly)"
        return res

    @staticmethod
    def _summarise(queries, nav_start, load_ts) -> LoadResult:
        done = [q for q in queries if q["end"] is not None and q["start"] is not None]
        ok = [q for q in done if not q["failed"]]
        res = LoadResult(query_count=len(ok), failed_queries=len(done) - len(ok))
        if ok:
            res.slowest_query_s = round(max(q["end"] - q["start"] for q in ok), 3)
            res.page_load_s = res.slowest_query_s     # queries run in parallel - the slowest one is the page's load time
        else:
            res.status = "no-queries"
            res.note = "no visual queries on this page"
        if res.failed_queries:
            res.note = (res.note + "; " if res.note else "") + f"{res.failed_queries} query request(s) failed/cancelled"
        return res

    def _wait_for_signin(self):
        self._set(state="signin", message="Please sign in to Power BI in the Edge window - the test continues automatically afterwards.")
        deadline = time.monotonic() + self.signin_timeout_s
        while time.monotonic() < deadline:
            if self._stop.is_set():
                return
            for method, p, sid in self._collector.drain():
                if method == "Page.frameNavigated" and sid is None and not (p.get("frame") or {}).get("parentId"):
                    host = urllib.parse.urlparse((p.get("frame") or {}).get("url", "")).hostname or ""
                    if host and not any(host.endswith(h) for h in _LOGIN_HOSTS) and "powerbi" in host:
                        self._set(state="running", message="Signed in - continuing...")
                        time.sleep(2)
                        return
            time.sleep(0.3)
        raise RuntimeError("Timed out waiting for sign-in.")

    # ---------- warm-up + page list ----------
    def _warmup_and_discover(self):
        self._set(state="running", message="Warm-up: opening the report (not counted)...")
        first_page = self.url.page_id
        warm_url = self.url.page_url(first_page) if first_page else self.url.base + self.url.rest
        for _ in range(3):
            try:
                self._load(warm_url, None, allow_signin=True)
                break
            except _SignedIn:
                continue
        self._discovering = False
        if self._stop.is_set():
            return

        pages, source = [], None
        self.log(f"Page discovery: {len(self._metadata_candidates)} JSON response(s) to inspect")
        for sid, rid, url in list(self._metadata_candidates):
            try:
                body = self._cdp.call("Network.getResponseBody", {"requestId": rid}, session_id=sid, timeout=20)
            except CDPError as e:
                self.log(f"  {url[:140]} -> body unavailable ({e})")
                continue
            text = body.get("body", "")
            if body.get("base64Encoded"):
                try:
                    text = base64.b64decode(text).decode("utf-8", errors="replace")
                except Exception:
                    continue
            try:
                found = pages_from_metadata(json.loads(text))
            except Exception:
                self.log(f"  {url[:140]} -> not JSON ({len(text)} chars)")
                continue
            self.log(f"  {url[:140]} -> {len(found)} page(s)"
                     + ("" if found else f"; top-level keys: {list(json.loads(text).keys())[:12] if text.strip().startswith('{') else type(json.loads(text)).__name__}"))
            if len(found) > len(pages):
                pages, source = found, "Power BI service (report metadata)"
        if not pages and self.pbip_folder:
            pages = pages_from_pbip(self.pbip_folder)
            source = f"project folder ({self.pbip_folder})" if pages else None
        if not pages:
            pid = first_page or ""
            if not pid:
                raise RuntimeError("Couldn't work out the report's pages. Paste a link that ends with a page "
                                   "ID, or choose the dashboard's PBIP project folder.")
            pages = [Page(id=pid, name=pid)]
            source = "the link only (page list not found - choose the PBIP folder to test every page)"
        self.rows = [PageRow(order=i + 1, page_id=p.id, name=p.name, hidden=p.hidden) for i, p in enumerate(pages)]
        self._set(pages_source=source, progress={"done": 0, "total": len(self.rows) * self.runs})
        self.log(f"Pages ({len(pages)}) from {source}")

    def _open(self, page_id: str, expect_page_id: Optional[str]) -> LoadResult:
        """Opens a page from its link (handles sign-in)."""
        for _ in range(3):
            try:
                res = self._load(self.url.page_url(page_id), expect_page_id, allow_signin=True)
                res.method = "link"
                return res
            except _SignedIn:
                continue
        return LoadResult(status="error", note="sign-in loop", method="link")

    def _switch_fn(self, how: str, row: "PageRow"):
        def go():
            js = (_SWITCH_PUSH_JS % json.dumps(row.page_id)) if how == "push" else (_SWITCH_CLICK_JS % json.dumps(row.name))
            t0 = time.time()
            r = self._cdp.call("Runtime.evaluate", {"expression": js, "returnByValue": True})
            return t0, (r.get("result") or {}).get("value")
        return go

    def _switch_to(self, row: "PageRow") -> LoadResult:
        for how in list(self._switch_order):
            try:
                res = self._load(None, row.page_id, allow_signin=False, switch=self._switch_fn(how, row))
            except _SignedIn:
                break
            if res.status != "no-switch":
                if self._switch_order[0] != how:          # remember what works on this tenant
                    self._switch_order.remove(how)
                    self._switch_order.insert(0, how)
                    self.log(f"Switching pages via '{how}' works here - using it first.")
                res.method = "switched"
                return res
            self.log(f"'{row.name}': switching via '{how}' didn't load the page ({res.note})")
            if self._stop.is_set():
                return LoadResult(status="error", note="stopped")
        res = self._open(row.page_id, row.page_id)
        res.note = ((res.note + "; ") if res.note else "") + \
            "measured by reopening its link - switching to it inside the report didn't work"
        return res

    def _measure_all(self):
        total = len(self.rows) * self.runs
        done = 0

        def record(row, result):
            nonlocal done
            row.runs.append(result)
            done += 1
            self._set(progress={"done": done, "total": total})

        for run in range(1, self.runs + 1):
            if self.open_mode == "link" or len(self.rows) == 1:
                for row in self.rows:
                    if self._stop.is_set():
                        return
                    self._set(state="running", message=f"Run {run} of {self.runs}: {row.name}")
                    result = self._open(row.page_id, row.page_id)
                    if self._stop.is_set() and result.note == "stopped":
                        return
                    record(row, result)
                continue

            # In-report: open the report on page 1 (not counted), switch to
            # pages 2..N; then reopen on page 2 (not counted) and switch to
            # page 1 - so every page is a first visit within its session.
            plan = [(self.rows[0], self.rows[1:]), (self.rows[1], [self.rows[0]])]
            for landing, targets in plan:
                if self._stop.is_set():
                    return
                self._set(state="running", message=f"Run {run} of {self.runs}: opening the report (not counted)...")
                self._open(landing.page_id, None)
                for row in targets:
                    if self._stop.is_set():
                        return
                    self._set(state="running", message=f"Run {run} of {self.runs}: {row.name}")
                    result = self._switch_to(row)
                    if self._stop.is_set() and result.note == "stopped":
                        return
                    record(row, result)

    # ---------- output ----------
    def save_csv(self, path: Optional[str] = None) -> str:
        if path is None:
            os.makedirs(self.results_dir, exist_ok=True)
            path = os.path.join(self.results_dir, f"perf_{time.strftime('%Y%m%d_%H%M%S')}.csv")
        write_csv(path, [r.summary() for r in self.rows], self.raw_url, self.runs, self.open_mode)
        return path


def write_csv(path: str, rows: list, report_url: str, runs: int, open_mode: str = "in_report") -> None:
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["Report", report_url])
        w.writerow(["Tested", time.strftime("%Y-%m-%d %H:%M:%S")])
        w.writerow(["Runs per page", runs])
        w.writerow(["Page load", "slowest visual query on the page (queries run in parallel)"])
        w.writerow([])
        head = ["#", "Page", "Page ID", "Hidden", "Page load - 1st run (s)", "Page load - avg of runs (s)", "Queries"]
        head += [f"Run {i} load (s)" for i in range(1, runs + 1)] + ["Status", "Note"]
        w.writerow(head)
        for r in rows:
            loads = list(r.get("loads_s") or []) + [None] * runs
            w.writerow([r["order"], r["name"], r["page_id"],
                        "" if r["hidden"] is None else ("Yes" if r["hidden"] else "No"),
                        r["first_load_s"], r["avg_load_s"], r["queries"]]
                       + loads[:runs] + [r["status"], r["note"]])


class _SignedIn(Exception):
    pass
