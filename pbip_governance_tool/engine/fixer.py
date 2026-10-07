from __future__ import annotations
import copy
import json
import os
import re
import uuid
from typing import Any

from . import jsonutil
from . import backup as backup_mod
from .rules import (
    RULE_SLICER_HEADER, RULE_VISUAL_HEADER_FOCUS, RULE_SORT_ASCENDING, RULE_COLOR_PALETTE,
    RULE_EXPORT_DISABLED, RULE_FILTERS_LOCKED_HIDDEN, RULE_LEGEND_BOLD, RULE_DATA_POINT_BORDER,
    RULE_TABLE_VALUES_CENTERED, RULE_DC_DISCLAIMER_BANNER_SHOWN, RULE_VISUALIZE_POPULATION_BOOKMARK,
    RULE_FILTER_VALUES_SORTED, RULE_PREVIOUS_PAGE_BACK, RULE_SEGMENTATION_SINGLE_SELECT,
    ICON_PROPS, _get_header_properties, _find_datapoint_border_entry, _field_identity, _filter_type_for_field,
)
from .models import Finding


def _visual_key(v) -> str:
    return f"{v.page_id}|{v.visual_id}|{v.container_index}"


def _apply_single(visual_obj: dict, finding: Finding) -> None:
    """Mutates `visual_obj` in place per `finding`. Despite the parameter
    name, for some rules (RULE_COLOR_PALETTE's theme kind, RULE_EXPORT_
    DISABLED, RULE_FILTERS_LOCKED_HIDDEN's report-level finding) the caller
    passes the whole file's root JSON instead of the nested visual object -
    see apply_fixes() below for which container each rule/format needs."""
    fmt = finding.visual.format
    if finding.rule_id == RULE_SLICER_HEADER:
        props = _get_header_properties(visual_obj, fmt, ensure=True)
        jsonutil.set_bool(props, "show", False)

    elif finding.rule_id == RULE_VISUAL_HEADER_FOCUS:
        props = _get_header_properties(visual_obj, fmt, ensure=True)
        # Only touch "show" if it was explicitly disabled - Power BI itself
        # never writes "show": true (that's the default), so we don't
        # either; we just make sure it isn't explicitly False. Focus mode
        # has no dedicated property at all, so it's never written - leaving
        # it alone is exactly what keeps it enabled.
        if jsonutil.read_bool(props.get("show")) is False:
            jsonutil.set_bool(props, "show", True)
        for label, prop_name in ICON_PROPS.items():
            jsonutil.set_bool(props, prop_name, False)

    elif finding.rule_id == RULE_SORT_ASCENDING:
        path = finding.fix_payload["path"]
        target: Any = visual_obj
        for p in path[:-1]:
            target = target[p]
        last_key = path[-1]
        current = target[last_key]
        target[last_key] = 1 if current == 2 else "Ascending"

    elif finding.rule_id == RULE_COLOR_PALETTE:
        kind = (finding.fix_payload or {}).get("kind")
        if kind == "theme_data_colors":
            # There's exactly one correct value here (the approved
            # sequence), so this is always safely auto-fixable.
            visual_obj["dataColors"] = list(finding.fix_payload["expected"])
        elif kind == "nearest_match_colors":
            # Only reached if the user deliberately checked this finding
            # (auto_select=False keeps it out of "select all"/"Fix Selected"
            # by default). Snaps every occurrence of the hardcoded color to
            # its nearest approved-palette match by RGB distance, preserving
            # whichever value encoding (plain string vs Literal-wrapped) was
            # already there.
            for occ in finding.fix_payload.get("occurrences", []):
                path, nearest = occ["path"], occ["nearest"]
                target: Any = visual_obj
                try:
                    for p in path[:-1]:
                        target = target[p]
                    last_key = path[-1]
                    current = target[last_key]
                except (KeyError, IndexError, TypeError):
                    continue
                if isinstance(current, dict):
                    lit = current.get("expr", {}).get("Literal", {})
                    if isinstance(lit, dict) and "Value" in lit:
                        orig = str(lit["Value"]).strip()
                        quote = orig[0] if orig[:1] in ("'", '"') else "'"
                        lit["Value"] = f"{quote}{nearest}{quote}"
                    else:
                        target[last_key] = nearest
                else:
                    target[last_key] = nearest

    elif finding.rule_id == RULE_EXPORT_DISABLED:
        # visual_obj here is the report.json root (format="report_root").
        settings = visual_obj.get("settings")
        if not isinstance(settings, dict):
            settings = {}
            visual_obj["settings"] = settings
        settings["exportDataMode"] = finding.fix_payload.get("value", "None")

    elif finding.rule_id == RULE_FILTERS_LOCKED_HIDDEN:
        # visual_obj here is either the report.json root (report-level
        # finding, format="report_root") or a visual.json's file root
        # (per-visual finding, format="pbir" - filterConfig sits at the file
        # root, a sibling of "visual", not nested inside the visual object;
        # apply_fixes() below passes `root` rather than the usual visual_obj
        # for exactly this rule).
        payload = finding.fix_payload or {}
        filter_config = visual_obj.get("filterConfig")
        if not isinstance(filter_config, dict):
            filter_config = {}
            visual_obj["filterConfig"] = filter_config
        filters = filter_config.get("filters")
        if not isinstance(filters, list):
            filters = []
            filter_config["filters"] = filters

        if payload.get("kind") == "create_and_lock_filters":
            # A slicer's own bound field shows as a card in its Filters
            # pane purely from the query definition - Power BI never wrote
            # an actual filterConfig entry for it because nobody has ever
            # locked/hidden it by hand. There's nothing existing to patch,
            # so create a new, minimal entry instead: the same shape Power
            # BI itself writes for a never-restricted filter card
            # (confirmed against several real entries in this project with
            # no "filter" key at all - just field + type + the two
            # view-mode flags).
            for field_obj in payload.get("fields", []):
                target_ident = _field_identity(field_obj)
                already = any(
                    isinstance(f, dict) and _field_identity(f.get("field")) == target_ident
                    for f in filters
                )
                if already:
                    continue
                filters.append({
                    "name": uuid.uuid4().hex[:20],
                    "field": copy.deepcopy(field_obj),
                    "type": _filter_type_for_field(field_obj),
                    "isHiddenInViewMode": True,
                    "isLockedInViewMode": True,
                })
        else:
            for i in payload.get("indices", []):
                if 0 <= i < len(filters) and isinstance(filters[i], dict):
                    # NOTE: unlike visual "objects.X[].properties" bindings,
                    # a filter entry's isLockedInViewMode/isHiddenInViewMode
                    # are plain JSON booleans in the PBIR schema - never the
                    # {"expr":{"Literal":{"Value":...}}} wrapper used for
                    # format-pane properties. jsonutil.set_bool() was wrong
                    # here (it defaults to the wrapped style when it can't
                    # detect a sibling's style, which is exactly what
                    # happened - "howCreated" is a string, not a bool) and
                    # produced a value Power BI Desktop rejects outright as
                    # the wrong type. Write plain booleans directly.
                    filters[i]["isLockedInViewMode"] = True
                    filters[i]["isHiddenInViewMode"] = True

    elif finding.rule_id == RULE_LEGEND_BOLD:
        objects = visual_obj.get("objects")
        if not isinstance(objects, dict):
            objects = {}
            visual_obj["objects"] = objects
        legend_list = objects.get("legend")
        if not isinstance(legend_list, list) or not legend_list:
            legend_list = [{"properties": {}}]
            objects["legend"] = legend_list
        for entry in legend_list:
            if isinstance(entry, dict):
                props = entry.get("properties")
                if not isinstance(props, dict):
                    props = {}
                    entry["properties"] = props
                jsonutil.set_bool(props, "bold", True)

    elif finding.rule_id == RULE_DATA_POINT_BORDER:
        # Always fixes to a literal '#FFFFFF' rather than the project's own
        # ThemeDataColor{ColorId:0} convention - that ColorId only means
        # "white" in a theme whose color-role ordering happens to put white
        # at slot 0 (confirmed for this project, not guaranteed for every
        # PBIP this tool might ever run against), so a literal hex is the
        # one encoding that's correct regardless of theme.
        objects = visual_obj.get("objects")
        if not isinstance(objects, dict):
            objects = {}
            visual_obj["objects"] = objects
        dp = objects.get("dataPoint")
        if not isinstance(dp, list):
            dp = []
            objects["dataPoint"] = dp
        entry = _find_datapoint_border_entry(objects)
        if entry is None:
            entry = {"properties": {}}
            dp.append(entry)
        props = entry.get("properties")
        if not isinstance(props, dict):
            props = {}
            entry["properties"] = props
        props["borderShow"] = {"expr": {"Literal": {"Value": "true"}}}
        props["borderColor"] = {"solid": {"color": {"expr": {"Literal": {"Value": "'#FFFFFF'"}}}}}
        props["borderSize"] = {"expr": {"Literal": {"Value": "0.5D"}}}

    elif finding.rule_id == RULE_TABLE_VALUES_CENTERED:
        # Column headers use the identical table-wide default entry
        # (index 0, no selector) for both Table and Matrix visuals.
        payload = finding.fix_payload or {}
        objects = visual_obj.get("objects")
        if not isinstance(objects, dict):
            objects = {}
            visual_obj["objects"] = objects

        entries = objects.get("columnHeaders")
        if not isinstance(entries, list) or not entries:
            entries = [{"properties": {}}]
            objects["columnHeaders"] = entries
        entry = entries[0]
        if not isinstance(entry, dict):
            entry = {"properties": {}}
            entries[0] = entry
        props = entry.get("properties")
        if not isinstance(props, dict):
            props = {}
            entry["properties"] = props
        props["alignment"] = {"expr": {"Literal": {"Value": "'Center'"}}}

        if payload.get("is_matrix"):
            # A Matrix has no table-wide default for its value cells - each
            # column flagged by check_table_alignment (value_query_refs)
            # gets its own "Specific column" override written into
            # objects.columnFormatting[], matched by selector.metadata ==
            # that column's queryRef - exactly what Power BI Desktop itself
            # writes when a user sets alignment on one column there. An
            # existing entry for that column (e.g. one with some other
            # property already set, or the wrong alignment) is updated in
            # place rather than duplicated.
            formatting = objects.get("columnFormatting")
            if not isinstance(formatting, list):
                formatting = []
                objects["columnFormatting"] = formatting
            for qref in payload.get("value_query_refs", []):
                target = None
                for cf_entry in formatting:
                    if isinstance(cf_entry, dict) and isinstance(cf_entry.get("selector"), dict) \
                            and cf_entry["selector"].get("metadata") == qref:
                        target = cf_entry
                        break
                if target is None:
                    target = {"properties": {}, "selector": {"metadata": qref}}
                    formatting.append(target)
                col_props = target.get("properties")
                if not isinstance(col_props, dict):
                    col_props = {}
                    target["properties"] = col_props
                col_props["alignment"] = {"expr": {"Literal": {"Value": "'Center'"}}}
        else:
            # Table: the table-wide "values" default entry (index 0, no
            # selector) covers any column that doesn't have its own
            # override.
            entries = objects.get("values")
            if not isinstance(entries, list) or not entries:
                entries = [{"properties": {}}]
                objects["values"] = entries
            entry = entries[0]
            if not isinstance(entry, dict):
                entry = {"properties": {}}
                entries[0] = entry
            props = entry.get("properties")
            if not isinstance(props, dict):
                props = {}
                entry["properties"] = props
            props["alignment"] = {"expr": {"Literal": {"Value": "'Center'"}}}

            # But a per-column "Specific column" override always wins over
            # that table-wide default for whichever column it targets -
            # confirmed against a real table where fixing only the
            # table-wide default left an overridden column visibly
            # unchanged. check_table_alignment only ever puts a queryRef in
            # value_query_refs here if a columnFormatting entry already
            # exists for it, so this only ever updates an existing entry in
            # place - it never invents a new override for a column that was
            # already correctly following the table-wide default.
            formatting = objects.get("columnFormatting")
            if isinstance(formatting, list):
                for qref in payload.get("value_query_refs", []):
                    for cf_entry in formatting:
                        if isinstance(cf_entry, dict) and isinstance(cf_entry.get("selector"), dict) \
                                and cf_entry["selector"].get("metadata") == qref:
                            col_props = cf_entry.get("properties")
                            if not isinstance(col_props, dict):
                                col_props = {}
                                cf_entry["properties"] = col_props
                            col_props["alignment"] = {"expr": {"Literal": {"Value": "'Center'"}}}
                            break

    elif finding.rule_id == RULE_DC_DISCLAIMER_BANNER_SHOWN:
        # visual_obj here is the Disclaimer Banner visualGroup's own
        # visual.json file root (format="visual_group_root") - "isHidden" is
        # a top-level sibling of "visualGroup", not nested inside it.
        # Removing the key entirely (rather than writing "isHidden": false)
        # matches the convention Power BI Desktop itself uses for "shown":
        # every currently-shown group/visual grounded in this project simply
        # has no isHidden key at all.
        visual_obj.pop("isHidden", None)

    elif finding.rule_id == RULE_VISUALIZE_POPULATION_BOOKMARK:
        # Standard per-visual dispatch (format="pbir") - visualLink lives
        # inside visual.visualContainerObjects, nested under the normal
        # "visual" key, unlike RULE_FILTERS_LOCKED_HIDDEN/RULE_DC_
        # DISCLAIMER_BANNER_SHOWN's file-root properties. Only ever
        # overwrites "type" and "bookmark" - navigationSection, show, and
        # everything else about the button are left exactly as they are.
        target = (finding.fix_payload or {}).get("target_bookmark_name", "")
        vco = visual_obj.get("visualContainerObjects")
        if not isinstance(vco, dict):
            vco = {}
            visual_obj["visualContainerObjects"] = vco
        link_list = vco.get("visualLink")
        if not isinstance(link_list, list) or not link_list or not isinstance(link_list[0], dict):
            link_list = [{"properties": {}}]
            vco["visualLink"] = link_list
        entry = link_list[0]
        props = entry.get("properties")
        if not isinstance(props, dict):
            props = {}
            entry["properties"] = props
        props["type"] = {"expr": {"Literal": {"Value": "'Bookmark'"}}}
        props["bookmark"] = {"expr": {"Literal": {"Value": f"'{target}'"}}}

    elif finding.rule_id == RULE_FILTER_VALUES_SORTED:
        # Dropping the explicit sortDefinition returns the slicer to Power
        # BI's default - sorted by its own field, ascending - the same state
        # 143 of 145 real slicers are already in.
        query = visual_obj.get("query")
        if isinstance(query, dict):
            query.pop("sortDefinition", None)

    elif finding.rule_id == RULE_PREVIOUS_PAGE_BACK:
        # Only rewrites the action "type" - leftover navigationSection /
        # bookmark properties are ignored by Power BI for a Back action and
        # are left exactly as found (same as the real compliant button).
        vco = visual_obj.get("visualContainerObjects")
        if not isinstance(vco, dict):
            vco = {}
            visual_obj["visualContainerObjects"] = vco
        links = vco.get("visualLink")
        if not isinstance(links, list) or not links or not isinstance(links[0], dict):
            links = [{"properties": {"show": {"expr": {"Literal": {"Value": "true"}}}}}]
            vco["visualLink"] = links
        props = links[0].get("properties")
        if not isinstance(props, dict):
            props = {}
            links[0]["properties"] = props
        props["type"] = {"expr": {"Literal": {"Value": "'Back'"}}}

    elif finding.rule_id == RULE_SEGMENTATION_SINGLE_SELECT:
        objects = visual_obj.get("objects")
        if not isinstance(objects, dict):
            objects = {}
            visual_obj["objects"] = objects
        sel = objects.get("selection")
        if not isinstance(sel, list) or not sel or not isinstance(sel[0], dict):
            sel = [{"properties": {}}]
            objects["selection"] = sel
        props = sel[0].get("properties")
        if not isinstance(props, dict):
            props = {}
            sel[0]["properties"] = props
        props["strictSingleSelect"] = {"expr": {"Literal": {"Value": "true"}}}


def apply_fixes(root_path: str, findings: list[Finding]) -> dict:
    """Applies a list of previously-scanned Findings. Groups by file so each
    file is read once, patched in memory, and written once. Backs up every
    affected file before touching it."""
    file_groups: dict[str, list[Finding]] = {}
    for f in findings:
        file_groups.setdefault(f.visual.file_path, []).append(f)

    backup_folder = backup_mod.backup_files(root_path, list(file_groups.keys()))

    applied, failed = [], []

    for file_path, group in file_groups.items():
        fmt = group[0].visual.format

        # A "delete_visual" finding (e.g. RULE_DC_PLAN_DESIGN_NO_PRIVACY_NOTE)
        # doesn't patch JSON at all - the fix IS removing the file. Handled
        # generically here (keyed on fix_payload["kind"], not a specific
        # rule_id) so any future rule can reuse it the same way. The file
        # was already snapshotted by backup_files() above, so this is
        # recoverable the same way every other fix is. Only removes the
        # visual's own folder, and only if it's now empty - never a
        # recursive delete of anything unexpected.
        if any((f.fix_payload or {}).get("kind") == "delete_visual" for f in group):
            try:
                if os.path.isfile(file_path):
                    os.remove(file_path)
                parent_dir = os.path.dirname(file_path)
                if os.path.isdir(parent_dir) and not os.listdir(parent_dir):
                    os.rmdir(parent_dir)
                applied.extend(f.id for f in group)
            except Exception as e:
                failed.extend({"id": f.id, "error": str(e)} for f in group)
            continue

        try:
            with open(file_path, "r", encoding="utf-8") as fh:
                root = json.load(fh)

            if fmt in ("theme", "report_root", "page_root", "visual_group_root"):
                # Theme JSON, report.json-level findings (export setting,
                # report-level filters), page.json-level findings (page-level
                # filters), and a visualGroup's own file (Disclaimer Banner
                # visibility - "isHidden" is a sibling of "visualGroup", not
                # nested inside a "visual" key that a group's file doesn't
                # even have) all have no per-visual wrapper - the thing to
                # patch is the file's root object itself, so patch it in
                # place and skip the visual-container bookkeeping below
                # entirely.
                for finding in group:
                    _apply_single(root, finding)
                with open(file_path, "w", encoding="utf-8") as fh:
                    json.dump(root, fh, indent=2, ensure_ascii=False)
                    fh.write("\n")
                applied.extend(f.id for f in group)
                continue

            by_visual: dict[str, list[Finding]] = {}
            for f in group:
                by_visual.setdefault(_visual_key(f.visual), []).append(f)

            for _key, vfindings in by_visual.items():
                visual = vfindings[0].visual
                config = None
                if fmt == "pbir":
                    visual_obj = root.get("visual", {})
                else:
                    section = root["sections"][visual.page_index]
                    container = section["visualContainers"][visual.container_index]
                    config = json.loads(container["config"])
                    visual_obj = config.get("singleVisual", {})

                for finding in vfindings:
                    if fmt == "pbir" and finding.rule_id == RULE_FILTERS_LOCKED_HIDDEN:
                        # filterConfig sits at the visual.json file root, a
                        # sibling of "visual" - not inside visual_obj itself.
                        _apply_single(root, finding)
                    else:
                        _apply_single(visual_obj, finding)

                if fmt != "pbir":
                    section = root["sections"][visual.page_index]
                    container = section["visualContainers"][visual.container_index]
                    container["config"] = json.dumps(config, ensure_ascii=False)

            with open(file_path, "w", encoding="utf-8") as fh:
                json.dump(root, fh, indent=2, ensure_ascii=False)
                fh.write("\n")

            applied.extend(f.id for f in group)
        except Exception as e:
            failed.extend({"id": f.id, "error": str(e)} for f in group)

    return {
        "backup_folder": backup_folder,
        "applied_count": len(applied),
        "failed_count": len(failed),
        "applied": applied,
        "failed": failed,
    }
