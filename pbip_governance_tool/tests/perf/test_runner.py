"""End-to-end test of the runner against the mock DevTools/Power BI server.
Run:  python tests/perf/test_runner.py"""
import csv
import os
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))   # project root
sys.path.insert(0, HERE)

from mock_devtools import MockDevTools  # noqa: E402
from perftest.runner import PerfRunner, is_query_request  # noqa: E402
from perftest.pages import parse_report_url, pages_from_pbip  # noqa: E402

URL = ("https://app.powerbi.com/groups/0b665265-bf57-475d-9332-1ecd5f111f52/reports/"
       "805089a4-aaff-4882-a452-0993da25c613/aaaaaaaaaaaaaaaaaaaa?experience=power-bi")


def approx(a, b, tol=0.15):
    return a is not None and abs(a - b) <= tol


LOGS = []


def run(signin, runs=2, stop_after=None, mode="link", router="push"):
    mock = MockDevTools(signin=signin, router=router)
    tmp = tempfile.mkdtemp()
    r = PerfRunner(URL, runs=runs, results_dir=tmp, devtools_port=mock.port, open_mode=mode,
                   settle_s=1.0, no_query_grace_s=1.5, max_page_s=30, switch_timeout_s=2.0, log=LOGS.append)
    t = threading.Thread(target=r.run)
    t.start()
    if stop_after:
        time.sleep(stop_after)
        r.stop()
    t.join(120)
    assert not t.is_alive(), "runner hung"
    return r.status(), mock


# ---- unit checks ----
u = parse_report_url(URL)
assert u.page_id == "aaaaaaaaaaaaaaaaaaaa" and u.rest == "?experience=power-bi"
assert u.page_url("bbbbbbbbbbbbbbbbbbbb").endswith("/reports/805089a4-aaff-4882-a452-0993da25c613/bbbbbbbbbbbbbbbbbbbb?experience=power-bi")
assert parse_report_url(URL.split("/aaaa")[0]).page_id is None
assert is_query_request("https://x/explore/querydata?synchronous=true", "POST", "XHR")
assert is_query_request("https://x/explore/query", "POST", "Fetch")
assert not is_query_request("https://x/explore/querydata", "OPTIONS", "XHR")
assert not is_query_request("https://x/explore/conceptualschema", "POST", "XHR")
assert not is_query_request("https://x/explore/query", "GET", "Script")
real_pbip = "/sessions/confident-kind-einstein/mnt/DC-Benefit-Affordability-Dashboard"
if os.path.isdir(real_pbip):
    pp = pages_from_pbip(real_pbip)
    print(f"PBIP fallback on the real DC dashboard: {len(pp)} pages, {sum(1 for p in pp if p.hidden)} hidden - first: {pp[0].name!r}")
    assert len(pp) == 17
print("unit checks OK")

# ======== "open each page from its link" mode ========
# ---- full run, sign-in page shown, user signs in 1s later ----
t0 = time.time()
s, mock = run("delayed")
print(f"\n[delayed sign-in] state={s['state']} source={s['pages_source']!r} in {time.time()-t0:.1f}s")
assert s["state"] == "done", s
assert s["pages_source"].startswith("Power BI service")
rows = {r["name"]: r for r in s["rows"]}
for r in s["rows"]:
    print(f"  {r['order']}. {r['name']:<22} hidden={r['hidden']!s:<5} load 1st={r['first_load_s']} avg={r['avg_load_s']} "
          f"slowest 1st={r['first_slowest_s']} q={r['queries']} status={r['status']} {r['note']}")
a, b, c, d = rows["Benefit affordability"], rows["DC"], rows["Info"], rows["Drillthrough detail"]
assert a["runs_done"] == 2 and approx(a["first_load_s"], 0.9) and approx(a["avg_load_s"], 0.9)   # = slowest query, not 1.7 to last query
assert approx(a["first_slowest_s"], 0.9) and a["queries"] == 3, a          # worker query counted; preflight, schema xhr, cancelled ignored
assert "failed/cancelled" in a["note"]
assert approx(b["first_load_s"], 1.2) and approx(b["first_slowest_s"], 1.2) and b["queries"] == 2, b
assert c["hidden"] is True and c["status"] == "no-queries" and c["first_load_s"] is None, c
assert d["status"] == "redirected", d
# navigation order: warm-up (login) -> warm-up again after sign-in -> round robin x2
pages = [n.split("/")[-1].split("?")[0][:4] for n in mock.navigations]
print("  navigations:", pages)
assert pages == ["aaaa", "aaaa", "aaaa", "bbbb", "cccc", "dddd", "aaaa", "bbbb", "cccc", "dddd"], pages
with open(s["csv_path"], encoding="utf-8-sig") as f:
    rows_csv = list(csv.reader(f))
print("  CSV header:", rows_csv[5][:9])
assert rows_csv[6][1] == "Benefit affordability" and rows_csv[6][4] == "0.9" and len(rows_csv) == 10
assert rows_csv[5][4:7] == ["Page load - 1st run (s)", "Page load - avg of runs (s)", "Queries"]

# ---- SSO bounce: login page and straight back, same instant ----
s, mock = run("instant", runs=1, mode="link")
print(f"\n[instant SSO] state={s['state']} loads={[r['first_load_s'] for r in s['rows']]}")
assert s["state"] == "done" and len(mock.navigations) == 6

# ---- Stop mid-run ----
s, mock = run("none", runs=3, stop_after=6, mode="link")
print(f"\n[stop] state={s['state']} loads done={s['progress']}")
assert s["state"] == "stopped" and s["progress"]["done"] < s["progress"]["total"] and s["csv_path"]

# ======== "switch pages inside the report" mode (default) ========
def show(label, s, mock):
    print(f"\n[{label}] state={s['state']}  switches={mock.switches}")
    for r in s["rows"]:
        print(f"  {r['name']:<22} load={r['first_load_s']} slowest={r['first_slowest_s']} q={r['queries']} "
              f"status={r['status']} opened_by={r['opened_by']!r} {r['note']}")
    return {r["name"]: r for r in s["rows"]}

# Power BI's router reacts to the URL change: every page switched in place, timed from the switch
LOGS.clear()
s, mock = run("delayed", runs=1, mode="in_report", router="push")
rows = show("in-report, router reacts to URL change", s, mock)
assert s["state"] == "done"
a, b, c, d = rows["Benefit affordability"], rows["DC"], rows["Info"], rows["Drillthrough detail"]
assert a["opened_by"] == b["opened_by"] == "switched in report"
assert approx(a["first_load_s"], 0.9) and a["queries"] == 3
assert approx(b["first_load_s"], 1.2) and b["queries"] == 2
assert c["status"] == "no-queries" and c["opened_by"] == "reopened link" and "reopening its link" in c["note"]
assert d["status"] == "redirected"
nav = [n.split("/")[-1][:4] for n in mock.navigations]
print("  full page loads:", nav)
assert nav == ["aaaa", "aaaa", "aaaa", "cccc", "bbbb"], nav   # warm-up x2 (sign-in), landing on p1, Info fallback, landing on p2

# Router ignores URL changes, but clicking the Pages pane works -> learns to click first
LOGS.clear()
s, mock = run("none", runs=1, mode="in_report", router="click")
rows = show("in-report, only Pages-pane click works", s, mock)
assert rows["DC"]["opened_by"] == "switched in report" and approx(rows["DC"]["first_load_s"], 1.2)
assert rows["Benefit affordability"]["opened_by"] == "switched in report" and approx(rows["Benefit affordability"]["first_load_s"], 0.9)
assert rows["Drillthrough detail"]["opened_by"] == "reopened link"      # hidden page isn't in the Pages pane
assert any("using it first" in l for l in LOGS)
kinds = [k for k, _ in mock.switches]
assert kinds[0] == "push" and kinds[1] == "click" and kinds[2] == "click", kinds   # tried push once, then click first

# Neither works -> every page falls back to reopening its link, and says so
s, mock = run("none", runs=1, mode="in_report", router="none")
rows = show("in-report, switching never works", s, mock)
assert all(r["opened_by"] == "reopened link" for r in rows.values())
assert approx(rows["Benefit affordability"]["first_load_s"], 0.9) and approx(rows["DC"]["first_load_s"], 1.2)

# Page discovery is logged for diagnosis
assert any(l.startswith("Page discovery:") for l in LOGS) and any("-> 4 page(s)" in l for l in LOGS)

# Two runs: each run reopens the report, every page measured twice
s, mock = run("none", runs=2, mode="in_report", router="push")
rows = show("in-report, 2 runs", s, mock)
assert rows["DC"]["runs_done"] == 2 and approx(rows["DC"]["avg_load_s"], 1.2)
with open(s["csv_path"], encoding="utf-8-sig") as f:
    lines = list(csv.reader(f))
assert lines[3] == ["Page load", "slowest visual query on the page (queries run in parallel)"]

# ======== leftover Edge on the test profile ========
from perftest import edge
prof = tempfile.mkdtemp()
mock = MockDevTools(signin="none")
open(os.path.join(prof, "DevToolsActivePort"), "w").write(f"{mock.port}\n/devtools/browser/x\n")
assert edge.existing_devtools_port(prof) == mock.port
proc, port = edge.start_edge(prof, log=LOGS.append)
assert proc is None and port == mock.port, (proc, port)         # reused, no new Edge launched
print("\n[leftover Edge] still-running test Edge is reused instead of failing")
open(os.path.join(prof, "DevToolsActivePort"), "w").write("1\n/devtools/browser/x\n")   # stale file, nothing listening
assert edge.existing_devtools_port(prof) is None
print("[leftover Edge] stale DevToolsActivePort file is ignored")
print("\nALL TESTS PASSED")
