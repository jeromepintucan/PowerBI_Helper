"""
Report URL parsing and page discovery.

A Power BI service report link looks like
  https://app.powerbi.com/groups/<workspace>/reports/<report>/<page>?experience=power-bi
where <page> is the page's internal name - the same ID a PBIP project uses
for that page's folder (definition/pages/<page>/). Swapping that last
segment opens a different page directly.

The list of pages is found, in order of preference:
  1. from the report metadata Power BI itself downloads when the report
     opens (captured from the network, exactly like DevTools would see it);
  2. from a local PBIP project folder, if you point the app at one;
  3. failing both, just the page in the link you pasted.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Optional

_URL_RE = re.compile(
    r"^(?P<base>https?://[^?#]*?/reports/[^/?#]+)(?:/(?P<page>[^/?#]+))?/?(?P<rest>[?#].*)?$",
    re.IGNORECASE,
)
_PAGE_ID_RE = re.compile(r"^(ReportSection[0-9a-zA-Z]*|[0-9a-f]{20})$")


@dataclass
class Page:
    id: str
    name: str
    hidden: Optional[bool] = None   # None = unknown
    order: int = 0


@dataclass
class ReportUrl:
    base: str            # .../reports/<id>
    page_id: Optional[str]
    rest: str            # ?experience=power-bi (kept on every page URL)

    def page_url(self, page_id: str) -> str:
        return f"{self.base}/{page_id}{self.rest}"


def parse_report_url(url: str) -> ReportUrl:
    url = (url or "").strip()
    m = _URL_RE.match(url)
    if not m:
        raise ValueError(
            "That doesn't look like a Power BI report link. It should contain "
            "'/reports/<report id>', e.g. https://app.powerbi.com/groups/.../reports/.../<page>"
        )
    page = m.group("page")
    if page and page.lower() in ("reportsection", ""):
        page = None
    return ReportUrl(base=m.group("base"), page_id=page, rest=m.group("rest") or "")


def _is_hidden(d: dict) -> Optional[bool]:
    v = d.get("visibility")
    if v is None and isinstance(d.get("config"), str):
        try:
            v = json.loads(d["config"]).get("visibility")
        except Exception:
            v = None
    if v is None:
        return None
    return v in (1, "1", "HiddenInViewMode", "Hidden")


def pages_from_metadata(obj) -> list[Page]:
    """Walks any JSON Power BI returned and picks out the page list: a list
    of objects that each have a page-ID-shaped "name" and a "displayName".
    Deliberately shape-agnostic - it doesn't depend on one exact endpoint
    or nesting, only on what a page entry always contains."""
    best: list[Page] = []

    def walk(node):
        nonlocal best
        if isinstance(node, dict):
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            entries = [x for x in node if isinstance(x, dict)
                       and isinstance(x.get("name"), str) and _PAGE_ID_RE.match(x["name"])
                       and isinstance(x.get("displayName"), str)]
            if entries and len(entries) == len([x for x in node if isinstance(x, dict)]):
                pages = [Page(id=e["name"], name=e["displayName"], hidden=_is_hidden(e),
                              order=int(e.get("ordinal", i)) if str(e.get("ordinal", i)).lstrip("-").isdigit() else i)
                         for i, e in enumerate(entries)]
                if len(pages) > len(best):
                    best = pages
            for v in node:
                walk(v)

    walk(obj)
    if any(p.hidden is not None for p in best):   # visibility is only written for hidden pages
        for p in best:
            if p.hidden is None:
                p.hidden = False
    best.sort(key=lambda p: p.order)
    return best


def pages_from_pbip(folder: str) -> list[Page]:
    """Reads the page list from a local PBIP project (PBIR format or legacy
    report.json). `folder` can be the folder holding the .pbip file, or the
    *.Report folder itself."""
    if not folder or not os.path.isdir(folder):
        return []
    report_dirs = []
    if folder.lower().endswith(".report"):
        report_dirs.append(folder)
    else:
        for name in sorted(os.listdir(folder)):
            if name.lower().endswith(".report") and os.path.isdir(os.path.join(folder, name)):
                report_dirs.append(os.path.join(folder, name))
    for rd in report_dirs:
        pages_dir = os.path.join(rd, "definition", "pages")
        if os.path.isdir(pages_dir):
            order: list[str] = []
            try:
                with open(os.path.join(pages_dir, "pages.json"), encoding="utf-8") as f:
                    order = json.load(f).get("pageOrder") or []
            except Exception:
                pass
            pages = []
            for pid in sorted(os.listdir(pages_dir)):
                pj = os.path.join(pages_dir, pid, "page.json")
                if not os.path.isfile(pj):
                    continue
                try:
                    with open(pj, encoding="utf-8") as f:
                        d = json.load(f)
                except Exception:
                    continue
                pages.append(Page(id=d.get("name") or pid, name=d.get("displayName") or pid,
                                  hidden=d.get("visibility") == "HiddenInViewMode",
                                  order=order.index(pid) if pid in order else 10_000 + len(pages)))
            if pages:
                pages.sort(key=lambda p: p.order)
                return pages
        legacy = os.path.join(rd, "report.json")
        if os.path.isfile(legacy):
            try:
                with open(legacy, encoding="utf-8") as f:
                    return pages_from_metadata({"sections": json.load(f).get("sections", [])})
            except Exception:
                pass
    return []
