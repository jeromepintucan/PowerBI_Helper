"""
BI Dev Assistant
----------------
A small native desktop app (pywebview) that scans a Power BI Project (PBIP)
folder against a few header-icon / sort conventions, shows a summary of
violations, and can auto-fix the selected ones (with an automatic backup of
every file it's about to touch).

Run (from source):
    py -3 -m pip install --user -r requirements.txt
    py -3 main.py

Or run the packaged .exe built by build_exe.bat - see README.md.

Everything this script does is also written to app.log next to this file
(or next to the .exe, when packaged), so if the window never appears, that
log is the place to look.
"""
from __future__ import annotations
import os
import sys
import threading
import time
import traceback

# BASE_DIR is the real, persistent location: the .exe's own folder (or this
# script's folder, running from source). app.log lives here so it's easy to
# find next to the exe and survives between runs.
#
# RESOURCE_DIR is where bundled files (ui/index.html, config/rule_config.
# json) actually sit at runtime. This build is packaged with PyInstaller's
# --onefile mode, which self-extracts its bundled data into a fresh
# temporary folder (sys._MEIPASS) on every launch - NOT next to the real
# .exe - so bundled resources must be read from there instead of BASE_DIR.
if getattr(sys, "frozen", False):
    BASE_DIR = os.path.dirname(os.path.abspath(sys.executable))
    RESOURCE_DIR = getattr(sys, "_MEIPASS", BASE_DIR)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    RESOURCE_DIR = BASE_DIR
LOG_PATH = os.path.join(BASE_DIR, "app.log")


def log(msg: str) -> None:
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    try:
        # A --windowed build has no console, so sys.stdout is None and a
        # plain print() would raise - app.log below is the only guaranteed
        # place these messages show up once the console window is gone.
        print(line, flush=True)
    except Exception:
        pass
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def fatal(msg: str, exc: Exception | None = None) -> None:
    log("ERROR: " + msg)
    if exc is not None:
        log(traceback.format_exc())
    log("Full history is in app.log next to this script - share that file if you need help.")
    sys.exit(1)


log("=" * 60)
log("Starting BI Dev Assistant...")
log(f"Python: {sys.version}")
log(f"Executable: {sys.executable}")

try:
    import webview
except Exception as e:
    fatal(
        "Could not import 'webview' (the pywebview package). Setup likely "
        "didn't finish. From this folder, run:\n"
        "    py -3 -m pip install --user -r requirements.txt",
        e,
    )
    raise SystemExit(1)

sys.path.insert(0, BASE_DIR)
try:
    from engine import service, rules
except Exception as e:
    fatal("Could not import the scan/fix engine (the engine/ folder next to main.py).", e)
    raise SystemExit(1)

UI_HTML = os.path.join(RESOURCE_DIR, "ui", "index.html")


class Api:
    def __init__(self):
        self._window = None
        self._last_result = None  # keeps the last ScanResult so fix() can map ids -> findings

    def set_window(self, window):
        self._window = window

    # ---- exposed to JS ----

    def get_settings(self):
        """Static config the UI wants before any scan happens - currently
        just the approved color palette, so the checklist can show the user
        what "the brand palette" actually means. Reads live from
        rule_config.json each call, so an edit there shows up without a
        rebuild (just reopen the app)."""
        return {"approved_palette": rules.APPROVED_PALETTE}

    def browse_folder(self):
        result = self._window.create_file_dialog(webview.FOLDER_DIALOG)
        if not result:
            return None
        return result[0] if isinstance(result, (list, tuple)) else result

    def scan(self, path: str, enabled_rules: list | None = None, rule_options: dict | None = None):
        log(f"Scanning: {path} (rules: {enabled_rules or 'all'})")
        result = service.run_scan(path, enabled_rules, rule_options)
        self._last_result = result
        log(f"Scan done: {result.visuals_scanned} visuals, {len(result.findings)} findings")
        return result.to_dict()

    def apply_fixes(self, path: str, finding_ids: list):
        if self._last_result is None or self._last_result.root_path != os.path.abspath(path):
            self._last_result = service.run_scan(path)
        res = service.apply_fixes(path, self._last_result, finding_ids)
        log(f"Fix applied: {res.get('applied_count')} ok, {res.get('failed_count')} failed, "
            f"backup={res.get('backup_folder')}")
        return res

    # ---- Performance test tab (perftest/) ----
    # Measures each page of a PUBLISHED report in the Power BI service: page
    # load = the page's slowest visual query, read from Edge's DevTools
    # network data. Imported lazily so a problem there can never stop the
    # governance scan from starting.

    def perf_environment(self):
        from perftest import edge
        return {"edge_found": bool(edge.find_edge()), "results_dir": os.path.join(BASE_DIR, "perf_results")}

    def perf_validate_url(self, url: str):
        from perftest.pages import parse_report_url
        try:
            u = parse_report_url(url)
            return {"ok": True, "page_id": u.page_id}
        except ValueError as e:
            return {"ok": False, "error": str(e)}

    def perf_start(self, url: str, runs: int = 3, pbip_folder: str | None = None):
        from perftest.runner import PerfRunner
        t = getattr(self, "_perf_thread", None)
        if t and t.is_alive():
            return {"ok": False, "error": "A performance test is already running."}
        try:
            self._perf = PerfRunner(
                url, runs=runs, pbip_folder=pbip_folder or None,
                profile_dir=os.path.join(BASE_DIR, "edge_profile"),
                results_dir=os.path.join(BASE_DIR, "perf_results"), log=log,
            )
        except ValueError as e:
            return {"ok": False, "error": str(e)}
        log(f"Performance test: {url} (runs={runs}, pbip={pbip_folder or '-'})")
        self._perf_thread = threading.Thread(target=self._perf.run, name="perf-run", daemon=True)
        self._perf_thread.start()
        return {"ok": True}

    def perf_status(self):
        r = getattr(self, "_perf", None)
        return r.status() if r else {"state": "idle", "rows": []}

    def perf_stop(self):
        r = getattr(self, "_perf", None)
        if r:
            r.stop()
        return True

    def perf_export_csv(self):
        from perftest.runner import write_csv
        r = getattr(self, "_perf", None)
        if not r or not r.rows:
            return None
        res = self._window.create_file_dialog(
            webview.SAVE_DIALOG, save_filename=f"performance_test_{time.strftime('%Y%m%d_%H%M')}.csv",
            file_types=("CSV file (*.csv)",),
        )
        if not res:
            return None
        path = res[0] if isinstance(res, (list, tuple)) else res
        if not path.lower().endswith(".csv"):
            path += ".csv"
        write_csv(path, [x.summary() for x in r.rows], r.raw_url, r.runs, r.open_mode)
        return path

    def perf_open_path(self, path: str):
        self.open_folder(path if os.path.isdir(path) else os.path.dirname(path))

    def open_folder(self, path: str):
        try:
            if sys.platform.startswith("win"):
                os.startfile(path)  # noqa
            elif sys.platform == "darwin":
                os.system(f'open "{path}"')
            else:
                os.system(f'xdg-open "{path}"')
        except Exception:
            pass


def start_gui_with_fallback():
    """Try the modern Edge WebView2 backend first (needed for the app's
    styling to render correctly); fall back to letting pywebview auto-pick
    if that specific backend can't be loaded on this machine."""
    attempts = [("edgechromium", {"gui": "edgechromium"}), ("auto-detect", {})]
    last_exc = None
    for name, kwargs in attempts:
        try:
            log(f"Starting GUI loop (backend: {name})...")
            webview.start(debug=False, **kwargs)
            log("GUI loop ended (window was closed normally).")
            return
        except Exception as e:
            last_exc = e
            log(f"Backend '{name}' failed to start: {e}")
    fatal(
        "Could not start any GUI backend. This usually means the Microsoft "
        "Edge WebView2 Runtime isn't installed. It ships with Windows 11, "
        "but if it's missing/corrupted, install 'Evergreen Bootstrapper' "
        "from https://developer.microsoft.com/microsoft-edge/webview2/ "
        "and try again.",
        last_exc,
    )


def main():
    log(f"UI file: {UI_HTML} (exists={os.path.isfile(UI_HTML)})")
    api = Api()
    try:
        window = webview.create_window(
            "BI Dev Assistant",
            UI_HTML,
            js_api=api,
            width=1180,
            height=860,
            min_size=(860, 620),
            background_color="#0f1420",
        )
        api.set_window(window)
        log("Window object created.")
    except Exception as e:
        fatal("Failed to create the app window.", e)
        return

    start_gui_with_fallback()


if __name__ == "__main__":
    main()
