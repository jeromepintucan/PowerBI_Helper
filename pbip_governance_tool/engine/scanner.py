"""
Discovers *.Report folders inside a selected PBIP root folder, and yields
VisualRef objects for every visual found, supporting both:

  - PBIR ("enhanced report format"): definition/pages/<id>/visuals/<id>/visual.json
  - Legacy (flat) format: report.json with sections[].visualContainers[].config
    (config is a JSON-encoded STRING, Power BI's older layout format)
"""
from __future__ import annotations
import json
import os
from typing import Iterator, Optional

from .models import VisualRef


def find_report_folders(root_path: str):
    """Yield absolute paths to every *.Report folder under root_path.
    If root_path itself IS a *.Report folder, yield just that."""
    root_path = os.path.abspath(root_path)
    base = os.path.basename(root_path.rstrip("\\/"))
    if base.lower().endswith(".report"):
        yield root_path
        return

    for dirpath, dirnames, _filenames in os.walk(root_path):
        # Never descend into our own backup snapshots or VCS folders - a
        # backup preserves the original "X.Report" folder name, which would
        # otherwise be re-discovered as a second, duplicate report.
        dirnames[:] = [
            d for d in dirnames
            if d != "_pbip_governance_backups" and d not in (".git", ".vs")
        ]
        for d in list(dirnames):
            if d.lower().endswith(".report"):
                yield os.path.join(dirpath, d)
        # don't recurse into report folders we already yielded (avoid dupes
        # from possible nested project copies) but do keep walking siblings
        dirnames[:] = [d for d in dirnames if not d.lower().endswith(".report")]


def _best_title(visual_obj: dict) -> str:
    """Best-effort human-readable label for a visual."""
    try:
        title_props = visual_obj.get("visualContainerObjects", {}).get("title", [])
        for t in title_props:
            text = t.get("properties", {}).get("text", {})
            lit = text.get("expr", {}).get("Literal", {}).get("Value")
            if lit:
                return lit.strip('"\'')
    except Exception:
        pass
    return visual_obj.get("visualType", "visual")


def _pbir_pages(report_folder: str):
    """Yield (page_id, page_name, page_folder) for a PBIR-format report."""
    pages_dir = os.path.join(report_folder, "definition", "pages")
    if not os.path.isdir(pages_dir):
        return
    for entry in sorted(os.listdir(pages_dir)):
        page_folder = os.path.join(pages_dir, entry)
        page_json_path = os.path.join(page_folder, "page.json")
        if not os.path.isfile(page_json_path):
            continue
        page_name = entry
        try:
            with open(page_json_path, "r", encoding="utf-8") as f:
                page_json = json.load(f)
            page_name = page_json.get("displayName") or page_json.get("name") or entry
        except Exception:
            pass
        yield entry, page_name, page_folder


def scan_pbir_report(report_folder: str) -> Iterator[VisualRef]:
    report_name = os.path.basename(report_folder)
    for page_id, page_name, page_folder in _pbir_pages(report_folder):
        visuals_dir = os.path.join(page_folder, "visuals")
        if not os.path.isdir(visuals_dir):
            continue
        for visual_entry in sorted(os.listdir(visuals_dir)):
            visual_folder = os.path.join(visuals_dir, visual_entry)
            visual_json_path = os.path.join(visual_folder, "visual.json")
            if not os.path.isfile(visual_json_path):
                continue
            try:
                with open(visual_json_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                continue
            if "visual" not in data:
                # Not a real visual - e.g. a "visualGroup" layout container
                # (used to group/frame other visuals). These have no header
                # icons of their own and must not be treated as visuals.
                continue
            visual_obj = data.get("visual", {})
            visual_type = visual_obj.get("visualType", "unknown")
            title = _best_title(visual_obj)
            yield VisualRef(
                id=f"{report_name}|{page_id}|{visual_entry}",
                report_name=report_name,
                report_path=report_folder,
                page_id=page_id,
                page_name=page_name,
                visual_id=visual_entry,
                visual_type=visual_type,
                title=title,
                file_path=visual_json_path,
                format="pbir",
            )


def is_legacy_report(report_folder: str) -> bool:
    return os.path.isfile(os.path.join(report_folder, "report.json")) and not os.path.isdir(
        os.path.join(report_folder, "definition", "pages")
    )


def scan_legacy_report(report_folder: str) -> Iterator[VisualRef]:
    report_name = os.path.basename(report_folder)
    report_json_path = os.path.join(report_folder, "report.json")
    try:
        with open(report_json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return
    sections = data.get("sections", [])
    for p_idx, section in enumerate(sections):
        page_id = section.get("name", str(p_idx))
        page_name = section.get("displayName", page_id)
        containers = section.get("visualContainers", [])
        for c_idx, vc in enumerate(containers):
            config_raw = vc.get("config")
            if not config_raw:
                continue
            try:
                config = json.loads(config_raw)
            except Exception:
                continue
            single_visual = config.get("singleVisual", {})
            visual_type = single_visual.get("visualType", "unknown")
            title = _best_title(config)
            yield VisualRef(
                id=f"{report_name}|{page_id}|{c_idx}",
                report_name=report_name,
                report_path=report_folder,
                page_id=page_id,
                page_name=page_name,
                visual_id=str(config.get("name", c_idx)),
                visual_type=visual_type,
                title=title,
                file_path=report_json_path,
                format="legacy",
                container_index=c_idx,
                page_index=p_idx,
            )


def iter_all_visuals(root_path: str):
    for report_folder in find_report_folders(root_path):
        if is_legacy_report(report_folder):
            yield from scan_legacy_report(report_folder)
        else:
            yield from scan_pbir_report(report_folder)


def count_pages(root_path: str) -> int:
    total = 0
    for report_folder in find_report_folders(root_path):
        if is_legacy_report(report_folder):
            try:
                with open(os.path.join(report_folder, "report.json"), "r", encoding="utf-8") as f:
                    data = json.load(f)
                total += len(data.get("sections", []))
            except Exception:
                pass
        else:
            total += len(list(_pbir_pages(report_folder)))
    return total
