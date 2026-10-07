"""
One-time recovery script for a bug in an earlier build of the PBIP
Governance Assistant's "filters locked & hidden" fix.

What went wrong: the fixer wrote isLockedInViewMode/isHiddenInViewMode using
the wrapped {"expr": {"Literal": {"Value": "true"}}} encoding used for
visual format-pane properties. Those two filter properties are actually
plain JSON booleans in the PBIR schema, so Power BI Desktop rejects the
wrapped form with "Property ... was not provided as the correct type" and
won't open the report until it's fixed.

This script finds every filterConfig.filters[] entry across your PBIP
project (both report.json and every visual.json) and converts any wrapped
isLockedInViewMode/isHiddenInViewMode value back to a plain boolean with the
same intended True/False meaning. It does NOT touch anything else - no other
property, no other rule - and it backs up every file it's about to change
into a `_repair_backup_<timestamp>` folder next to wherever you point it,
before writing anything.

Usage:
    py -3 repair_filter_property_types.py "C:\\path\\to\\your\\PBIP folder"

(This is a standalone script - it only needs the Python standard library,
no install step, no need to rebuild the .exe first.)
"""
from __future__ import annotations
import json
import os
import shutil
import sys
import time


def read_bool(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        if value.lower() == "true":
            return True
        if value.lower() == "false":
            return False
    if isinstance(value, dict):
        try:
            return read_bool(value["expr"]["Literal"]["Value"])
        except (KeyError, TypeError):
            return None
    return None


def is_wrapped(value) -> bool:
    return (
        isinstance(value, dict)
        and isinstance(value.get("expr"), dict)
        and isinstance(value["expr"].get("Literal"), dict)
        and "Value" in value["expr"]["Literal"]
    )


KEYS = ("isLockedInViewMode", "isHiddenInViewMode")


def fix_filters_list(filters) -> int:
    """Mutates `filters` (a list of filter dicts) in place. Returns how many
    individual property values were converted."""
    fixed = 0
    if not isinstance(filters, list):
        return fixed
    for filt in filters:
        if not isinstance(filt, dict):
            continue
        for key in KEYS:
            if key in filt and is_wrapped(filt[key]):
                val = read_bool(filt[key])
                filt[key] = bool(val) if val is not None else True
                fixed += 1
    return fixed


def find_report_folders(root_path: str):
    root_path = os.path.abspath(root_path)
    base = os.path.basename(root_path.rstrip("\\/"))
    if base.lower().endswith(".report"):
        yield root_path
        return
    for dirpath, dirnames, _ in os.walk(root_path):
        dirnames[:] = [d for d in dirnames if d != "_pbip_governance_backups" and d not in (".git", ".vs")
                       and not d.startswith("_repair_backup_")]
        for d in list(dirnames):
            if d.lower().endswith(".report"):
                yield os.path.join(dirpath, d)
        dirnames[:] = [d for d in dirnames if not d.lower().endswith(".report")]


def iter_target_files(report_folder: str):
    report_json = os.path.join(report_folder, "definition", "report.json")
    if os.path.isfile(report_json):
        yield report_json
    else:
        legacy = os.path.join(report_folder, "report.json")
        if os.path.isfile(legacy):
            yield legacy
    pages_dir = os.path.join(report_folder, "definition", "pages")
    if os.path.isdir(pages_dir):
        for page_entry in os.listdir(pages_dir):
            visuals_dir = os.path.join(pages_dir, page_entry, "visuals")
            if not os.path.isdir(visuals_dir):
                continue
            for visual_entry in os.listdir(visuals_dir):
                vpath = os.path.join(visuals_dir, visual_entry, "visual.json")
                if os.path.isfile(vpath):
                    yield vpath


def main():
    if len(sys.argv) < 2:
        print('Usage: py -3 repair_filter_property_types.py "C:\\path\\to\\your\\PBIP folder"')
        sys.exit(1)
    root = os.path.abspath(sys.argv[1])
    if not os.path.isdir(root):
        print(f"Not a folder: {root}")
        sys.exit(1)

    report_folders = list(find_report_folders(root))
    if not report_folders:
        print(f"No *.Report folder found under: {root}")
        sys.exit(1)

    backup_dir = os.path.join(root, f"_repair_backup_{time.strftime('%Y%m%d_%H%M%S')}")
    touched_files = []
    total_props_fixed = 0

    for report_folder in report_folders:
        for path in iter_target_files(report_folder):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception as e:
                print(f"  ! could not read {path}: {e}")
                continue

            n = fix_filters_list((data.get("filterConfig") or {}).get("filters"))
            if n == 0:
                continue

            # Back up before writing.
            rel = os.path.relpath(path, root)
            backup_path = os.path.join(backup_dir, rel)
            os.makedirs(os.path.dirname(backup_path), exist_ok=True)
            shutil.copy2(path, backup_path)

            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
                f.write("\n")

            touched_files.append(path)
            total_props_fixed += n
            print(f"  fixed {n} propert{'y' if n == 1 else 'ies'}: {rel}")

    print()
    if touched_files:
        print(f"Done. Fixed {total_props_fixed} propert{'y' if total_props_fixed == 1 else 'ies'} "
              f"across {len(touched_files)} file(s).")
        print(f"Originals backed up to: {backup_dir}")
        print("Reopen the report in Power BI Desktop - the type error should be gone.")
    else:
        print("No wrapped isLockedInViewMode/isHiddenInViewMode values were found - nothing to fix.")
        print("(If you're still seeing the error, double-check you pointed this at the right folder.)")


if __name__ == "__main__":
    main()
