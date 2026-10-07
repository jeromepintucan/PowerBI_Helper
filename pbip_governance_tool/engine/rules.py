"""
Rule definitions.

Rule 1  slicer_header_disabled   - every SLICER's "Header icons" (Format pane
                                    General > Header icons > master toggle)
                                    must be OFF.
Rule 2  visual_header_focus_only - every NON-slicer visual must have its
                                    header icons ON, but with only the
                                    "Focus mode" icon enabled; every other
                                    icon (Options, Filter, Pin, Drill, See
                                    data) disabled.
Rule 3  sort_ascending           - any visual that already has an explicit
                                    sort configured must sort Ascending
                                    (visuals with no sort at all are left
                                    alone / reported as not applicable).
Rule 4  color_palette_compliance - see check_visual_colors / check_theme_colors.
Rule 5  export_disabled          - report.json's settings.exportDataMode must
                                    not allow full/underlying data export -
                                    "None" (fully disabled) or a
                                    summarized-only value are both compliant.
Rule 6  filters_locked_hidden    - every filter card (report-level,
                                    page-level, or per-visual - all three
                                    scopes shown in the Filters pane) must be
                                    both locked and hidden in view mode -
                                    whether or not it currently has an
                                    active `filter` condition set. A field
                                    left at "(All)" is still a real, visible
                                    card in the pane, and real dashboards
                                    routinely lock+hide those too. A
                                    slicer's own bound field is a special
                                    case: Power BI shows it as a card purely
                                    from the query definition and never
                                    writes a filterConfig entry for it until
                                    someone locks/hides it by hand at least
                                    once - so this rule also detects that
                                    "nothing to patch yet" case and creates
                                    the missing entry outright.
Rule 7  legend_bold              - a visual's legend must not have bold
                                    explicitly turned off (objects.legend[].
                                    properties.bold === false).
Rule 8  data_source_review       - every table's data-source connection
                                    (Power Query M `Source = ...` steps in
                                    the paired .SemanticModel's .tmdl files)
                                    must resolve to a path under the
                                    approved TCTO SharePoint location; a
                                    resolved path elsewhere, or a connection
                                    whose exact target can't be resolved at
                                    all, is flagged for review. Compliant
                                    sources aren't shown - only violations
                                    and unresolved cases are. There's no
                                    safe automatic "fix" for a data
                                    connection, so these findings are never
                                    fixable.
Rule 9  data_point_border        - pie/donut/bar/column-family charts must
                                    show a white, 0.5pt border around each
                                    data point (Format pane: "Slices >
                                    Border" for pie/donut, "Columns"/"Bars"
                                    > Border for bar/column charts - same
                                    underlying object regardless of the
                                    Format-pane section label).
Rule 10 table_values_centered    - a table (tableEx) visual's "Values" and
                                    "Column headers" Format-pane sections
                                    must both be set to Center alignment.
                                    Power BI's own default (nothing set) is
                                    NOT centered - text left, numbers right
                                    - so absence of the property is a
                                    violation, not a pass. Only the table-
                                    wide default is checked/fixed; per-
                                    column alignment overrides
                                    (columnFormatting[].properties.
                                    alignment) aren't touched.

Rule 11 visualize_population_bookmark - every "Visualize population"
                                    actionButton (matched by title) must
                                    bookmark to "Employee demographics".
                                    Runs on every dashboard; silent if the
                                    button doesn't exist.

Dashboard-family rule (DC & Benefit Affordability Dashboard only, opt-in via
the family dropdown - see DASHBOARD_FAMILY_RULE_IDS):
dc_benefit_disclaimer_banner_shown - the "Disclaimer Banner"
                                    visualGroup must not be hidden by default
                                    on a configurable set of pages (default:
                                    Benefit affordability, DC, Plan design).
                                    Only the group container's own visibility
                                    is checked - never its child visuals.

Notes / assumptions (confirmed with the user, and verified against a real
visual.json written by Power BI Desktop 2.12.0 schema):
  - "Header icons" for slicers = the standard Format-pane header section
    (visualHeader / vcObjects.visualHeader), not the slicer's separate
    clear-selection control. Ground truth: disabling it writes only
    `"show": false` inside visualHeader.properties - nothing else.
  - "Enabled only on focus mode" has no literal native PBI equivalent, and
    critically: Power BI Desktop does NOT write a dedicated property for the
    Focus mode icon at all, and does not write "show": true either - both
    are the default state, and Power BI only persists properties that
    differ from default. So the fix never touches "show" (unless it was
    explicitly turned off) and never invents a "focus mode" property; it
    only explicitly disables every *other* action icon. Verified property
    names for those other icons (schema 2.12.0): showOptionsMenu,
    showFilterRestatementButton, showPinButton, showSeeDataLayoutToggleButton,
    showDrillDownExpandButton, showDrillDownLevelButton, showDrillToggleButton,
    showDrillUpButton, showDrillRoleSelector, showCommentButton,
    showCopyVisualImageButton, showSetAlertButton, plus the diagnostic
    showVisualInformationButton/showVisualWarningButton/showVisualErrorButton.
  - Sorting fix only flips existing Descending sorts to Ascending; it never
    invents a new sort where none exists.
  - Visual *groups* (layout containers with a "visualGroup" key instead of
    "visual", e.g. a page's "Footer" group) are not real visuals and have no
    header icons - the scanner skips them entirely.
"""
from __future__ import annotations
import copy
import json
import os
import re
from typing import Any, Optional

from . import jsonutil
from .appdirs import resource_base_dir
from .models import Finding, VisualRef

# Bundled, read-only config - resource_base_dir() (not app_base_dir()) since
# a --onefile build extracts this to a temp folder on each launch rather
# than keeping it next to the .exe. Editing it means rebuilding the .exe.
_CONFIG_PATH = os.path.join(resource_base_dir(), "config", "rule_config.json")

RULE_SLICER_HEADER = "slicer_header_disabled"
RULE_VISUAL_HEADER_FOCUS = "visual_header_focus_only"
RULE_SORT_ASCENDING = "sort_ascending"
RULE_COLOR_PALETTE = "color_palette_compliance"
RULE_EXPORT_DISABLED = "export_disabled"
RULE_FILTERS_LOCKED_HIDDEN = "filters_locked_hidden"
RULE_LEGEND_BOLD = "legend_bold"
RULE_DATA_SOURCE_REVIEW = "data_source_review"
RULE_DATA_POINT_BORDER = "data_point_border"
RULE_TABLE_VALUES_CENTERED = "table_values_centered"

# Dashboard-family-specific rules (not generic - only relevant to one named
# dashboard's actual data model). Grounded against a real copy of the DC &
# Benefit Affordability Dashboard's .SemanticModel (see check_dc_
# headcount_measure / check_dc_baseline_cardinality below). Both are
# detection-only: they exist to catch a regression back to a known-bad
# state, not to auto-fix DAX logic or relationship structure. Excluded from
# check_all() and from ALL_RULE_IDS/run-everything - see service.run_scan(),
# which only runs them when explicitly requested (family dropdown selected).
RULE_DC_HEADCOUNT_MEASURE = "dc_benefit_headcount_measure_source"
RULE_DC_BASELINE_CARDINALITY = "dc_benefit_baseline_cardinality"
# Unlike the two above, this one IS auto-fixable (deletes the offending
# visual) - see check_dc_plan_design_no_privacy_note below.
RULE_DC_PLAN_DESIGN_NO_PRIVACY_NOTE = "dc_benefit_plan_design_no_privacy_note"
# Also auto-fixable (removes a single "isHidden" flag) - see
# check_dc_disclaimer_banner_shown below. Which pages this checks is
# user-configurable at scan time (the UI's page checkboxes for this rule),
# not hardcoded - _DEFAULT_DISCLAIMER_BANNER_PAGES below is only the
# fallback/default selection.
RULE_DC_DISCLAIMER_BANNER_SHOWN = "dc_benefit_disclaimer_banner_shown"

# Generic (all dashboards) rule 11 - see check_visualize_population_bookmark.
# Applies to every page of every project; a project with no "Visualize
# population" button at all simply produces nothing.
RULE_VISUALIZE_POPULATION_BOOKMARK = "visualize_population_bookmark"
# Generic (all dashboards) rule 12 - see check_filter_values_sorted. Every
# finding is fixable but never auto-selected: the user reviews each one and
# opts in.
RULE_FILTER_VALUES_SORTED = "filter_values_sorted"
# Generic (all dashboards) rule 13 - see check_visualize_population_orphaned.
# Fix deletes the button outright (same "delete_visual" mechanism as the
# Plan design privacy-note rule).
RULE_VISUALIZE_POPULATION_ORPHANED = "visualize_population_orphaned"
# Generic rules 14 and 15 - see check_previous_page_back /
# check_segmentation_single_select.
RULE_PREVIOUS_PAGE_BACK = "employee_demographics_previous_page_back"
RULE_SEGMENTATION_SINGLE_SELECT = "segmentation_single_select"

RULE_LABELS = {
    RULE_SLICER_HEADER: "Slicer header icons must be disabled",
    RULE_VISUAL_HEADER_FOCUS: "Visual header icons must show only Focus mode",
    RULE_SORT_ASCENDING: "Sort direction must be ascending",
    RULE_COLOR_PALETTE: "Visual colors must follow the approved brand palette",
    RULE_EXPORT_DISABLED: "Export must be disabled or restricted to summarized data",
    RULE_FILTERS_LOCKED_HIDDEN: "Filters must be locked and hidden",
    RULE_LEGEND_BOLD: "Visual legends must be bold",
    RULE_DATA_SOURCE_REVIEW: "Data source connections must be from the approved TCTO location",
    RULE_DATA_POINT_BORDER: "Chart data points must have a white, 0.5pt border",
    RULE_TABLE_VALUES_CENTERED: "Table values and column headers must be centered",
    RULE_DC_HEADCOUNT_MEASURE: "MeasureTable[Headcount] must reference Employee_Segments, not Baseline",
    RULE_DC_BASELINE_CARDINALITY: "Baseline ↔ Employee_Segments relationship must be 1:1",
    RULE_DC_PLAN_DESIGN_NO_PRIVACY_NOTE: "Plan design page must not have the Data privacy note card",
    RULE_DC_DISCLAIMER_BANNER_SHOWN: "Disclaimer Banner must be shown by default on the selected pages",
    RULE_VISUALIZE_POPULATION_BOOKMARK: "'Visualize population' button must bookmark to Employee demographics",
    RULE_FILTER_VALUES_SORTED: "Slicers must be sorted by their own field, ascending",
    RULE_VISUALIZE_POPULATION_ORPHANED: "If 'Employee demographics' is not available, visualize population should be removed",
    RULE_PREVIOUS_PAGE_BACK: "Employee demographics page must have a Previous page button with action type Back",
    RULE_SEGMENTATION_SINGLE_SELECT: "Segmentation tabs are single select only",
}

ALL_RULE_IDS = [
    RULE_SLICER_HEADER, RULE_VISUAL_HEADER_FOCUS, RULE_SORT_ASCENDING, RULE_COLOR_PALETTE,
    RULE_EXPORT_DISABLED, RULE_FILTERS_LOCKED_HIDDEN, RULE_LEGEND_BOLD, RULE_DATA_SOURCE_REVIEW,
    RULE_DATA_POINT_BORDER, RULE_TABLE_VALUES_CENTERED, RULE_VISUALIZE_POPULATION_BOOKMARK,
    RULE_FILTER_VALUES_SORTED, RULE_VISUALIZE_POPULATION_ORPHANED,
    RULE_PREVIOUS_PAGE_BACK, RULE_SEGMENTATION_SINGLE_SELECT,
]

# Visual types confirmed (or, for close siblings not directly sampled, very
# safely inferred - same Cartesian/pie visual family sharing the same
# capabilities.json "dataPoint" object) to expose a "Border" control under
# their data-point/slices/columns/bars Format-pane section. Grounded against
# a real DC & Benefit Affordability Dashboard project: every donutChart,
# hundredPercentStackedColumnChart, barChart and pieChart already had this
# configured; clusteredColumnChart/columnChart/lineStackedColumnComboChart
# mostly did but a few were missing it entirely. No table/card/slicer/
# textbox/gauge/plain lineChart/custom visual ever had this object at all -
# the rule is deliberately scoped to just this chart family.
_BORDER_APPLICABLE_VISUAL_TYPES = {
    "piechart", "donutchart",
    "barchart", "clusteredbarchart", "stackedbarchart", "hundredpercentstackedbarchart",
    "columnchart", "clusteredcolumnchart", "stackedcolumnchart", "hundredpercentstackedcolumnchart",
    "linestackedcolumncombochart", "lineclusteredcolumncombochart",
}

# Rules scoped to a specific dashboard family, keyed by the family id the UI
# sends. Kept separate from ALL_RULE_IDS since these don't apply generically.
DASHBOARD_FAMILY_RULE_IDS = {
    "dc_benefit": [
        RULE_DC_HEADCOUNT_MEASURE, RULE_DC_BASELINE_CARDINALITY, RULE_DC_PLAN_DESIGN_NO_PRIVACY_NOTE,
        RULE_DC_DISCLAIMER_BANNER_SHOWN,
    ],
}

# Default set of pages the Disclaimer Banner visibility rule checks, used
# only when the caller (the UI's page checkboxes) doesn't supply its own
# list. Grounded directly against the real DC & Benefit Affordability
# Dashboard: all three pages currently have a "Disclaimer Banner" visualGroup
# that's shown by default (no "isHidden" key at all on any of them right
# now) - this rule exists to catch a future regression back to hidden, not
# because a violation currently exists.
_DEFAULT_DISCLAIMER_BANNER_PAGES = ["Benefit affordability", "DC", "Plan design"]

# The button text (and, per Power BI's own convention - see
# _read_visual_title - its matching hidden Format-pane title) this rule
# matches on, and the bookmark it must always point to. Grounded directly
# against the real dashboard: 11 "Visualize population" actionButtons across
# 11 different pages all already point to a bookmark file literally named
# Bookmark4424418ac77b09114842.bookmark.json, whose own displayName is
# "Employee demographics" - resolved by display name rather than hardcoding
# that id, so this still works if the bookmark is ever re-exported with a
# different internal name. A 12th file matched a raw text search for
# "Visualize population" but turned out to be an unrelated "More details"
# button with leftover text in one of its style-state variants (hover/
# selected/etc) - not its actual title - confirming title-based matching
# (not a raw substring search) is required to avoid a false positive there.
_VISUALIZE_POPULATION_BUTTON_TITLE = "Visualize population"
_VISUALIZE_POPULATION_TARGET_BOOKMARK_DISPLAY_NAME = "Employee demographics"
# The page the button ultimately takes viewers to. Grounded: the real
# "Employee demographics" bookmark's activeSection is page 8757e3ee756c2018d6c6,
# whose displayName is "Employee Demographics" (capital D) - so the page is
# matched case-insensitively.
_VISUALIZE_POPULATION_TARGET_PAGE_DISPLAY_NAME = "Employee demographics"

# Verified directly from a real visual.json written by Power BI Desktop
# (visualContainer schema 2.12.0). "Focus mode" is deliberately absent: PBI
# has no dedicated property for it, so it can only ever be left at its
# (enabled) default - never set explicitly.
_DEFAULT_ICON_PROPS = {
    "visual_information": "showVisualInformationButton",
    "visual_warning": "showVisualWarningButton",
    "visual_error": "showVisualErrorButton",
    "drill_role_selector": "showDrillRoleSelector",
    "drill_up": "showDrillUpButton",
    "drill_toggle": "showDrillToggleButton",
    "drill_down_level": "showDrillDownLevelButton",
    "drill_down_expand": "showDrillDownExpandButton",
    "pin": "showPinButton",
    "see_data": "showSeeDataLayoutToggleButton",
    "options": "showOptionsMenu",
    "filter": "showFilterRestatementButton",
    "comment": "showCommentButton",
    "copy_image": "showCopyVisualImageButton",
    "set_alert": "showSetAlertButton",
}
_DEFAULT_NO_HEADER_TYPES = {
    "textbox", "image", "shape", "actionbutton", "basicshape", "wordcloud", "qnavisual",
}
# Source of truth: the "Power BI Sequence" sheet in
# WTW_Data_Visualization_Color_Catalog.xlsx (order matters - it's meant to be
# used as-is for a report theme's dataColors array).
_DEFAULT_APPROVED_PALETTE = [
    "#521380", "#24A19B", "#4A91F4", "#CA8100", "#E76F00",
    "#E954D3", "#F75784", "#995BC5", "#1A71EF", "#C25700",
]
# Object names (as they appear under a visual's "objects", or under a theme's
# visualStyles.<type>.<variant>) that hold actual data/series colors - NOT
# text, labels, axes, legends, titles or gridlines. Deliberately excludes
# "fill" - on chart-type visuals it isn't used, and on UI elements (buttons,
# shapes, page navigators) it's chrome/branding, not a data color. Editable
# via config/rule_config.json.
_DEFAULT_COLOR_OBJECT_ALLOWLIST = [
    "dataPoint", "colors", "plotArea", "dataBars", "tree", "ribbonChart",
]
# report.json's settings.exportDataMode values that are NOT a violation.
# "None" (export fully disabled) is definitely correct - confirmed directly
# from a real report.json. "AllowSummarized" (summarized-data-only export) is
# the best-evidenced name for the other value the user explicitly said should
# also be acceptable ("we can allow summarized data or current layout data") -
# Microsoft doesn't publish an authoritative enum list for this property, so
# this is deliberately editable via config/rule_config.json if a project uses
# a different exact string for that same "summarized only" setting.
_DEFAULT_EXPORT_COMPLIANT_VALUES = ["None", "AllowSummarized"]
# The TCTO (Trusted Central... data location) SharePoint folder every table's
# source file is expected to live under. Confirmed directly from the real DC
# & Benefit Affordability Dashboard's expressions.tmdl - every table's
# `Source = Excel.Workbook(Web.Contents(SourceFile), ...)` resolves this
# shared `SourceFile` parameter to a path under this exact folder. A source
# that resolves to a path NOT starting with one of these prefixes gets
# flagged for review; one that does is treated as compliant and not shown at
# all. Editable via config/rule_config.json (e.g. to add another approved
# TCTO subfolder for a different project).
_DEFAULT_TCTO_PATH_PREFIXES = [
    "https://wtwonline.sharepoint.com/sites/tctPA_RETNADataI/Projects/",
]
# Pages whose display name contains any of these substrings (case-
# insensitive) are skipped entirely by Rule 10 (table/matrix alignment).
# Grounded against a real page, "Info (Plan design)" in the DC & Benefit
# Affordability Dashboard: its Matrix is a flat glossary list (just
# Category/Description Row-header fields, no Values well at all), so it has
# no "Specific column" override in the Format pane to write a fix into at
# all - the only alignment control there lives under a "Row headers"
# section this rule doesn't yet know how to write safely. Editable via
# config/rule_config.json.
_DEFAULT_ALIGNMENT_EXCLUDED_PAGE_SUBSTRINGS = ["info"]


def _load_config():
    icons, no_header = dict(_DEFAULT_ICON_PROPS), set(_DEFAULT_NO_HEADER_TYPES)
    palette, color_objs = list(_DEFAULT_APPROVED_PALETTE), list(_DEFAULT_COLOR_OBJECT_ALLOWLIST)
    export_ok = list(_DEFAULT_EXPORT_COMPLIANT_VALUES)
    tcto_prefixes = list(_DEFAULT_TCTO_PATH_PREFIXES)
    align_excluded_pages = list(_DEFAULT_ALIGNMENT_EXCLUDED_PAGE_SUBSTRINGS)
    try:
        with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        if isinstance(cfg.get("icons"), dict) and cfg["icons"]:
            icons = cfg["icons"]
        if isinstance(cfg.get("no_header_visual_types"), list):
            no_header = {t.lower() for t in cfg["no_header_visual_types"]}
        if isinstance(cfg.get("approved_palette"), list) and cfg["approved_palette"]:
            palette = [str(c).upper() for c in cfg["approved_palette"]]
        if isinstance(cfg.get("color_object_allowlist"), list) and cfg["color_object_allowlist"]:
            color_objs = cfg["color_object_allowlist"]
        if isinstance(cfg.get("export_compliant_values"), list) and cfg["export_compliant_values"]:
            export_ok = [str(v) for v in cfg["export_compliant_values"]]
        if isinstance(cfg.get("tcto_path_prefixes"), list) and cfg["tcto_path_prefixes"]:
            tcto_prefixes = [str(v) for v in cfg["tcto_path_prefixes"]]
        if isinstance(cfg.get("alignment_excluded_page_substrings"), list):
            align_excluded_pages = [str(v).lower() for v in cfg["alignment_excluded_page_substrings"]]
    except Exception:
        pass  # fall back to hardcoded defaults; config file is optional
    return icons, no_header, palette, color_objs, export_ok, tcto_prefixes, align_excluded_pages


# The icon properties that live under visualHeader / vcObjects.visualHeader,
# all of which must be disabled for rule 2 (everything except Focus mode,
# which is never written - see module docstring). Editable via
# config/rule_config.json.
(ICON_PROPS, NO_HEADER_VISUAL_TYPES, APPROVED_PALETTE, COLOR_OBJECT_ALLOWLIST, EXPORT_COMPLIANT_VALUES,
 TCTO_PATH_PREFIXES, ALIGNMENT_EXCLUDED_PAGE_SUBSTRINGS) = _load_config()
APPROVED_PALETTE_SET = set(APPROVED_PALETTE)


def header_key_for_format(fmt: str) -> str:
    return "visualContainerObjects" if fmt == "pbir" else "vcObjects"


def load_visual_json(visual: VisualRef):
    """Returns (file_root_json, visual_obj) for the given VisualRef, freshly
    read from disk. visual_obj is the nested dict that holds visualType /
    visualContainerObjects / query, etc. for PBIR, or singleVisual for legacy."""
    with open(visual.file_path, "r", encoding="utf-8") as f:
        root = json.load(f)

    if visual.format == "pbir":
        return root, root.get("visual", {})

    # legacy: root is the whole report.json; drill down to the right
    # visualContainer, then json-decode its "config" string.
    section = root["sections"][visual.page_index]
    container = section["visualContainers"][visual.container_index]
    config = json.loads(container["config"])
    return root, config.get("singleVisual", {})


def save_visual_json(visual: VisualRef, root: dict, visual_obj: dict, config: Optional[dict] = None) -> None:
    if visual.format == "pbir":
        root["visual"] = visual_obj
        with open(visual.file_path, "w", encoding="utf-8") as f:
            json.dump(root, f, indent=2, ensure_ascii=False)
            f.write("\n")
        return

    # legacy: config is the full decoded "config" object; visual_obj IS
    # config["singleVisual"], already mutated in place since dicts are
    # references. Re-encode config back into the container's config STRING.
    section = root["sections"][visual.page_index]
    container = section["visualContainers"][visual.container_index]
    container["config"] = json.dumps(config, ensure_ascii=False)
    with open(visual.file_path, "w", encoding="utf-8") as f:
        json.dump(root, f, indent=2, ensure_ascii=False)
        f.write("\n")


def _get_header_properties(visual_obj: dict, fmt: str, ensure: bool):
    """Return the `properties` dict inside visualHeader[0], or {} if absent
    and ensure=False. If ensure=True, creates the structure as needed and
    returns the live dict for mutation."""
    key = header_key_for_format(fmt)
    container = visual_obj.get(key)
    if container is None:
        if not ensure:
            return {}
        container = {}
        visual_obj[key] = container

    header_list = container.get("visualHeader")
    if not header_list:
        if not ensure:
            return {}
        header_list = [{"properties": {}}]
        container["visualHeader"] = header_list

    entry = header_list[0]
    props = entry.get("properties")
    if props is None:
        if not ensure:
            return {}
        props = {}
        entry["properties"] = props
    return props


def is_slicer(visual_type: str) -> bool:
    return "slicer" in (visual_type or "").lower()


def applies_to_header_rules(visual_type: str) -> bool:
    return (visual_type or "").lower() not in NO_HEADER_VISUAL_TYPES


def applies_to_border_rule(visual_type: str) -> bool:
    return (visual_type or "").lower() in _BORDER_APPLICABLE_VISUAL_TYPES


def check_header_rules(visual: VisualRef) -> list[Finding]:
    findings: list[Finding] = []
    if not applies_to_header_rules(visual.visual_type):
        return findings

    _, visual_obj = load_visual_json(visual)
    props = _get_header_properties(visual_obj, visual.format, ensure=False)

    if is_slicer(visual.visual_type):
        show = jsonutil.read_bool(props.get("show"))
        is_disabled = show is False
        if not is_disabled:
            findings.append(Finding(
                rule_id=RULE_SLICER_HEADER,
                rule_label=RULE_LABELS[RULE_SLICER_HEADER],
                visual=visual,
                detail="Header icons are currently "
                       + ("shown (default)" if show is None else "enabled")
                       + " on this slicer.",
                fix_payload={},
            ))
    else:
        # "show" (the header itself) must not be explicitly disabled. Absent
        # = default = on, which is correct - only an explicit False is wrong.
        show = jsonutil.read_bool(props.get("show"))
        header_off = show is False

        bad_icons = []
        for label, prop_name in ICON_PROPS.items():
            val = jsonutil.read_bool(props.get(prop_name))
            if val is not False:
                bad_icons.append(label.replace("_", " ").title())

        if header_off or bad_icons:
            chips = (["Header off"] if header_off else []) + bad_icons
            if header_off:
                summary = "Header is off - should stay on, showing only Focus mode"
            else:
                summary = f"{len(bad_icons)} icon{'s' if len(bad_icons) != 1 else ''} still enabled"
            findings.append(Finding(
                rule_id=RULE_VISUAL_HEADER_FOCUS,
                rule_label=RULE_LABELS[RULE_VISUAL_HEADER_FOCUS],
                visual=visual,
                detail=summary,
                items=chips,
                fix_payload={},
            ))
    return findings


def _walk_for_descending(obj, path):
    """Yield (path_list, current_repr) for direction fields set to Descending.

    Skips any sort whose enclosing sortDefinition is Power BI's own
    automatic default (`"isDefaultSort": true`) rather than something a
    report author actually configured. Verified against a real 1200+ visual
    production PBIP: every Card visual (and plenty of others - lineChart
    included) carries an auto-generated "sort by the first measure,
    Descending, isDefaultSort: true" query artifact. It has no user-facing
    sort control at all (Cards can't be sorted from the UI) and doesn't
    reflect an author's choice, so flagging/fixing it would be a false
    positive. A real, author-set sort either omits `isDefaultSort` entirely
    or has it explicitly `false` - both cases still get walked normally."""
    if isinstance(obj, dict):
        if isinstance(obj.get("sort"), list) and obj.get("isDefaultSort") is True:
            return
        for k, v in obj.items():
            if k.lower() == "direction":
                if v == "Descending" or v == 2:
                    yield (path + [k], v)
            else:
                yield from _walk_for_descending(v, path + [k])
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            yield from _walk_for_descending(item, path + [i])


def check_sort_rule(visual: VisualRef) -> list[Finding]:
    """Rule 3. Slicers are skipped - their sorting is owned by rule 12
    (check_filter_values_sorted), which checks both direction AND which
    field a slicer sorts by, so a descending slicer isn't flagged twice
    with two conflicting fixes."""
    findings = []
    if is_slicer(visual.visual_type):
        return findings
    _, visual_obj = load_visual_json(visual)
    for path, current in _walk_for_descending(visual_obj, []):
        findings.append(Finding(
            rule_id=RULE_SORT_ASCENDING,
            rule_label=RULE_LABELS[RULE_SORT_ASCENDING],
            visual=visual,
            detail=f"Sort direction is Descending at `{'/'.join(map(str, path))}`.",
            fix_payload={"path": path, "was": current},
        ))
    return findings


def _extract_literal_hex(color_value: Any) -> Optional[str]:
    """Returns an uppercased '#RRGGBB' if color_value is a literal hex color
    (plain string or {"expr":{"Literal":{"Value":"'#RRGGBB'"}}}). Returns
    None for anything else, notably a {"expr":{"ThemeDataColor":...}}
    reference - that's a visual correctly inheriting from the report theme,
    not a hardcoded override, so it's not flagged here (the theme itself is
    checked separately by check_theme_colors)."""
    if isinstance(color_value, str) and color_value.startswith("#"):
        return color_value.upper()
    if isinstance(color_value, dict):
        lit = color_value.get("expr", {}).get("Literal", {}).get("Value")
        if isinstance(lit, str):
            s = lit.strip().strip("'\"")
            if s.startswith("#"):
                return s.upper()
    return None


def _find_hex_paths(node: Any, path: list):
    """Yields (path_to_color_value, hex) for every literal hex color found
    anywhere under `node`, matching Power BI's `{"solid": {"color": <value>}}`
    fill/color shape. path_to_color_value is relative to `node` and always
    ends in ["solid", "color"] - exactly what a fixer needs to walk down to
    and overwrite. Stops descending once inside a color object (no reason to
    look further)."""
    if isinstance(node, dict):
        solid = node.get("solid")
        if isinstance(solid, dict) and "color" in solid:
            hexv = _extract_literal_hex(solid["color"])
            if hexv:
                yield (path + ["solid", "color"], hexv)
            return
        for k, v in node.items():
            yield from _find_hex_paths(v, path + [k])
    elif isinstance(node, list):
        for i, item in enumerate(node):
            yield from _find_hex_paths(item, path + [i])


def _hex_to_rgb(hexv: str):
    h = hexv.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _nearest_palette_color(hexv: str) -> str:
    """Closest approved-palette color by plain Euclidean RGB distance - the
    deterministic, explainable replacement used when the user opts a
    manual-review color finding into "Fix Selected"."""
    try:
        r1, g1, b1 = _hex_to_rgb(hexv)
    except Exception:
        return APPROVED_PALETTE[0]
    best, best_d = APPROVED_PALETTE[0], None
    for c in APPROVED_PALETTE:
        r2, g2, b2 = _hex_to_rgb(c)
        d = (r1 - r2) ** 2 + (g1 - g2) ** 2 + (b1 - b2) ** 2
        if best_d is None or d < best_d:
            best, best_d = c, d
    return best


def check_visual_colors(visual: VisualRef) -> list[Finding]:
    """Rule 4 (per-visual half): flags literal/hardcoded colors on a visual's
    own data-color properties (dataPoint, colors, plotArea, ...) that aren't
    in the approved palette. Deliberately does NOT look at text/label/axis/
    legend/title/gridline properties.

    This IS fixable (snap each occurrence to its nearest approved-palette
    color by RGB distance), but auto_select=False - there's no way to know
    the *intended* color, so it's left unchecked by default and excluded
    from "select all" / "select all pages"; the user opts individual ones in
    after eyeballing whether a nearest-match snap is acceptable."""
    findings: list[Finding] = []
    if not APPROVED_PALETTE:
        return findings
    _, visual_obj = load_visual_json(visual)
    objects = visual_obj.get("objects")
    if not isinstance(objects, dict):
        return findings

    occurrences = []
    seen: dict[str, str] = {}
    for obj_name in COLOR_OBJECT_ALLOWLIST:
        if obj_name in objects:
            for relpath, hexv in _find_hex_paths(objects[obj_name], []):
                if hexv not in APPROVED_PALETTE_SET:
                    nearest = seen.get(hexv) or _nearest_palette_color(hexv)
                    seen[hexv] = nearest
                    occurrences.append({"path": ["objects", obj_name] + relpath, "hex": hexv, "nearest": nearest})

    if occurrences:
        findings.append(Finding(
            rule_id=RULE_COLOR_PALETTE,
            rule_label=RULE_LABELS[RULE_COLOR_PALETTE],
            visual=visual,
            detail=f"Uses {len(seen)} color{'s' if len(seen) != 1 else ''} not in the "
                   f"approved WTW palette.",
            items=list(seen.keys()),
            expected=list(seen.values()),
            fixable=True,
            auto_select=False,
            fix_payload={"kind": "nearest_match_colors", "occurrences": occurrences},
        ))
    return findings


def _find_theme_file(report_folder: str) -> tuple[Optional[str], str]:
    """Resolves the report's custom theme JSON file on disk, if it has one.
    Returns (path, "") on success, or (None, reason) - the reason is
    surfaced as a scan warning so a silent "theme not checked" outcome is
    always explainable instead of just quietly producing zero findings."""
    report_json_path = os.path.join(report_folder, "definition", "report.json")
    if not os.path.isfile(report_json_path):
        report_json_path = os.path.join(report_folder, "report.json")
    if not os.path.isfile(report_json_path):
        return None, f"no report.json found (looked for {report_json_path})"
    try:
        with open(report_json_path, "r", encoding="utf-8") as f:
            report_json = json.load(f)
    except Exception as e:
        return None, f"could not parse {report_json_path}: {e}"

    custom = report_json.get("themeCollection", {}).get("customTheme")
    if not isinstance(custom, dict):
        return None, ("report.json has no themeCollection.customTheme - this report may only be using "
                       "a built-in Power BI theme, which has no local file to check")
    theme_name = custom.get("name")
    theme_type = custom.get("type", "RegisteredResources")

    checked_paths = []
    for pkg in report_json.get("resourcePackages", []):
        if pkg.get("type") != theme_type:
            continue
        for item in pkg.get("items", []):
            if item.get("type") == "CustomTheme" or item.get("name") == theme_name:
                rel = item.get("path") or theme_name
                if not rel:
                    continue
                candidate = os.path.join(report_folder, "StaticResources", pkg.get("name", theme_type), rel)
                checked_paths.append(candidate)
                if os.path.isfile(candidate):
                    return candidate, ""
    if checked_paths:
        return None, (f"customTheme references '{theme_name}' but no file was found on disk - "
                       f"looked at: {', '.join(checked_paths)}")
    return None, (f"customTheme references '{theme_name}' (type '{theme_type}') but no matching entry "
                   f"was found in report.json's resourcePackages")


_THEME_CHROME_VISUAL_TYPES = {"shape", "basicshape", "actionbutton", "image"}


def _count_theme_dependent_ui_chrome(report_folder: str, max_examples: int = 3) -> tuple[int, list]:
    """Counts shape/button/icon-style visuals (NOT charts) anywhere in the
    report that reference a theme color (`"ThemeDataColor"` anywhere in
    their JSON) - these are never touched by this tool directly, but they
    share the exact same theme palette a chart's data colors come from, so
    overwriting the theme's `dataColors` array to fix a chart's colors will
    also silently change these on next render, purely as a side effect of
    how Power BI resolves theme colors (not something this tool writes).

    Confirmed directly against a real report: an "Info" actionButton's
    icon/text color, and a basicShape tile's fill/outline, both use
    `ThemeDataColor` bindings just like a real chart's data-point fill does
    - Power BI doesn't distinguish "chart color" from "shape color" at the
    theme level, they're the same shared list. There's currently no
    reliably-confirmed formula for exactly which resulting color a given
    ColorId will render as outside of a chart's own data-point context (a
    border rule elsewhere in this file found ColorId:0 behaves as the
    theme's background/white slot, not literally `dataColors[0]` - the
    exact mapping for other ColorId values isn't confirmed with the same
    confidence), so this deliberately only counts affected objects and
    surfaces a warning - it does not attempt to "protect" them by rewriting
    their color, which risks freezing them at a wrong guessed value."""
    pages_dir = os.path.join(report_folder, "definition", "pages")
    if not os.path.isdir(pages_dir):
        return 0, []
    count = 0
    examples: list = []
    seen_examples: set = set()
    for root_dir, _dirs, files in os.walk(pages_dir):
        if "visual.json" not in files:
            continue
        path = os.path.join(root_dir, "visual.json")
        try:
            with open(path, "r", encoding="utf-8") as f:
                text = f.read()
        except Exception:
            continue
        if "ThemeDataColor" not in text:
            continue
        m = re.search(r'"visualType"\s*:\s*"([^"]+)"', text)
        vtype = m.group(1) if m else ""
        if vtype.lower() not in _THEME_CHROME_VISUAL_TYPES:
            continue
        count += 1
        if len(examples) < max_examples:
            title_m = re.search(r'"text"\s*:\s*\{\s*"expr"\s*:\s*\{\s*"Literal"\s*:\s*\{\s*"Value"\s*:\s*"\'([^\']*)\'"', text)
            article = "an" if vtype[:1].lower() in "aeiou" else "a"
            label = f"{article} {vtype} ('{title_m.group(1)}')" if title_m else f"{article} {vtype}"
            if label not in seen_examples:
                seen_examples.add(label)
                examples.append(label)
    return count, examples


def check_theme_colors(report_folder: str, warnings: Optional[list] = None) -> list[Finding]:
    """Rule 4 (theme half): (a) the report's custom theme's `dataColors`
    array must match the approved palette exactly, in order - this IS
    fixable, but only auto-selected when nothing else in the report shares
    that palette (see _count_theme_dependent_ui_chrome - shapes/icons/
    buttons using "Theme colors" would silently change too, so if any exist
    this becomes a manual-review item instead, same as an unresolved
    hardcoded color); (b) any per-visual-type default style baked into the
    theme (e.g. every columnChart defaulting to some other purple) that
    uses a non-approved color is flagged for manual review, same reasoning
    as check_visual_colors.

    If `warnings` is given, appends a human-readable reason whenever the
    theme couldn't be located/parsed at all, so "0 theme findings" is always
    explainable (rather than silently meaning either "compliant" or "not
    checked" with no way to tell them apart)."""
    findings: list[Finding] = []
    report_name = os.path.basename(report_folder)
    if not APPROVED_PALETTE:
        return findings
    theme_path, reason = _find_theme_file(report_folder)
    if not theme_path:
        if warnings is not None:
            warnings.append(f"Color palette: could not check '{report_name}''s report theme - {reason}.")
        return findings
    try:
        with open(theme_path, "r", encoding="utf-8") as f:
            theme = json.load(f)
    except Exception as e:
        if warnings is not None:
            warnings.append(f"Color palette: found theme file for '{report_name}' but could not parse it - {e}.")
        return findings

    def _theme_visual(visual_id: str, visual_type: str, title: str) -> VisualRef:
        return VisualRef(
            id=f"{report_name}|__theme__|{visual_id}",
            report_name=report_name,
            report_path=report_folder,
            page_id="__theme__",
            page_name="(Report Theme)",
            visual_id=visual_id,
            visual_type=visual_type,
            title=title,
            file_path=theme_path,
            format="theme",
        )

    current = [str(c).upper() for c in theme.get("dataColors", []) if isinstance(c, str)]
    if current != APPROVED_PALETTE:
        chrome_count, chrome_examples = _count_theme_dependent_ui_chrome(report_folder)
        if chrome_count:
            examples = ", ".join(chrome_examples)
            detail = (
                "Report theme's dataColors sequence doesn't match the approved WTW palette "
                "(order matters). Heads up: this palette is shared report-wide - Power BI "
                f"resolves it at render time, so fixing it will also change the color of "
                f"{chrome_count} non-chart shape/icon/button object{'s' if chrome_count != 1 else ''} "
                f"that reference a theme color too (e.g. {examples}), not just charts. There's no "
                "reliable way yet to tell which theme color slot a shape 'means' versus a chart, so "
                "review before applying - this isn't auto-selected by default for that reason."
            )
            auto_select = False
        else:
            detail = "Report theme's dataColors sequence doesn't match the approved WTW palette (order matters)."
            auto_select = True
        findings.append(Finding(
            rule_id=RULE_COLOR_PALETTE,
            rule_label=RULE_LABELS[RULE_COLOR_PALETTE],
            visual=_theme_visual("dataColors", "theme", os.path.basename(theme_path)),
            detail=detail,
            items=current,
            expected=APPROVED_PALETTE,
            fixable=True,
            auto_select=auto_select,
            fix_payload={"kind": "theme_data_colors", "expected": APPROVED_PALETTE},
        ))

    occurrences_by_type: dict[str, list] = {}
    visual_styles = theme.get("visualStyles")
    if isinstance(visual_styles, dict):
        for vtype, variants in visual_styles.items():
            if not isinstance(variants, dict):
                continue
            for variant_key, variant_props in variants.items():
                if not isinstance(variant_props, dict):
                    continue
                for obj_name in COLOR_OBJECT_ALLOWLIST:
                    if obj_name in variant_props:
                        for relpath, hexv in _find_hex_paths(variant_props[obj_name], []):
                            if hexv not in APPROVED_PALETTE_SET:
                                full_path = ["visualStyles", vtype, variant_key, obj_name] + relpath
                                nearest = _nearest_palette_color(hexv)
                                occurrences_by_type.setdefault(vtype, []).append(
                                    {"path": full_path, "hex": hexv, "nearest": nearest}
                                )

    for vtype, occs in occurrences_by_type.items():
        seen: dict[str, str] = {}
        for o in occs:
            seen.setdefault(o["hex"], o["nearest"])
        findings.append(Finding(
            rule_id=RULE_COLOR_PALETTE,
            rule_label=RULE_LABELS[RULE_COLOR_PALETTE],
            visual=_theme_visual(f"style:{vtype}", f"theme style · {vtype}", f"Theme default style - {vtype}"),
            detail=f"Theme's default style for '{vtype}' visuals uses a color not in the "
                   f"approved palette.",
            items=list(seen.keys()),
            expected=list(seen.values()),
            fixable=True,
            auto_select=False,
            fix_payload={"kind": "nearest_match_colors", "occurrences": occs},
        ))

    return findings


def _find_report_json(report_folder: str) -> Optional[str]:
    path = os.path.join(report_folder, "definition", "report.json")
    if os.path.isfile(path):
        return path
    path = os.path.join(report_folder, "report.json")
    return path if os.path.isfile(path) else None


def check_export_disabled(report_folder: str, warnings: Optional[list] = None) -> list[Finding]:
    """Rule 5: report.json's settings.exportDataMode must not be a value that
    allows full/underlying-data export. Missing entirely also counts as a
    violation - Power BI's own default when nothing is set is unrestricted
    export, which is the least-restrictive (and least governed) state."""
    findings: list[Finding] = []
    report_name = os.path.basename(report_folder)
    report_json_path = _find_report_json(report_folder)
    if not report_json_path:
        return findings
    try:
        with open(report_json_path, "r", encoding="utf-8") as f:
            report_json = json.load(f)
    except Exception as e:
        if warnings is not None:
            warnings.append(f"Export setting: could not parse {report_json_path}: {e}.")
        return findings

    current = report_json.get("settings", {}).get("exportDataMode")
    if current in EXPORT_COMPLIANT_VALUES:
        return findings

    visual = VisualRef(
        id=f"{report_name}|__settings__|exportDataMode",
        report_name=report_name,
        report_path=report_folder,
        page_id="__report_settings__",
        page_name="(Report Settings)",
        visual_id="exportDataMode",
        visual_type="settings",
        title="Report export setting",
        file_path=report_json_path,
        format="report_root",
    )
    detail = (
        f"Export is set to '{current}', which permits exporting underlying data."
        if current is not None
        else "No export restriction is set (report.json has no settings.exportDataMode), "
             "which defaults to unrestricted export."
    )
    findings.append(Finding(
        rule_id=RULE_EXPORT_DISABLED,
        rule_label=RULE_LABELS[RULE_EXPORT_DISABLED],
        visual=visual,
        detail=detail,
        items=[str(current) if current is not None else "(not set)"],
        expected=["None"],
        fixable=True,
        auto_select=True,
        fix_payload={"kind": "export_data_mode", "value": "None"},
    ))
    return findings


def _filter_label(filt: dict) -> str:
    """Best-effort human-readable label for a filter entry - the column or
    measure name it's filtering on, falling back to its internal name."""
    field = filt.get("field", {})
    if isinstance(field, dict):
        for key in ("Column", "Measure", "HierarchyLevel"):
            node = field.get(key)
            if isinstance(node, dict) and node.get("Property"):
                return str(node["Property"])
    return str(filt.get("name", "filter"))


def _find_unlocked_active_filters(root: dict):
    """Yields (index, filt) for entries in root['filterConfig']['filters']
    that aren't both locked AND hidden in view mode - every filter card that
    shows up in the Filters pane, whether or not it currently has a real
    `filter` condition set. A field added to a filter well but left at
    "(All)" has no `filter` key yet, but it's still a real, visible card in
    the pane - and real-world dashboards routinely lock+hide it anyway as a
    blanket convention (confirmed against a report where every filter card
    on a visual, none of which had an active condition, was deliberately
    locked and hidden - and a second visual on the same report where two
    "(All)"-state cards were hidden but not locked, a real compliance gap
    this rule must catch). So every entry is checked, regardless of whether
    it has a `filter` key."""
    filters = (root.get("filterConfig") or {}).get("filters")
    if not isinstance(filters, list):
        return
    for i, filt in enumerate(filters):
        if not isinstance(filt, dict):
            continue
        locked = jsonutil.read_bool(filt.get("isLockedInViewMode")) is True
        hidden = jsonutil.read_bool(filt.get("isHiddenInViewMode")) is True
        if not (locked and hidden):
            yield i, filt


def check_report_filters(report_folder: str, warnings: Optional[list] = None) -> list[Finding]:
    """Rule 6 (report-level half): every filter card in the report-level
    filter pane (report.json's filterConfig.filters, a sibling of
    themeCollection at the file root) - active or left at "(All)" - must be
    locked and hidden."""
    findings: list[Finding] = []
    report_name = os.path.basename(report_folder)
    report_json_path = _find_report_json(report_folder)
    if not report_json_path:
        return findings
    try:
        with open(report_json_path, "r", encoding="utf-8") as f:
            report_json = json.load(f)
    except Exception as e:
        if warnings is not None:
            warnings.append(f"Filter lock/hide: could not parse {report_json_path}: {e}.")
        return findings

    occurrences = list(_find_unlocked_active_filters(report_json))
    if not occurrences:
        return findings

    visual = VisualRef(
        id=f"{report_name}|__filters__|report",
        report_name=report_name,
        report_path=report_folder,
        page_id="__report_filters__",
        page_name="(Report Filters)",
        visual_id="report-filters",
        visual_type="filters",
        title="Report-level filter pane",
        file_path=report_json_path,
        format="report_root",
    )
    items = [_filter_label(f) for _, f in occurrences]
    n = len(occurrences)
    findings.append(Finding(
        rule_id=RULE_FILTERS_LOCKED_HIDDEN,
        rule_label=RULE_LABELS[RULE_FILTERS_LOCKED_HIDDEN],
        visual=visual,
        detail=f"{n} report-level filter{'s' if n != 1 else ''} "
               f"{'are' if n != 1 else 'is'} not both locked and hidden.",
        items=items,
        fixable=True,
        auto_select=True,
        fix_payload={"kind": "lock_hide_filters", "indices": [i for i, _ in occurrences]},
    ))
    return findings


def _field_identity(field_obj) -> Optional[tuple]:
    """Extracts a (kind, entity, property) identity tuple from a PBIR field
    reference (a query projection's `field`, or a filterConfig entry's
    `field`), so the two can be matched even though everything else about
    their wrapping JSON legitimately differs. Returns None if the shape
    isn't a plain Column/Measure/HierarchyLevel reference - rare for a
    slicer's own bound field, and deliberately left unmatched rather than
    guessed at."""
    if not isinstance(field_obj, dict):
        return None
    for kind in ("Column", "Measure", "HierarchyLevel"):
        node = field_obj.get(kind)
        if isinstance(node, dict):
            entity = ((node.get("Expression") or {}).get("SourceRef") or {}).get("Entity")
            prop = node.get("Property")
            if entity and prop:
                return (kind, entity, prop)
    return None


def _filter_type_for_field(field_obj) -> str:
    """Mirrors Power BI's own convention for a filterConfig entry's "type":
    a plain Column/HierarchyLevel reference is "Categorical"; a Measure
    reference is "Advanced" - confirmed against every real entry seen in
    this project (e.g. a Column-based segment filter is "Categorical", a
    Measure-based one like "Headcount" is "Advanced")."""
    if isinstance(field_obj, dict) and isinstance(field_obj.get("Measure"), dict):
        return "Advanced"
    return "Categorical"


def _visual_field_projections(visual_obj: dict) -> list[dict]:
    """Returns every field dict a visual's query is bound to, across every
    role in queryState (Category, Values, Y, Rows, Columns, Series,
    Tooltips, etc. - whatever the visual type actually uses). Confirmed
    against several real, different visual types that Power BI always
    shows EVERY one of these as its own card in that visual's Filters
    pane, purely from the query definition - not just a slicer's own
    field: a donutChart's Category and Y fields, and a columnChart's
    Category/Rows/Tooltips/Y fields, all showed up the same way. Whether
    or not any of them has ever been given a real filterConfig entry is a
    separate question - see _missing_own_field_filters."""
    query_state = (visual_obj.get("query") or {}).get("queryState") or {}
    fields: list[dict] = []
    if not isinstance(query_state, dict):
        return fields
    for role in query_state.values():
        if not isinstance(role, dict):
            continue
        projections = role.get("projections")
        if not isinstance(projections, list):
            continue
        for p in projections:
            if isinstance(p, dict) and isinstance(p.get("field"), dict):
                fields.append(p["field"])
    return fields


def _missing_own_field_filters(root: dict, visual_obj: dict) -> list[dict]:
    """For any visual, returns the field dict(s) it's bound to (in any
    query role) that have NO corresponding entry in filterConfig.filters[]
    at all. Power BI Desktop always shows every field a visual uses as a
    card in that visual's own "Filters on this visual" pane, synthesized
    purely from the query definition - but until someone manually
    interacts with that specific card (e.g. locks it once), no JSON node
    for it exists anywhere in the file. The regular lock/hide fix
    (_find_unlocked_active_filters) can only patch an EXISTING entry; it's
    powerless against a card with nothing to patch. Confirmed against a
    real project on two different fronts: two slicers (bound to "Segment
    2" and "Segment 4") each had a *different*, already-fully-compliant
    filter entry in their filterConfig (a cross-filter on
    "Dim_Segment.Value") but no entry at all for their own bound field; and
    separately, a donutChart's own Category and Y fields ("Employee
    outcome" / "Headcount") had no filterConfig entry at all either, on a
    visual type that isn't a slicer. Either way, that field's own card
    stayed at "(All)", unlocked and unhidden, no matter how many times the
    existing fix ran, because there was nothing there to fix."""
    own_fields = _visual_field_projections(visual_obj)
    if not own_fields:
        return []
    existing = (root.get("filterConfig") or {}).get("filters")
    existing_identities = set()
    if isinstance(existing, list):
        for filt in existing:
            if isinstance(filt, dict):
                ident = _field_identity(filt.get("field"))
                if ident:
                    existing_identities.add(ident)
    missing, seen = [], set()
    for f in own_fields:
        ident = _field_identity(f)
        if ident and ident not in existing_identities and ident not in seen:
            missing.append(f)
            seen.add(ident)
    return missing


def check_visual_filters(visual: VisualRef) -> list[Finding]:
    """Rule 6 (per-visual half): same check as check_report_filters, but for
    a single visual's own filter pane (visual.json's filterConfig, a sibling
    of "visual" at the file root - not nested inside the visual object
    itself). Every filter card is checked, active or not - see
    _find_unlocked_active_filters. Also separately catches any field the
    visual is bound to (in any query role, on any visual type - not just a
    slicer) when it has no filterConfig entry to patch at all - see
    _missing_own_field_filters. PBIR-only for now; legacy report.json
    encodes visual-level filters differently and isn't handled here yet."""
    if visual.format != "pbir":
        return []
    root, visual_obj = load_visual_json(visual)
    findings: list[Finding] = []

    occurrences = list(_find_unlocked_active_filters(root))
    if occurrences:
        items = [_filter_label(f) for _, f in occurrences]
        n = len(occurrences)
        findings.append(Finding(
            rule_id=RULE_FILTERS_LOCKED_HIDDEN,
            rule_label=RULE_LABELS[RULE_FILTERS_LOCKED_HIDDEN],
            visual=visual,
            detail=f"{n} filter{'s' if n != 1 else ''} on this visual "
                   f"{'are' if n != 1 else 'is'} not both locked and hidden.",
            items=items,
            fixable=True,
            auto_select=True,
            fix_payload={"kind": "lock_hide_filters", "indices": [i for i, _ in occurrences]},
        ))

    missing_fields = _missing_own_field_filters(root, visual_obj)
    if missing_fields:
        items = [_filter_label({"field": f}) for f in missing_fields]
        n = len(missing_fields)
        findings.append(Finding(
            rule_id=RULE_FILTERS_LOCKED_HIDDEN,
            rule_label=RULE_LABELS[RULE_FILTERS_LOCKED_HIDDEN],
            visual=visual,
            detail=(f"This visual's own field{'s' if n != 1 else ''} "
                    f"({', '.join(items)}) show{'s' if n == 1 else ''} up in the "
                    f"Filters pane but {'has' if n == 1 else 'have'} never been "
                    f"locked or hidden - there's no filter entry for "
                    f"{'it' if n == 1 else 'them'} yet, so fixing this creates one."),
            items=items,
            fixable=True,
            auto_select=True,
            fix_payload={"kind": "create_and_lock_filters", "fields": missing_fields},
        ))

    return findings


def _iter_pbir_pages(report_folder: str):
    """Yields (page_id, page_name, page_json_path) for every PBIR page in a
    report folder. A lightweight local copy of the scanner's own page
    enumeration - kept here rather than importing scanner to avoid a
    cross-module dependency on a private helper."""
    pages_dir = os.path.join(report_folder, "definition", "pages")
    if not os.path.isdir(pages_dir):
        return
    for entry in sorted(os.listdir(pages_dir)):
        page_json_path = os.path.join(pages_dir, entry, "page.json")
        if not os.path.isfile(page_json_path):
            continue
        page_name = entry
        try:
            with open(page_json_path, "r", encoding="utf-8") as f:
                page_json = json.load(f)
            page_name = page_json.get("displayName") or page_json.get("name") or entry
        except Exception:
            pass
        yield entry, page_name, page_json_path


def check_page_filters(report_folder: str, warnings: Optional[list] = None) -> list[Finding]:
    """Rule 6 (page-level third scope): every filter card in a page's own
    filter pane ("Filters on this page" in the Filters pane - page.json's
    own filterConfig.filters, a sibling of "displayName" at that page's own
    file root) - active or left at "(All)" - must be locked and hidden too.
    This is a third, distinct scope from the report-level pane ("Filters on
    all pages", report.json) and any one visual's own pane (visual.json) -
    neither of those two ever looks inside page.json, so a page-level
    filter was previously invisible to this rule entirely. Confirmed
    against a real page.json in the DC & Benefit Affordability Dashboard
    with an active, unlocked, unhidden RelativeDate filter that neither
    check_report_filters nor check_visual_filters would ever have caught.
    PBIR-only for now, same as check_visual_filters - legacy report.json
    encodes page-level filters differently and isn't handled here yet."""
    findings: list[Finding] = []
    report_name = os.path.basename(report_folder)
    for page_id, page_name, page_json_path in _iter_pbir_pages(report_folder):
        try:
            with open(page_json_path, "r", encoding="utf-8") as f:
                page_json = json.load(f)
        except Exception as e:
            if warnings is not None:
                warnings.append(f"Filter lock/hide: could not parse {page_json_path}: {e}.")
            continue

        occurrences = list(_find_unlocked_active_filters(page_json))
        if not occurrences:
            continue

        visual = VisualRef(
            id=f"{report_name}|{page_id}|__page_filters__",
            report_name=report_name,
            report_path=report_folder,
            page_id=page_id,
            page_name=page_name,
            visual_id="page-filters",
            visual_type="filters",
            title="Page-level filter pane",
            file_path=page_json_path,
            format="page_root",
        )
        items = [_filter_label(f) for _, f in occurrences]
        n = len(occurrences)
        findings.append(Finding(
            rule_id=RULE_FILTERS_LOCKED_HIDDEN,
            rule_label=RULE_LABELS[RULE_FILTERS_LOCKED_HIDDEN],
            visual=visual,
            detail=f"{n} page-level filter{'s' if n != 1 else ''} "
                   f"{'are' if n != 1 else 'is'} not both locked and hidden.",
            items=items,
            fixable=True,
            auto_select=True,
            fix_payload={"kind": "lock_hide_filters", "indices": [i for i, _ in occurrences]},
        ))
    return findings


def check_legend_bold(visual: VisualRef) -> list[Finding]:
    """Rule 7: a visual's legend must not have bold explicitly turned off.
    The WTW theme already defaults legends to bold, so - mirroring the
    established pattern for rule 2's header icons - this only needs to catch
    an explicit override (objects.legend[].properties.bold === false);
    absence of the property means "inherits the theme's bold default",
    which is already correct."""
    _, visual_obj = load_visual_json(visual)
    objects = visual_obj.get("objects")
    if not isinstance(objects, dict):
        return []
    legend_list = objects.get("legend")
    if not isinstance(legend_list, list):
        return []
    for entry in legend_list:
        if not isinstance(entry, dict):
            continue
        props = entry.get("properties")
        if isinstance(props, dict) and jsonutil.read_bool(props.get("bold")) is False:
            return [Finding(
                rule_id=RULE_LEGEND_BOLD,
                rule_label=RULE_LABELS[RULE_LEGEND_BOLD],
                visual=visual,
                detail="Legend text is explicitly set to not bold.",
                fixable=True,
                auto_select=True,
                fix_payload={"kind": "legend_bold"},
            )]
    return []


def _read_literal_value(node: Any) -> Any:
    """Returns the raw Literal value out of a Format-pane property binding
    (`{"expr": {"Literal": {"Value": ...}}}`), or None if `node` isn't that
    shape - e.g. a Conditional/Aggregation expression, which this rule
    doesn't attempt to evaluate."""
    if isinstance(node, dict):
        return node.get("expr", {}).get("Literal", {}).get("Value")
    return None


def _is_white_border_color(border_color: Any) -> bool:
    """True if a `borderColor` property (shaped like Power BI's standard
    `{"solid": {"color": {"expr": ...}}}` color binding) represents white.

    Grounded against the real project: the dominant convention (used in
    ~91% of already-configured charts) is a theme reference,
    {"ThemeDataColor": {"ColorId": 0, "Percent": 0}} - ColorId 0 here
    resolves to the theme's background/white slot (confirmed by cross-
    referencing against the same theme's `dataColors` array, whose index 0
    is a distinct purple used elsewhere for actual data-series fills - the
    border's ColorId:0 is evidently a *different*, earlier slot in Power
    BI's underlying color-role ordering, not the first data color). A
    literal '#FFFFFF' (any letter case) is the other pattern actually found
    in the same project and is equally accepted."""
    if not isinstance(border_color, dict):
        return False
    color_value = border_color.get("solid", {}).get("color")
    if not isinstance(color_value, dict):
        return False
    expr = color_value.get("expr", {})
    if not isinstance(expr, dict):
        return False
    theme_ref = expr.get("ThemeDataColor")
    if isinstance(theme_ref, dict):
        percent = theme_ref.get("Percent", 0)
        return theme_ref.get("ColorId") == 0 and (percent in (0, 0.0) or percent is None)
    lit = expr.get("Literal", {}).get("Value")
    if isinstance(lit, str):
        return lit.strip().strip("'\"").upper() == "#FFFFFF"
    return False


def _find_datapoint_border_entry(objects: dict) -> Optional[dict]:
    """Finds the entry in visual.objects.dataPoint[] that holds the global
    border config - the one WITHOUT a "selector" key (a selector there means
    it's a per-category conditional-formatting override, e.g. a specific
    fill color for one data value - not the border default that applies to
    every point) and whose properties include at least one of
    borderShow/borderColor/borderSize."""
    dp = objects.get("dataPoint")
    if not isinstance(dp, list):
        return None
    for entry in dp:
        if not isinstance(entry, dict) or "selector" in entry:
            continue
        props = entry.get("properties")
        if isinstance(props, dict) and any(k in props for k in ("borderShow", "borderColor", "borderSize")):
            return entry
    return None


def check_data_point_border(visual: VisualRef) -> list[Finding]:
    """Rule 9: pie/donut/bar/column-family charts must show a white, 0.5pt
    border around each data point. In the Format pane this shows up under
    different section labels depending on visual type ("Slices > Border"
    for pie/donut, "Columns"/"Bars" > Border for bar/column charts) but
    it's the exact same underlying object either way:
    visual.objects.dataPoint[], in the selector-less entry, holding sibling
    properties borderShow/borderColor/borderSize (there is no nested
    "border" wrapper key, and it is NOT under a "slices"/"columns"/"bars"
    object - those Format-pane section names don't correspond to a JSON key
    at all here).

    Grounded directly against a real donutChart and a real columnChart in
    the DC & Benefit Affordability Dashboard: a deliberately-introduced
    change to one chart's border width (0.5D -> 1D) confirmed both the
    property location and that no other existing rule catches this kind of
    deviation. A chart of an applicable type with no such entry at all
    (border never configured) is also flagged, not silently skipped - the
    same real project had 6 charts in that exact state."""
    if not applies_to_border_rule(visual.visual_type):
        return []
    _, visual_obj = load_visual_json(visual)
    objects = visual_obj.get("objects")
    if not isinstance(objects, dict):
        objects = {}
    entry = _find_datapoint_border_entry(objects)

    if entry is None:
        return [Finding(
            rule_id=RULE_DATA_POINT_BORDER,
            rule_label=RULE_LABELS[RULE_DATA_POINT_BORDER],
            visual=visual,
            detail="This visual's data point border (Slices/Columns/Bars > Border in the "
                   "Format pane) has never been configured - expected white, 0.5pt.",
            fixable=True,
            auto_select=True,
            fix_payload={"kind": "data_point_border"},
        )]

    props = entry.get("properties", {})
    show_val = _read_literal_value(props.get("borderShow"))
    show_ok = str(show_val).lower() == "true"
    size_val = _read_literal_value(props.get("borderSize"))
    size_ok = str(size_val) == "0.5D"
    color_ok = _is_white_border_color(props.get("borderColor"))

    if show_ok and size_ok and color_ok:
        return []

    problems = []
    if not show_ok:
        problems.append("border is off" if str(show_val).lower() == "false" else "border isn't explicitly on")
    if not size_ok:
        problems.append(f"width is {size_val if size_val is not None else '(not set)'}, expected 0.5pt")
    if not color_ok:
        problems.append("color isn't white")

    return [Finding(
        rule_id=RULE_DATA_POINT_BORDER,
        rule_label=RULE_LABELS[RULE_DATA_POINT_BORDER],
        visual=visual,
        detail="Data point border doesn't match the required white/0.5pt standard: "
               + "; ".join(problems) + ".",
        fixable=True,
        auto_select=True,
        fix_payload={"kind": "data_point_border"},
    )]


_TABLE_VISUAL_TYPES = {"tableex"}
_MATRIX_VISUAL_TYPES = {"pivottable"}


def _table_alignment(objects: dict, object_name: str) -> Optional[str]:
    """Reads the table-wide (no-selector, first-entry) alignment literal for
    a "values" or "columnHeaders" object, or None if not set at all."""
    entries = objects.get(object_name)
    if not isinstance(entries, list) or not entries:
        return None
    entry = entries[0]
    if not isinstance(entry, dict):
        return None
    lit = _read_literal_value(entry.get("properties", {}).get("alignment"))
    return lit.strip().strip("'\"") if isinstance(lit, str) else None


def _column_formatting_alignment(objects: dict, query_ref: str) -> Optional[str]:
    """Reads a per-column alignment override (the Format pane's "Specific
    column" section) for one column/measure, matched by its queryRef via
    `selector.metadata` - the same mechanism Table and Matrix visuals both
    use for a column-specific override, or None if that column has no
    override at all."""
    entries = objects.get("columnFormatting")
    if not isinstance(entries, list):
        return None
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        selector = entry.get("selector")
        if isinstance(selector, dict) and selector.get("metadata") == query_ref:
            lit = _read_literal_value(entry.get("properties", {}).get("alignment"))
            return lit.strip().strip("'\"") if isinstance(lit, str) else None
    return None


def _value_query_refs(visual_obj: dict) -> list[str]:
    """Collects the queryRef of every field in a Table's or Matrix's Values
    well - confirmed identical for both visual types (same "query.
    queryState.Values.projections[].queryRef" shape), so each one's
    per-column alignment override can be checked individually."""
    refs = []
    query_state = ((visual_obj.get("query") or {}).get("queryState")) or {}
    values_well = query_state.get("Values") or {}
    for proj in values_well.get("projections", []):
        if isinstance(proj, dict):
            qref = proj.get("queryRef")
            if isinstance(qref, str):
                refs.append(qref)
    return refs


def check_table_alignment(visual: VisualRef) -> list[Finding]:
    """Rule 10: a table (tableEx) or matrix (pivotTable) visual's cell
    values and column headers must be centered.

    Table: grounded against three real tableEx visuals - the table-wide
    default lives at `objects.values[0].properties.alignment` (data cells)
    and `objects.columnHeaders[0].properties.alignment` (header row) as a
    plain Literal string ('Left'/'Center'/'Right'). BUT a per-column
    "Specific column" override (`objects.columnFormatting[]`, matched by
    `selector.metadata` to that column's queryRef) always wins over the
    table-wide default for whichever column it targets, confirmed against
    a real table with two such overrides (one also carrying a
    "styleHeader" flag, matching the Format pane's "Apply to header"
    toggle) - so fixing only the table-wide default silently does nothing
    for a column that already has its own override sitting on top of it.
    This rule therefore also checks every value column for an *existing*
    override that isn't Center. A column with no override at all is left
    alone here - it already correctly inherits the table-wide default once
    that's centered, so nothing new is invented for it.

    Matrix: grounded against three real pivotTable visuals in the DC &
    Benefit Affordability Dashboard (one single-value-column, one with four
    value columns, all already centered via explicit overrides - exactly
    the mechanism this fixes). Column headers use the identical
    `objects.columnHeaders[0].properties.alignment` as a Table. But the
    value *cells* have no table-wide default at all - none of the three
    real Matrix visuals ever had an "alignment" key under
    `objects.values[].properties` (only font/color/size/wordWrap ones) -
    so a Matrix's values can only be centered one column at a time via the
    same "Specific column" override mechanism, and (unlike Table) a column
    with no existing override still needs one created, since there's no
    table-wide default to fall back on. Row headers were deliberately left
    unchecked: none of the three real Matrix visuals ever had an
    "alignment" property under `objects.rowHeaders[].properties` either,
    consistent with Power BI Desktop not exposing a Row headers alignment
    control at all (row headers are always left-aligned to show hierarchy
    indentation).

    Exception: entire pages matching ALIGNMENT_EXCLUDED_PAGE_SUBSTRINGS
    (default: any page whose display name contains "info") are skipped
    outright. Grounded against a real counter-example: the "Info (Plan
    design)" page's Matrix is a flat glossary list with no Values well at
    all (just Category/Description Row-header fields) - it has no
    "Specific column" override available in the Format pane the way every
    other grounded Matrix did, only a "Row headers > Alignment" control
    this rule doesn't yet know how to write safely. Rather than risk
    guessing at that different Matrix layout's schema, whole pages matching
    this naming convention are excluded - editable via
    config/rule_config.json.

    Either way, Power BI's own default when a property is never set is NOT
    centered (text defaults left, numbers default right), so a missing
    property is a violation here, not a pass, unlike most other rules in
    this file where "absent" means "inheriting an already-correct
    default." """
    vtype = (visual.visual_type or "").lower()
    is_table = vtype in _TABLE_VISUAL_TYPES
    is_matrix = vtype in _MATRIX_VISUAL_TYPES
    if not is_table and not is_matrix:
        return []
    page_name_lower = (visual.page_name or "").lower()
    if any(substr in page_name_lower for substr in ALIGNMENT_EXCLUDED_PAGE_SUBSTRINGS):
        return []

    _, visual_obj = load_visual_json(visual)
    objects = visual_obj.get("objects")
    if not isinstance(objects, dict):
        objects = {}

    header_align = _table_alignment(objects, "columnHeaders")
    header_ok = header_align == "Center"
    problems = []
    if not header_ok:
        problems.append(f"column headers are {header_align or 'not explicitly'} aligned")

    value_query_refs: list[str] = []
    values_ok = True
    if is_table:
        values_align = _table_alignment(objects, "values")
        if values_align != "Center":
            values_ok = False
            problems.append(f"values are {values_align or 'not explicitly'} aligned")
        for qref in _value_query_refs(visual_obj):
            col_align = _column_formatting_alignment(objects, qref)
            if col_align is not None and col_align != "Center":
                values_ok = False
                value_query_refs.append(qref)
                problems.append(f"column '{qref}' overrides alignment to {col_align}")
    else:
        for qref in _value_query_refs(visual_obj):
            col_align = _column_formatting_alignment(objects, qref)
            if col_align != "Center":
                values_ok = False
                value_query_refs.append(qref)
                problems.append(f"column '{qref}' is {col_align or 'not explicitly'} aligned")

    if header_ok and values_ok:
        return []

    return [Finding(
        rule_id=RULE_TABLE_VALUES_CENTERED,
        rule_label=RULE_LABELS[RULE_TABLE_VALUES_CENTERED],
        visual=visual,
        detail=("Matrix isn't centered: " if is_matrix else "Table isn't centered: ")
               + "; ".join(problems) + " - expected Center throughout.",
        fixable=True,
        auto_select=True,
        fix_payload={
            "kind": "table_alignment",
            "is_matrix": is_matrix,
            "value_query_refs": value_query_refs,
        },
    )]


_TMDL_FUNC_RE = re.compile(r"([A-Za-z][A-Za-z0-9_.#]*)\s*\(")
_TMDL_STRING_RE = re.compile(r'"([^"]{3,300})"')
# M/DAX functions that construct a table in-memory rather than reaching out
# anywhere - never an external connection, so a `Source = ...` line whose
# outermost call is one of these has nothing to review. Confirmed against
# real patterns in the DC & Benefit Affordability Dashboard: small manually-
# entered tables Power BI Desktop encodes as `Table.FromRows(Json.Document(
# Binary.Decompress(Binary.FromText("<base64>", ...))))`; a single-blank-row
# placeholder table, `Row("Blank", BLANK())`; and Power BI's own
# auto-generated "Auto date/time" hidden tables (`DateTableTemplate_*` /
# `LocalDateTable_*`), whose `Source = Calendar(...)` line computes a date
# range purely from an existing column's min/max - never a real connection.
_NON_SOURCE_FUNCS = {
    "row", "table.fromrows", "table.fromcolumns", "table.fromrecords", "#table",
    "calendar", "calendarauto",
}


def _find_semantic_model_folder(report_folder: str) -> Optional[str]:
    """A PBIP's report and semantic model are sibling folders sharing the
    same base name (`<Name>.Report` / `<Name>.SemanticModel`), not nested
    inside each other."""
    parent = os.path.dirname(report_folder.rstrip("\\/"))
    base = os.path.basename(report_folder.rstrip("\\/"))
    if not base.lower().endswith(".report"):
        return None
    stem = base[: -len(".Report")]
    candidate = os.path.join(parent, stem + ".SemanticModel")
    if os.path.isdir(candidate):
        return candidate
    try:
        for name in os.listdir(parent):
            if name.lower() == (stem + ".SemanticModel").lower():
                return os.path.join(parent, name)
    except OSError:
        pass
    return None


def _iter_tmdl_files(semantic_model_folder: str):
    definition_dir = os.path.join(semantic_model_folder, "definition")
    if not os.path.isdir(definition_dir):
        return
    expr_path = os.path.join(definition_dir, "expressions.tmdl")
    if os.path.isfile(expr_path):
        yield expr_path
    tables_dir = os.path.join(definition_dir, "tables")
    if os.path.isdir(tables_dir):
        for name in sorted(os.listdir(tables_dir)):
            if name.lower().endswith(".tmdl"):
                yield os.path.join(tables_dir, name)


_PARAM_EXPRESSION_RE = re.compile(r'^expression\s+(\S+)\s*=\s*"((?:[^"]|"")*)"', re.MULTILINE)


def _scan_tmdl_sources(path: str):
    """Yields (line_no, snippet, function_name, target) for each
    `Source = ...` statement found in a .tmdl file. TMDL is plain text (not
    JSON), so this is a best-effort scan good enough to surface a
    connection for a human to review - not a full M-language parser."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = f.read().splitlines()
    except Exception:
        return
    for i, line in enumerate(lines):
        m = re.match(r"\s*[Ss]ource\s*=\s*(.+)$", line)
        if not m:
            continue
        snippet = m.group(1).strip()
        # The M expression can wrap onto following lines - cheaply pull in a
        # little more context if this line's parens aren't balanced yet.
        j = i
        while snippet.count("(") > snippet.count(")") and j + 1 < len(lines) and j - i < 5:
            j += 1
            snippet += " " + lines[j].strip()
        func_match = _TMDL_FUNC_RE.search(snippet)
        str_match = _TMDL_STRING_RE.search(snippet)
        yield i + 1, snippet[:220], (func_match.group(1) if func_match else None), \
            (str_match.group(1) if str_match else None)


def _load_tmdl_parameters(sm_folder: str) -> dict:
    """Parses definition/expressions.tmdl for shared query parameters Power
    BI Desktop writes as `expression <Name> = "<value>" meta [...]`.

    Grounded directly against the real DC & Benefit Affordability
    Dashboard: it has exactly one such parameter, `SourceFile`, holding the
    actual SharePoint URL every table's `Source = Excel.Workbook(Web.
    Contents(SourceFile), ...)` line references by bare name rather than as
    a literal string - which is why the naive "first quoted string on the
    line" scan never found a real path for any of those tables. Returns
    {name: literal_string_value}."""
    params: dict = {}
    path = os.path.join(sm_folder, "definition", "expressions.tmdl")
    if not os.path.isfile(path):
        return params
    try:
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
    except Exception:
        return params
    for m in _PARAM_EXPRESSION_RE.finditer(text):
        params[m.group(1).strip("'")] = m.group(2).replace('""', '"')
    return params


def _resolve_source_path(snippet: str, target: Optional[str], params: dict) -> Optional[str]:
    """Best-effort resolution of the actual connection target for a
    `Source = ...` M expression: a literal quoted string already found on
    the line (`target`) is preferred; failing that, checks whether the
    snippet references a known shared parameter name as a bare identifier
    (e.g. `Web.Contents(SourceFile)`) and resolves it to that parameter's
    value. Returns None if neither yields anything - the connection exists
    but its exact target can't be determined from static analysis alone."""
    if target:
        return target
    for name, value in params.items():
        if re.search(r"(?<![\w.])" + re.escape(name) + r"(?![\w.])", snippet):
            return value
    return None


def _is_tcto_source(path: str) -> bool:
    p = path.lower()
    return any(p.startswith(prefix.lower()) for prefix in TCTO_PATH_PREFIXES)


def check_data_source_review(report_folder: str, warnings: Optional[list] = None) -> list[Finding]:
    """Rule 8: every table's data-source connection must resolve to a path
    under the approved TCTO SharePoint location (TCTO_PATH_PREFIXES,
    editable via config/rule_config.json) - anything else is flagged for
    review. A source whose exact target can't be resolved at all (still
    just a bare function call, no literal and no matching shared
    parameter) is also flagged, since it can't be confirmed as compliant
    either. There's no safe automatic fix for a data connection, so these
    findings are always fixable=False - they exist purely to be reviewed.

    Purely in-memory constructed tables (Power BI's own `Table.FromRows(...)`
    encoding for small manually-entered tables, or a `Row("Blank", BLANK())`
    placeholder table) are skipped entirely - confirmed against real
    examples of both in the same project - there's no external connection
    there to review at all."""
    findings: list[Finding] = []
    report_name = os.path.basename(report_folder)
    sm_folder = _find_semantic_model_folder(report_folder)
    if not sm_folder:
        if warnings is not None:
            warnings.append(
                f"Data source review: could not find a .SemanticModel folder next to "
                f"'{report_name}' - nothing to check."
            )
        return findings

    params = _load_tmdl_parameters(sm_folder)

    for tmdl_path in _iter_tmdl_files(sm_folder):
        source_name = os.path.splitext(os.path.basename(tmdl_path))[0]
        for line_no, snippet, func_name, target in _scan_tmdl_sources(tmdl_path):
            if not func_name and not target:
                # `Source = SomePriorStepOrQueryName` with no function call
                # and no literal at all - just a step referencing another
                # already-loaded step/query by name, not an actual
                # connection. Nothing useful to review here.
                continue
            if func_name and func_name.lower() in _NON_SOURCE_FUNCS:
                # In-memory constructed table (baked-in literal data, or a
                # blank-row placeholder) - not an external connection,
                # nothing to review.
                continue

            resolved = _resolve_source_path(snippet, target, params)
            if resolved and _is_tcto_source(resolved):
                continue  # compliant - resolves to the approved TCTO location

            visual = VisualRef(
                id=f"{report_name}|__datasource__|{source_name}:{line_no}",
                report_name=report_name,
                report_path=report_folder,
                page_id="__data_source__",
                page_name="(Data Source)",
                visual_id=f"{source_name}:{line_no}",
                visual_type="data source",
                title=source_name,
                file_path=tmdl_path,
                format="tmdl",
            )
            if resolved:
                detail = (f"'{source_name}' connects via {func_name or 'an M expression'} to "
                          f"'{resolved}', which is NOT the approved TCTO location - review whether "
                          f"this is intended.")
            else:
                detail = (f"'{source_name}' connects via {func_name or 'an M expression'} - could not "
                          f"determine the exact source location from the file alone; review manually.")
            findings.append(Finding(
                rule_id=RULE_DATA_SOURCE_REVIEW,
                rule_label=RULE_LABELS[RULE_DATA_SOURCE_REVIEW],
                visual=visual,
                detail=detail,
                items=[resolved or func_name or snippet],
                fixable=False,
                auto_select=False,
                fix_payload={},
            ))
    return findings


def _extract_tmdl_measure_dax(text: str, measure_name: str) -> Optional[str]:
    """Best-effort extraction of a named measure's DAX body from a table's
    TMDL source text. Handles both forms Power BI Desktop writes:
    multi-line, fenced in triple backticks (```...```), and inline single-
    line (`measure Name = <expr>`)."""
    pattern = re.compile(
        r"^[ \t]*measure\s+(?:'" + re.escape(measure_name) + r"'|" + re.escape(measure_name) + r")\s*=[ \t]*(.*)$",
        re.MULTILINE,
    )
    m = pattern.search(text)
    if not m:
        return None
    first_rest = m.group(1).strip()
    if first_rest.startswith("```"):
        body_start = m.end()
        closing = text.find("```", body_start)
        opening_extra = first_rest[3:].strip()
        if closing == -1:
            return opening_extra
        return (opening_extra + "\n" + text[body_start:closing]).strip()
    return first_rest


def _strip_dax_comments(text: str) -> str:
    """Removes DAX line comments (// or --) and /* block */ comments, so a
    literal search for a column reference doesn't match old, deliberately
    commented-out history left in a measure for reference."""
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    out_lines = []
    for line in text.splitlines():
        cut = len(line)
        for marker in ("//", "--"):
            idx = line.find(marker)
            if idx != -1:
                cut = min(cut, idx)
        out_lines.append(line[:cut])
    return "\n".join(out_lines)


_BASELINE_EMPNUM_RE = re.compile(r"'?Baseline'?\s*\[\s*EmployeeNumber\s*\]", re.IGNORECASE)
_SEGMENTS_EMPNUM_RE = re.compile(r"'?Employee_Segments'?\s*\[\s*EmployeeNumber\s*\]", re.IGNORECASE)


def check_dc_headcount_measure(report_folder: str, warnings: Optional[list] = None) -> list[Finding]:
    """DC & Benefit Affordability Dashboard family rule: MeasureTable[Headcount]
    must actively reference Employee_Segments[EmployeeNumber], not
    Baseline[EmployeeNumber]. Detection-only - rewriting DAX logic isn't
    something this tool does automatically, this exists to catch a
    regression back to the old approach.

    Grounded directly against the real dashboard's MeasureTable.tmdl: the
    compliant measure's active code only references Employee_Segments, and
    keeps the old Baseline-based v1-v3 logic purely as `//`/`--`-commented
    history below it - so comments are stripped before searching, or this
    would false-positive-fail a compliant measure that still keeps that
    history around."""
    report_name = os.path.basename(report_folder)
    sm_folder = _find_semantic_model_folder(report_folder)
    if not sm_folder:
        if warnings is not None:
            warnings.append(
                f"Headcount measure check: could not find a .SemanticModel folder next to "
                f"'{report_name}' - nothing to check."
            )
        return []
    measure_path = os.path.join(sm_folder, "definition", "tables", "MeasureTable.tmdl")
    if not os.path.isfile(measure_path):
        if warnings is not None:
            warnings.append(
                f"Headcount measure check: no MeasureTable.tmdl found in '{os.path.basename(sm_folder)}'."
            )
        return []
    try:
        with open(measure_path, "r", encoding="utf-8") as f:
            text = f.read()
    except Exception as e:
        if warnings is not None:
            warnings.append(f"Headcount measure check: could not read {measure_path}: {e}.")
        return []

    dax = _extract_tmdl_measure_dax(text, "Headcount")
    if dax is None:
        if warnings is not None:
            warnings.append("Headcount measure check: no 'Headcount' measure found in MeasureTable.tmdl.")
        return []

    active = _strip_dax_comments(dax)
    baseline_ref = bool(_BASELINE_EMPNUM_RE.search(active))
    segments_ref = bool(_SEGMENTS_EMPNUM_RE.search(active))

    if not baseline_ref and segments_ref:
        return []  # compliant

    visual = VisualRef(
        id=f"{report_name}|__dc_measure__|Headcount",
        report_name=report_name,
        report_path=report_folder,
        page_id="__dc_measure__",
        page_name="(MeasureTable)",
        visual_id="Headcount",
        visual_type="measure",
        title="MeasureTable[Headcount]",
        file_path=measure_path,
        format="tmdl",
    )
    if baseline_ref:
        detail = ("MeasureTable[Headcount]'s active DAX still references Baseline[EmployeeNumber] - "
                  "it should reference Employee_Segments[EmployeeNumber] instead.")
    else:
        detail = ("MeasureTable[Headcount] no longer references Employee_Segments[EmployeeNumber] at "
                  "all - the measure may have changed unexpectedly.")
    return [Finding(
        rule_id=RULE_DC_HEADCOUNT_MEASURE,
        rule_label=RULE_LABELS[RULE_DC_HEADCOUNT_MEASURE],
        visual=visual,
        detail=detail,
        fixable=False,
        auto_select=False,
        fix_payload={},
    )]


def _split_tmdl_ref(ref: str) -> tuple[str, str]:
    """Splits a TMDL `Table.Column` (or `'Table Name'.'Column Name'`)
    reference into (table, column)."""
    ref = ref.strip()
    if ref.startswith("'"):
        end = ref.index("'", 1)
        table = ref[1:end]
        rest = ref[end + 1:].lstrip(".")
    else:
        dot = ref.index(".")
        table = ref[:dot]
        rest = ref[dot + 1:]
    col = rest.strip()
    if col.startswith("'") and col.endswith("'"):
        col = col[1:-1]
    return table, col


def _parse_tmdl_relationships(text: str):
    """Yields a dict per `relationship <id>` block in a relationships.tmdl
    file - keys are whatever property lines that block has (fromColumn,
    toColumn, fromCardinality, toCardinality, crossFilteringBehavior,
    isActive, ...)."""
    current = None
    for line in text.splitlines():
        stripped = line.strip()
        m = re.match(r"relationship\s+(\S+)\s*$", stripped)
        if m:
            if current is not None:
                yield current
            current = {"id": m.group(1)}
            continue
        if current is None or not stripped:
            continue
        kv = re.match(r"([A-Za-z]+)\s*:\s*(.+)$", stripped)
        if kv:
            current[kv.group(1)] = kv.group(2).strip()
    if current is not None:
        yield current


def check_dc_baseline_cardinality(report_folder: str, warnings: Optional[list] = None) -> list[Finding]:
    """DC & Benefit Affordability Dashboard family rule: the relationship
    joining Baseline[EmployeeNumber] to Employee_Segments[EmployeeNumber]
    must be declared 1:1 and active. Detection-only - a relationship's
    cardinality is structural model design, not something to auto-fix, and
    this can only confirm what's *declared* in the file; actually matching
    row counts on both sides (part of the original manual QA check) needs a
    live data connection, out of scope for a static file scan.

    Grounded directly against the real dashboard's relationships.tmdl:
    `toCardinality` never appears anywhere in that file, including on
    relationships with no cardinality override at all (which are the
    ordinary many-to-one case) - confirming Power BI Desktop's TMDL writer
    defaults toCardinality to "one" and only ever writes `fromCardinality:
    one` as an override to flip a relationship to 1:1, exactly what the
    Baseline/Employee_Segments relationship has."""
    report_name = os.path.basename(report_folder)
    sm_folder = _find_semantic_model_folder(report_folder)
    if not sm_folder:
        if warnings is not None:
            warnings.append(
                f"Baseline/Employee_Segments cardinality check: could not find a .SemanticModel "
                f"folder next to '{report_name}' - nothing to check."
            )
        return []
    rel_path = os.path.join(sm_folder, "definition", "relationships.tmdl")
    if not os.path.isfile(rel_path):
        if warnings is not None:
            warnings.append(
                f"Baseline/Employee_Segments cardinality check: no relationships.tmdl found in "
                f"'{os.path.basename(sm_folder)}'."
            )
        return []
    try:
        with open(rel_path, "r", encoding="utf-8") as f:
            text = f.read()
    except Exception as e:
        if warnings is not None:
            warnings.append(f"Baseline/Employee_Segments cardinality check: could not read {rel_path}: {e}.")
        return []

    target = None
    for rel in _parse_tmdl_relationships(text):
        if "fromColumn" not in rel or "toColumn" not in rel:
            continue
        try:
            from_table, from_col = _split_tmdl_ref(rel["fromColumn"])
            to_table, to_col = _split_tmdl_ref(rel["toColumn"])
        except ValueError:
            continue
        pair = {(from_table, from_col), (to_table, to_col)}
        if pair == {("Baseline", "EmployeeNumber"), ("Employee_Segments", "EmployeeNumber")}:
            target = rel
            break

    visual = VisualRef(
        id=f"{report_name}|__dc_relationship__|Baseline_EmployeeSegments",
        report_name=report_name,
        report_path=report_folder,
        page_id="__dc_relationship__",
        page_name="(Relationships)",
        visual_id="Baseline-Employee_Segments",
        visual_type="relationship",
        title="Baseline ↔ Employee_Segments (EmployeeNumber)",
        file_path=rel_path,
        format="tmdl",
    )

    if target is None:
        return [Finding(
            rule_id=RULE_DC_BASELINE_CARDINALITY,
            rule_label=RULE_LABELS[RULE_DC_BASELINE_CARDINALITY],
            visual=visual,
            detail="No relationship was found joining Baseline[EmployeeNumber] to "
                   "Employee_Segments[EmployeeNumber] in relationships.tmdl.",
            fixable=False,
            auto_select=False,
            fix_payload={},
        )]

    from_card = target.get("fromCardinality", "many")
    to_card = target.get("toCardinality", "one")
    is_active = target.get("isActive", "true").lower() != "false"
    cross_filter = target.get("crossFilteringBehavior", "automatic")

    if from_card == "one" and to_card == "one" and is_active:
        return []  # compliant - declared 1:1 and active

    problems = []
    if from_card != "one":
        problems.append(f"fromCardinality is '{from_card}', not 'one'")
    if to_card != "one":
        problems.append(f"toCardinality is '{to_card}', not 'one'")
    if not is_active:
        problems.append("the relationship is inactive")
    return [Finding(
        rule_id=RULE_DC_BASELINE_CARDINALITY,
        rule_label=RULE_LABELS[RULE_DC_BASELINE_CARDINALITY],
        visual=visual,
        detail=("Baseline ↔ Employee_Segments relationship isn't declared 1:1: "
                + "; ".join(problems) + f" (cross-filter: {cross_filter}). This only confirms "
                "what's declared in the model - actual matching row counts on both sides still "
                "needs to be checked in Power BI Desktop directly."),
        fixable=False,
        auto_select=False,
        fix_payload={},
    )]


def _find_page_by_display_name(report_folder: str, display_name: str) -> Optional[tuple[str, str]]:
    """Returns (page_folder_path, page_id) for the PBIR page whose
    definition/pages/<id>/page.json `displayName` matches display_name
    (case-insensitive, whitespace-trimmed), or None if no page matches."""
    pages_dir = os.path.join(report_folder, "definition", "pages")
    if not os.path.isdir(pages_dir):
        return None
    target = display_name.strip().lower()
    try:
        page_ids = sorted(os.listdir(pages_dir))
    except OSError:
        return None
    for page_id in page_ids:
        page_json_path = os.path.join(pages_dir, page_id, "page.json")
        if not os.path.isfile(page_json_path):
            continue
        try:
            with open(page_json_path, "r", encoding="utf-8") as f:
                page_json = json.load(f)
        except Exception:
            continue
        if str(page_json.get("displayName", "")).strip().lower() == target:
            return os.path.join(pages_dir, page_id), page_id
    return None


def _read_visual_title(visual_json: dict) -> Optional[str]:
    """Best-effort extraction of a visual's Format-pane title text (unquoted),
    from visualContainerObjects.title[0].properties.text - the same text
    Power BI Desktop's Selection pane shows as a visual's readable label
    even when the title itself isn't shown on canvas (title.show: false)."""
    title_list = visual_json.get("visual", {}).get("visualContainerObjects", {}).get("title")
    if not isinstance(title_list, list) or not title_list:
        return None
    entry = title_list[0]
    if not isinstance(entry, dict):
        return None
    lit = _read_literal_value(entry.get("properties", {}).get("text"))
    if isinstance(lit, str):
        return lit.strip().strip("'\"")
    return None


def check_dc_plan_design_no_privacy_note(report_folder: str, warnings: Optional[list] = None) -> list[Finding]:
    """DC & Benefit Affordability Dashboard family rule: the "Plan design"
    page must not have a "Data privacy note" card on it - that disclaimer
    belongs only on the pages where headcount-suppression actually applies
    (Defined Contribution, Healthcare, Low income risk, Heat map,
    Employer/Employee value comparability, etc.), per the original QA
    review. It has a habit of coming back (e.g. via copy-pasting a page
    template that includes it), so this exists to catch that regression.

    Grounded directly against the real dashboard: the component is a
    hidden-title `card` visual bound to the "Data privacy" table's "Data
    privacy note" measure; its title text (which Power BI Desktop's
    Selection pane shows as the visual's label even though the title isn't
    displayed on canvas) is exactly "Data privacy note" - the same text the
    report author uses across every page that's still supposed to have this
    card, which is what makes this an easy, reliable name to match on.

    Unlike the other two DC-family rules, this one IS auto-fixable and
    auto-selected: deleting the one matching visual (its whole file, plus
    its now-empty folder) is exactly the requested fix, and the match is
    narrow enough - the right page AND the exact title text - that a false
    positive is very unlikely."""
    report_name = os.path.basename(report_folder)
    found = _find_page_by_display_name(report_folder, "Plan design")
    if found is None:
        if warnings is not None:
            warnings.append(
                f"Plan design privacy note check: could not find a page named 'Plan design' in "
                f"'{report_name}' - nothing to check."
            )
        return []
    page_dir, page_id = found
    visuals_dir = os.path.join(page_dir, "visuals")
    if not os.path.isdir(visuals_dir):
        return []

    findings: list[Finding] = []
    try:
        visual_ids = sorted(os.listdir(visuals_dir))
    except OSError:
        return []
    for visual_id in visual_ids:
        visual_json_path = os.path.join(visuals_dir, visual_id, "visual.json")
        if not os.path.isfile(visual_json_path):
            continue
        try:
            with open(visual_json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            continue
        title_text = _read_visual_title(data)
        if not title_text or title_text.lower() != "data privacy note":
            continue
        visual = VisualRef(
            id=f"{report_name}|{page_id}|{visual_id}",
            report_name=report_name,
            report_path=report_folder,
            page_id=page_id,
            page_name="Plan design",
            visual_id=visual_id,
            visual_type=data.get("visual", {}).get("visualType", ""),
            title="Data privacy note",
            file_path=visual_json_path,
            format="pbir",
        )
        findings.append(Finding(
            rule_id=RULE_DC_PLAN_DESIGN_NO_PRIVACY_NOTE,
            rule_label=RULE_LABELS[RULE_DC_PLAN_DESIGN_NO_PRIVACY_NOTE],
            visual=visual,
            detail="The 'Data privacy note' card shouldn't appear on the Plan design page - it "
                   "belongs only on pages where headcount suppression actually applies.",
            fixable=True,
            auto_select=True,
            fix_payload={"kind": "delete_visual"},
        ))
    return findings


def _find_visual_group_by_display_name(page_dir: str, display_name: str) -> Optional[tuple[str, str, dict]]:
    """Searches every visual.json directly under page_dir/visuals/ for a
    visualGroup (layout container - has a "visualGroup" key, not "visual")
    whose displayName matches (case-insensitive, whitespace-trimmed).
    Returns (visual_id, file_path, parsed_json) for the first match, or None.

    Deliberately only looks at each group's OWN file - never descends into
    its children (visuals whose "parentGroupName" points at this group's
    "name"). Grounded against the real "Disclaimer Banner" group on three
    pages: `{"name": ..., "position": {...}, "visualGroup": {"displayName":
    "Disclaimer Banner", "groupMode": "ScaleMode"}}` at the file root, with
    an "isHidden": true sibling key added only when the group is hidden."""
    target = display_name.strip().lower()
    visuals_dir = os.path.join(page_dir, "visuals")
    if not os.path.isdir(visuals_dir):
        return None
    try:
        visual_ids = sorted(os.listdir(visuals_dir))
    except OSError:
        return None
    for visual_id in visual_ids:
        path = os.path.join(visuals_dir, visual_id, "visual.json")
        if not os.path.isfile(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            continue
        group = data.get("visualGroup")
        if isinstance(group, dict) and str(group.get("displayName", "")).strip().lower() == target:
            return visual_id, path, data
    return None


def check_dc_disclaimer_banner_shown(
    report_folder: str, target_pages: Optional[list] = None, warnings: Optional[list] = None
) -> list[Finding]:
    """DC & Benefit Affordability Dashboard family rule: the "Disclaimer
    Banner" visualGroup must be shown by default (not hidden on initial page
    load) on each page in target_pages (default: Benefit affordability, DC,
    Plan design - _DEFAULT_DISCLAIMER_BANNER_PAGES - but the UI lets the user
    pick which of those pages to actually check via a checkbox per page).

    Deliberately scoped to ONLY the group container's own visibility state -
    per the user's explicit instruction, the individual child visuals inside
    the group (Close Button, Title, Message, Confidentiality Footer, WTW
    Logo, Background - each independently toggleable in Power BI's Selection
    pane) are never inspected or touched here.

    Grounded directly against the real dashboard: a visualGroup's own
    visibility is a top-level "isHidden": true key, a sibling of
    "visualGroup" and "position" at that group's own visual.json file root -
    confirmed against both a plain visual and a real hidden visualGroup
    elsewhere in the same project ("Total retirement contribution"). Absence
    of the key means shown by default, which is the current (compliant)
    state of all three Disclaimer Banner groups in this project - so this is
    a forward-looking regression guard, not something currently violated.

    A page in target_pages that doesn't exist, or that exists but has no
    "Disclaimer Banner" group at all, is surfaced as a scan warning (not a
    finding) - there's nothing safe to auto-create in that case, only
    something worth a human's attention."""
    findings: list[Finding] = []
    report_name = os.path.basename(report_folder)
    pages = target_pages if target_pages else _DEFAULT_DISCLAIMER_BANNER_PAGES
    for page_name in pages:
        found = _find_page_by_display_name(report_folder, page_name)
        if found is None:
            if warnings is not None:
                warnings.append(
                    f"Disclaimer Banner check: could not find a page named '{page_name}' in "
                    f"'{report_name}' - nothing to check there."
                )
            continue
        page_dir, page_id = found
        group = _find_visual_group_by_display_name(page_dir, "Disclaimer Banner")
        if group is None:
            if warnings is not None:
                warnings.append(
                    f"Disclaimer Banner check: no 'Disclaimer Banner' group found on the "
                    f"'{page_name}' page in '{report_name}'."
                )
            continue
        visual_id, group_path, data = group
        if jsonutil.read_bool(data.get("isHidden")) is not True:
            continue  # shown by default - compliant

        visual = VisualRef(
            id=f"{report_name}|{page_id}|{visual_id}",
            report_name=report_name,
            report_path=report_folder,
            page_id=page_id,
            page_name=page_name,
            visual_id=visual_id,
            visual_type="visualGroup",
            title="Disclaimer Banner",
            file_path=group_path,
            format="visual_group_root",
        )
        findings.append(Finding(
            rule_id=RULE_DC_DISCLAIMER_BANNER_SHOWN,
            rule_label=RULE_LABELS[RULE_DC_DISCLAIMER_BANNER_SHOWN],
            visual=visual,
            detail=f"The Disclaimer Banner group is hidden by default on the '{page_name}' page - "
                   f"it should be shown when the page first loads (viewers can still dismiss it via "
                   f"its own Close button).",
            fixable=True,
            auto_select=True,
            fix_payload={"kind": "unhide_visual_group"},
        ))
    return findings


def _find_bookmark_by_display_name(report_folder: str, display_name: str) -> Optional[str]:
    """Returns the internal `name` of the PBIR bookmark (a sibling of
    displayName in definition/bookmarks/<name>.bookmark.json) whose
    displayName matches (case-insensitive, trimmed), or None if no bookmark
    matches. This is the value a visualLink's "bookmark" property must hold
    (quoted) to actually point at it - resolved by display name rather than
    hardcoded, so a re-exported/renamed-internally bookmark is still found."""
    bookmarks_dir = os.path.join(report_folder, "definition", "bookmarks")
    if not os.path.isdir(bookmarks_dir):
        return None
    target = display_name.strip().lower()
    try:
        file_names = sorted(os.listdir(bookmarks_dir))
    except OSError:
        return None
    for fname in file_names:
        if not fname.lower().endswith(".bookmark.json"):
            continue
        path = os.path.join(bookmarks_dir, fname)
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            continue
        if str(data.get("displayName", "")).strip().lower() == target:
            return data.get("name") or fname[: -len(".bookmark.json")]
    return None


def check_visualize_population_bookmark(report_folder: str, warnings: Optional[list] = None) -> list[Finding]:
    """Generic rule 11 (all dashboards): every "Visualize
    population" button (a PBIR actionButton, matched by its Format-pane
    title text - see _read_visual_title) across every page must have its
    bookmark action (visualContainerObjects.visualLink[0].properties, a
    "'Bookmark'"-type visualLink) pointing at the bookmark whose displayName
    is "Employee demographics" (_VISUALIZE_POPULATION_TARGET_BOOKMARK_
    DISPLAY_NAME), resolved dynamically via _find_bookmark_by_display_name
    rather than a hardcoded id. Applies dashboard-wide - every page is
    checked, not a configurable subset, since there's no "wrong page" for
    this button the way there is for the Disclaimer Banner.

    Grounded directly against the real dashboard: 11 of these buttons across
    11 different pages all already point to the correct bookmark (no actual
    violation currently exists - this is a forward-looking regression
    guard, same as the Disclaimer Banner rule), and a 12th file that
    matched a raw text search for the button's label turned out to be an
    unrelated "More details" button with leftover matching text in one of
    its style-state variants, not its actual title - confirming this has to
    match on title, not a raw text search, to avoid exactly that false
    positive.

    Since this runs on every dashboard, a project with no "Visualize
    population" button at all produces nothing - no finding, no warning.
    Only when at least one such button exists but the "Employee
    demographics" bookmark itself can't be found is a warning surfaced
    (there's nothing to compare against or fix to) - this never guesses at
    what the "right" bookmark might be."""
    report_name = os.path.basename(report_folder)
    if _find_page_by_display_name(report_folder, _VISUALIZE_POPULATION_TARGET_PAGE_DISPLAY_NAME) is None:
        # No Employee demographics page at all - re-pointing the button is
        # pointless; check_visualize_population_orphaned flags it for
        # removal instead, so the two rules never fight over one button.
        return []
    target_name = _find_bookmark_by_display_name(report_folder, _VISUALIZE_POPULATION_TARGET_BOOKMARK_DISPLAY_NAME)
    buttons_seen = 0

    def _unquote(v):
        return v.strip().strip("'\"") if isinstance(v, str) else v

    findings: list[Finding] = []
    for page_id, page_name, _page_json_path in _iter_pbir_pages(report_folder):
        visuals_dir = os.path.join(report_folder, "definition", "pages", page_id, "visuals")
        if not os.path.isdir(visuals_dir):
            continue
        try:
            visual_ids = sorted(os.listdir(visuals_dir))
        except OSError:
            continue
        for visual_id in visual_ids:
            visual_json_path = os.path.join(visuals_dir, visual_id, "visual.json")
            if not os.path.isfile(visual_json_path):
                continue
            try:
                with open(visual_json_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                continue
            title_text = _read_visual_title(data)
            if not title_text or title_text.strip().lower() != _VISUALIZE_POPULATION_BUTTON_TITLE.lower():
                continue
            visual_obj = data.get("visual", {})
            if not isinstance(visual_obj, dict) or (visual_obj.get("visualType") or "").lower() != "actionbutton":
                continue  # same title text on a non-button visual has no bookmark action to check
            buttons_seen += 1
            if not target_name:
                continue  # warned once below - nothing to compare against

            link_list = (visual_obj.get("visualContainerObjects") or {}).get("visualLink")
            current_type = current_bookmark = None
            if isinstance(link_list, list) and link_list and isinstance(link_list[0], dict):
                props = link_list[0].get("properties", {})
                current_type = _unquote(_read_literal_value(props.get("type")))
                current_bookmark = _unquote(_read_literal_value(props.get("bookmark")))

            if current_type == "Bookmark" and current_bookmark == target_name:
                continue  # already points at the right bookmark

            visual = VisualRef(
                id=f"{report_name}|{page_id}|{visual_id}",
                report_name=report_name,
                report_path=report_folder,
                page_id=page_id,
                page_name=page_name,
                visual_id=visual_id,
                visual_type="actionButton",
                title=_VISUALIZE_POPULATION_BUTTON_TITLE,
                file_path=visual_json_path,
                format="pbir",
            )
            if current_bookmark is None:
                detail = (f"The 'Visualize population' button on the '{page_name}' page has no bookmark "
                          f"action configured - it should always point to the "
                          f"'{_VISUALIZE_POPULATION_TARGET_BOOKMARK_DISPLAY_NAME}' bookmark.")
            else:
                detail = (f"The 'Visualize population' button on the '{page_name}' page points to a "
                          f"different bookmark ('{current_bookmark}') instead of "
                          f"'{_VISUALIZE_POPULATION_TARGET_BOOKMARK_DISPLAY_NAME}'.")
            findings.append(Finding(
                rule_id=RULE_VISUALIZE_POPULATION_BOOKMARK,
                rule_label=RULE_LABELS[RULE_VISUALIZE_POPULATION_BOOKMARK],
                visual=visual,
                detail=detail,
                fixable=True,
                auto_select=True,
                fix_payload={"kind": "set_visualize_population_bookmark", "target_bookmark_name": target_name},
            ))
    if buttons_seen and not target_name and warnings is not None:
        warnings.append(
            f"Visualize population bookmark check: found {buttons_seen} 'Visualize population' "
            f"button{'s' if buttons_seen != 1 else ''} in '{report_name}', but no bookmark named "
            f"'{_VISUALIZE_POPULATION_TARGET_BOOKMARK_DISPLAY_NAME}' exists to point "
            f"{'them' if buttons_seen != 1 else 'it'} at - nothing was checked."
        )
    return findings


def check_visualize_population_orphaned(report_folder: str, warnings: Optional[list] = None) -> list[Finding]:
    """Generic rule 13 (all dashboards): if the report has no "Employee
    demographics" page (matched case-insensitively on page displayName),
    every "Visualize population" actionButton (same title-based match as
    rule 11) leads nowhere and must be removed. Fix deletes the button's
    visual.json and its now-empty folder - the same "delete_visual" fix the
    Plan design privacy-note rule uses, backed up first like every fix.

    Only the button itself is removed: if it sits inside a visual group,
    the group container and its other members are left exactly as they
    are. When the page DOES exist this rule produces nothing, and rule 11
    (bookmark target) takes over instead."""
    if _find_page_by_display_name(report_folder, _VISUALIZE_POPULATION_TARGET_PAGE_DISPLAY_NAME) is not None:
        return []
    report_name = os.path.basename(report_folder)
    findings: list[Finding] = []
    for page_id, page_name, _pj in _iter_pbir_pages(report_folder):
        visuals_dir = os.path.join(report_folder, "definition", "pages", page_id, "visuals")
        if not os.path.isdir(visuals_dir):
            continue
        try:
            visual_ids = sorted(os.listdir(visuals_dir))
        except OSError:
            continue
        for visual_id in visual_ids:
            path = os.path.join(visuals_dir, visual_id, "visual.json")
            if not os.path.isfile(path):
                continue
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                continue
            title_text = _read_visual_title(data)
            if not title_text or title_text.strip().lower() != _VISUALIZE_POPULATION_BUTTON_TITLE.lower():
                continue
            vis = data.get("visual")
            if not isinstance(vis, dict) or (vis.get("visualType") or "").lower() != "actionbutton":
                continue
            findings.append(Finding(
                rule_id=RULE_VISUALIZE_POPULATION_ORPHANED,
                rule_label=RULE_LABELS[RULE_VISUALIZE_POPULATION_ORPHANED],
                visual=VisualRef(
                    id=f"{report_name}|{page_id}|{visual_id}", report_name=report_name,
                    report_path=report_folder, page_id=page_id, page_name=page_name,
                    visual_id=visual_id, visual_type="actionButton",
                    title=_VISUALIZE_POPULATION_BUTTON_TITLE, file_path=path, format="pbir",
                ),
                detail=(f"This report has no '{_VISUALIZE_POPULATION_TARGET_PAGE_DISPLAY_NAME}' page, so the "
                        f"'Visualize population' button on the '{page_name}' page leads nowhere. Fixing "
                        f"deletes the button (backed up first)."),
                fixable=True,
                auto_select=True,
                fix_payload={"kind": "delete_visual"},
            ))
    return findings


def _iter_page_visuals(report_folder: str, page_id: Optional[str] = None):
    """Yields (page_id, page_name, visual_id, file_path, parsed_json) for
    every visual.json in the report (or just one page if page_id is given).
    Unparseable files are skipped."""
    for pid, page_name, _pj in _iter_pbir_pages(report_folder):
        if page_id is not None and pid != page_id:
            continue
        visuals_dir = os.path.join(report_folder, "definition", "pages", pid, "visuals")
        if not os.path.isdir(visuals_dir):
            continue
        try:
            visual_ids = sorted(os.listdir(visuals_dir))
        except OSError:
            continue
        for visual_id in visual_ids:
            path = os.path.join(visuals_dir, visual_id, "visual.json")
            if not os.path.isfile(path):
                continue
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                continue
            yield pid, page_name, visual_id, path, data


_PREVIOUS_PAGE_BUTTON_TITLE = "Previous page"


def check_previous_page_back(report_folder: str, warnings: Optional[list] = None) -> list[Finding]:
    """Generic rule 14: the "Employee demographics" page (case-insensitive -
    the real page is "Employee Demographics") must have a "Previous page"
    actionButton whose action type is Back.

    Grounded against the real DC & Benefit Affordability Dashboard: the
    button (title "Previous page", inside the page's filter group) has
    visualContainerObjects.visualLink[0].properties.type = "'Back'". It also
    carries leftover navigationSection/bookmark properties from an earlier
    action type - Power BI ignores those for a Back action, so the fix only
    ever rewrites "type" and leaves the rest alone.

    - Page missing: nothing here (rule 13 handles the knock-on effects).
    - Button present but not Back: fixable, auto-selected.
    - No "Previous page" button on the page at all: flagged for review
      only - creating a button means inventing its position and styling,
      which isn't something to guess at."""
    found = _find_page_by_display_name(report_folder, _VISUALIZE_POPULATION_TARGET_PAGE_DISPLAY_NAME)
    if found is None:
        return []
    page_dir, page_id = found
    report_name = os.path.basename(report_folder)

    def _unquote(v):
        return v.strip().strip("'\"") if isinstance(v, str) else v

    findings: list[Finding] = []
    page_name = None
    buttons = 0
    for pid, pname, visual_id, path, data in _iter_page_visuals(report_folder, page_id):
        page_name = pname
        title = _read_visual_title(data)
        vis = data.get("visual")
        if not title or title.strip().lower() != _PREVIOUS_PAGE_BUTTON_TITLE.lower():
            continue
        if not isinstance(vis, dict) or (vis.get("visualType") or "").lower() != "actionbutton":
            continue
        buttons += 1
        links = (vis.get("visualContainerObjects") or {}).get("visualLink")
        current = None
        if isinstance(links, list) and links and isinstance(links[0], dict):
            current = _unquote(_read_literal_value((links[0].get("properties") or {}).get("type")))
        if current == "Back":
            continue
        findings.append(Finding(
            rule_id=RULE_PREVIOUS_PAGE_BACK,
            rule_label=RULE_LABELS[RULE_PREVIOUS_PAGE_BACK],
            visual=VisualRef(
                id=f"{report_name}|{pid}|{visual_id}", report_name=report_name, report_path=report_folder,
                page_id=pid, page_name=pname, visual_id=visual_id, visual_type="actionButton",
                title=_PREVIOUS_PAGE_BUTTON_TITLE, file_path=path, format="pbir",
            ),
            detail=(f"The 'Previous page' button on the '{pname}' page has action type "
                    f"'{current or '(none)'}' - it should be 'Back'."),
            items=[current or "(none)"],
            expected=["Back"],
            fixable=True,
            auto_select=True,
            fix_payload={"kind": "set_button_action_back"},
        ))

    if buttons == 0:
        page_name = page_name or _VISUALIZE_POPULATION_TARGET_PAGE_DISPLAY_NAME
        page_json = os.path.join(page_dir, "page.json")
        findings.append(Finding(
            rule_id=RULE_PREVIOUS_PAGE_BACK,
            rule_label=RULE_LABELS[RULE_PREVIOUS_PAGE_BACK],
            visual=VisualRef(
                id=f"{report_name}|{page_id}|__previous_page__", report_name=report_name,
                report_path=report_folder, page_id=page_id, page_name=page_name,
                visual_id="previous-page-button", visual_type="page", title="Previous page button",
                file_path=page_json, format="page_root",
            ),
            detail=(f"The '{page_name}' page has no 'Previous page' button. Add one in Power BI Desktop "
                    f"(Insert > Buttons > Back, then name it 'Previous page'). Not auto-fixable - a new "
                    f"button's position and styling can't be safely guessed."),
            fixable=False,
            auto_select=False,
            fix_payload={},
        ))
    return findings


_SEGMENTATION_SLICER_TITLE = "Segmentations Slicer"


def check_segmentation_single_select(report_folder: str, warnings: Optional[list] = None) -> list[Finding]:
    """Generic rule 15: every slicer named "Segmentations Slicer" (its
    Selection-pane name = Format-pane title text, case-insensitive) must have
    Selection > Single select turned on.

    Grounded against the real DC & Benefit Affordability Dashboard (C:\\dev
    copy): 8 such slicers across 7 pages - 6 standard `slicer` and 2
    `advancedSlicerVisual` (the Heat map's tile-style ones) - all write the
    Format pane's "Single select" toggle as objects.selection[0].properties.
    strictSingleSelect = true (confirmed against a screenshot of that toggle
    showing On for the same visual). That is a different property from
    `singleSelect`, which is the "Multi-select with CTRL" toggle the
    ordinary filter slicers use - this rule never touches that one.
    Missing or false counts as not single-select. Fix sets
    strictSingleSelect to true, nothing else."""
    report_name = os.path.basename(report_folder)
    findings: list[Finding] = []
    for pid, pname, visual_id, path, data in _iter_page_visuals(report_folder):
        vis = data.get("visual")
        if not isinstance(vis, dict) or not is_slicer(vis.get("visualType")):
            continue
        title = _read_visual_title(data)
        if not title or title.strip().lower() != _SEGMENTATION_SLICER_TITLE.lower():
            continue
        sel = (vis.get("objects") or {}).get("selection")
        props = sel[0].get("properties", {}) if isinstance(sel, list) and sel and isinstance(sel[0], dict) else {}
        current = jsonutil.read_bool(props.get("strictSingleSelect"))
        if current is True:
            continue
        findings.append(Finding(
            rule_id=RULE_SEGMENTATION_SINGLE_SELECT,
            rule_label=RULE_LABELS[RULE_SEGMENTATION_SINGLE_SELECT],
            visual=VisualRef(
                id=f"{report_name}|{pid}|{visual_id}", report_name=report_name, report_path=report_folder,
                page_id=pid, page_name=pname, visual_id=visual_id, visual_type=vis.get("visualType", "slicer"),
                title=_SEGMENTATION_SLICER_TITLE, file_path=path, format="pbir",
            ),
            detail=(f"The Segmentations Slicer on the '{pname}' page allows multiple selections - "
                    f"Single select is {'off' if current is False else 'not set'}. It should be on."),
            fixable=True,
            auto_select=True,
            fix_payload={"kind": "strict_single_select"},
        ))
    return findings


def check_filter_values_sorted(report_folder: str, warnings: Optional[list] = None) -> list[Finding]:
    """Generic rule 12: every slicer must be sorted by its own field,
    ascending - i.e. in the slicer's "..." > Sort by menu, the slicer's own
    field is the ticked sort field and "Sort ascending" is ticked. Slicers
    only.

    Grounded against the real DC & Benefit Affordability Dashboard (145
    slicers). Power BI stores this choice in the slicer's
    query.sortDefinition:
      - No sortDefinition at all (143 slicers, incl. the Age slicer in the
        user's screenshot showing "Value" + "Sort ascending" ticked) =
        Power BI's default = own field, ascending -> compliant.
      - Explicit sort on the slicer's own field, Ascending (1 slicer)
        -> compliant.
      - Explicit sort on a field the slicer doesn't display (1 real case: a
        Heat map slicer showing 'Value view type'[View] sorted by
        'Benefit type filter'[Type]) -> violation.
      - Explicit Descending on any field -> violation.

    Fix removes the explicit sortDefinition, returning the slicer to the
    exact state the 143 compliant ones are in (own field, ascending). Per
    the user's instruction these are flagged for review and never
    auto-selected - fixed only when ticked."""
    report_name = os.path.basename(report_folder)
    findings: list[Finding] = []
    for pid, pname, visual_id, path, data in _iter_page_visuals(report_folder):
        vis = data.get("visual")
        if not isinstance(vis, dict) or not is_slicer(vis.get("visualType")):
            continue
        sort_def = (vis.get("query") or {}).get("sortDefinition")
        sorts = sort_def.get("sort") if isinstance(sort_def, dict) else None
        if not isinstance(sorts, list) or not sorts:
            continue  # Power BI default: own field, ascending
        own_ids = {_field_identity(f) for f in _visual_field_projections(vis)} - {None}
        own_label = ", ".join(f"{i[1]}[{i[2]}]" for i in sorted(own_ids)) or "its own field"
        problems = []
        for s in sorts:
            if not isinstance(s, dict):
                continue
            sid = _field_identity(s.get("field"))
            direction = s.get("direction")
            is_desc = direction in ("Descending", 2)
            label = f"{sid[1]}[{sid[2]}]" if sid else "another field"
            if sid and own_ids and sid not in own_ids:
                problems.append(f"sorted by {label} instead of {own_label}"
                                + (" (descending)" if is_desc else ""))
            elif is_desc:
                problems.append(f"sorted descending by {label}")
        if not problems:
            continue
        findings.append(Finding(
            rule_id=RULE_FILTER_VALUES_SORTED,
            rule_label=RULE_LABELS[RULE_FILTER_VALUES_SORTED],
            visual=VisualRef(
                id=f"{report_name}|{pid}|{visual_id}", report_name=report_name, report_path=report_folder,
                page_id=pid, page_name=pname, visual_id=visual_id, visual_type=vis.get("visualType", "slicer"),
                title=_read_visual_title(data) or f"Slicer - {own_label}", file_path=path, format="pbir",
            ),
            detail=(f"This slicer ({own_label}) is " + "; ".join(problems) + ". In its \u2026 > Sort by "
                    f"menu it should be sorted by its own field, ascending. Fixing resets it to that "
                    f"(Power BI's default for a slicer)."),
            items=problems,
            expected=[f"Sort by {own_label}, ascending"],
            fixable=True,
            auto_select=False,
            fix_payload={"kind": "remove_slicer_sort"},
        ))
    return findings


def check_all(visual: VisualRef, enabled_rules: Optional[set] = None) -> list[Finding]:
    findings: list[Finding] = []
    if enabled_rules is None or RULE_SLICER_HEADER in enabled_rules or RULE_VISUAL_HEADER_FOCUS in enabled_rules:
        findings.extend(check_header_rules(visual))
    if enabled_rules is None or RULE_SORT_ASCENDING in enabled_rules:
        findings.extend(check_sort_rule(visual))
    if enabled_rules is None or RULE_COLOR_PALETTE in enabled_rules:
        findings.extend(check_visual_colors(visual))
    if enabled_rules is None or RULE_FILTERS_LOCKED_HIDDEN in enabled_rules:
        findings.extend(check_visual_filters(visual))
    if enabled_rules is None or RULE_LEGEND_BOLD in enabled_rules:
        findings.extend(check_legend_bold(visual))
    if enabled_rules is None or RULE_DATA_POINT_BORDER in enabled_rules:
        findings.extend(check_data_point_border(visual))
    if enabled_rules is None or RULE_TABLE_VALUES_CENTERED in enabled_rules:
        findings.extend(check_table_alignment(visual))
    if enabled_rules is not None:
        findings = [f for f in findings if f.rule_id in enabled_rules]
    return findings
