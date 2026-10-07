from __future__ import annotations
import os
from typing import Optional

from . import scanner, rules, fixer
from .models import ScanResult


def run_scan(root_path: str, enabled_rules: Optional[list] = None, rule_options: Optional[dict] = None) -> ScanResult:
    """enabled_rules: list of rule ids from rules.ALL_RULE_IDS (or a
    dashboard-family-specific id from rules.DASHBOARD_FAMILY_RULE_IDS) to
    actually check (the pre-scan checklist in the UI). None/empty-list
    means "run every generic rule" - kept backwards compatible for any
    other caller. Family-specific rules are deliberately NOT run in that
    "run everything" case - they only ever run when explicitly named in
    enabled_rules (i.e. the family dropdown was actually used).

    rule_options: small per-rule config the UI collects beyond a plain
    on/off checkbox - currently just
    {"disclaimer_banner_pages": [...]} for RULE_DC_DISCLAIMER_BANNER_SHOWN
    (which pages to check, from that rule's own page checkboxes). None/
    missing falls back to that rule's own hardcoded default page list."""
    root_path = os.path.abspath(root_path)
    result = ScanResult(root_path=root_path)
    rule_set = set(enabled_rules) if enabled_rules else None

    report_folders = list(scanner.find_report_folders(root_path))
    if not report_folders:
        result.warnings.append(
            "No *.Report folder was found under the selected path. Make sure "
            "you selected the folder that contains your .pbip file (or a "
            "parent folder containing one or more PBIP projects)."
        )
        return result

    seen_pages = set()
    for report_folder in report_folders:
        result.reports_scanned.append(os.path.basename(report_folder))
        legacy = scanner.is_legacy_report(report_folder)
        if legacy:
            result.warnings.append(
                f"'{os.path.basename(report_folder)}' uses the legacy "
                f"(non-PBIR) report.json format. Support for this format is "
                f"best-effort — review fixes carefully before trusting them."
            )
            visuals = list(scanner.scan_legacy_report(report_folder))
        else:
            visuals = list(scanner.scan_pbir_report(report_folder))

        if rule_set is None or rules.RULE_COLOR_PALETTE in rule_set:
            try:
                result.findings.extend(rules.check_theme_colors(report_folder, result.warnings))
            except Exception as e:
                result.warnings.append(
                    f"Could not check theme colors for '{os.path.basename(report_folder)}': {e}"
                )

        if rule_set is None or rules.RULE_EXPORT_DISABLED in rule_set:
            try:
                result.findings.extend(rules.check_export_disabled(report_folder, result.warnings))
            except Exception as e:
                result.warnings.append(
                    f"Could not check export setting for '{os.path.basename(report_folder)}': {e}"
                )

        if rule_set is None or rules.RULE_FILTERS_LOCKED_HIDDEN in rule_set:
            try:
                result.findings.extend(rules.check_report_filters(report_folder, result.warnings))
            except Exception as e:
                result.warnings.append(
                    f"Could not check report-level filters for '{os.path.basename(report_folder)}': {e}"
                )
            try:
                result.findings.extend(rules.check_page_filters(report_folder, result.warnings))
            except Exception as e:
                result.warnings.append(
                    f"Could not check page-level filters for '{os.path.basename(report_folder)}': {e}"
                )

        if rule_set is None or rules.RULE_DATA_SOURCE_REVIEW in rule_set:
            try:
                result.findings.extend(rules.check_data_source_review(report_folder, result.warnings))
            except Exception as e:
                result.warnings.append(
                    f"Could not check data source connections for '{os.path.basename(report_folder)}': {e}"
                )

        if rule_set is None or rules.RULE_VISUALIZE_POPULATION_BOOKMARK in rule_set:
            try:
                result.findings.extend(
                    rules.check_visualize_population_bookmark(report_folder, result.warnings)
                )
            except Exception as e:
                result.warnings.append(
                    f"Could not check 'Visualize population' bookmarks for "
                    f"'{os.path.basename(report_folder)}': {e}"
                )

        if rule_set is None or rules.RULE_VISUALIZE_POPULATION_ORPHANED in rule_set:
            try:
                result.findings.extend(rules.check_visualize_population_orphaned(report_folder, result.warnings))
            except Exception as e:
                result.warnings.append(
                    f"Could not check for orphaned 'Visualize population' buttons in "
                    f"'{os.path.basename(report_folder)}': {e}"
                )

        for rid, fn, what in (
            (rules.RULE_PREVIOUS_PAGE_BACK, rules.check_previous_page_back, "the Previous page button"),
            (rules.RULE_SEGMENTATION_SINGLE_SELECT, rules.check_segmentation_single_select,
             "Segmentations Slicer single select"),
        ):
            if rule_set is None or rid in rule_set:
                try:
                    result.findings.extend(fn(report_folder, result.warnings))
                except Exception as e:
                    result.warnings.append(f"Could not check {what} for '{os.path.basename(report_folder)}': {e}")

        if rule_set is None or rules.RULE_FILTER_VALUES_SORTED in rule_set:
            try:
                result.findings.extend(rules.check_filter_values_sorted(report_folder, result.warnings))
            except Exception as e:
                result.warnings.append(
                    f"Could not check slicer value sort order for '{os.path.basename(report_folder)}': {e}"
                )

        # Dashboard-family-specific rules - opt-in only, never part of the
        # "run everything" (rule_set is None) default.
        if rule_set is not None and rules.RULE_DC_HEADCOUNT_MEASURE in rule_set:
            try:
                result.findings.extend(rules.check_dc_headcount_measure(report_folder, result.warnings))
            except Exception as e:
                result.warnings.append(
                    f"Could not check Headcount measure for '{os.path.basename(report_folder)}': {e}"
                )

        if rule_set is not None and rules.RULE_DC_BASELINE_CARDINALITY in rule_set:
            try:
                result.findings.extend(rules.check_dc_baseline_cardinality(report_folder, result.warnings))
            except Exception as e:
                result.warnings.append(
                    f"Could not check Baseline/Employee_Segments cardinality for "
                    f"'{os.path.basename(report_folder)}': {e}"
                )

        if rule_set is not None and rules.RULE_DC_PLAN_DESIGN_NO_PRIVACY_NOTE in rule_set:
            try:
                result.findings.extend(rules.check_dc_plan_design_no_privacy_note(report_folder, result.warnings))
            except Exception as e:
                result.warnings.append(
                    f"Could not check Plan design privacy note for '{os.path.basename(report_folder)}': {e}"
                )

        if rule_set is not None and rules.RULE_DC_DISCLAIMER_BANNER_SHOWN in rule_set:
            try:
                target_pages = (rule_options or {}).get("disclaimer_banner_pages")
                result.findings.extend(
                    rules.check_dc_disclaimer_banner_shown(report_folder, target_pages, result.warnings)
                )
            except Exception as e:
                result.warnings.append(
                    f"Could not check Disclaimer Banner visibility for '{os.path.basename(report_folder)}': {e}"
                )

        for v in visuals:
            seen_pages.add((v.report_name, v.page_id))
            result.visuals_scanned += 1
            try:
                result.findings.extend(rules.check_all(v, rule_set))
            except Exception as e:
                result.warnings.append(
                    f"Could not analyze visual '{v.visual_id}' on page "
                    f"'{v.page_name}' ({v.report_name}): {e}"
                )

    result.pages_scanned = len(seen_pages)
    return result


def apply_fixes(root_path: str, scan_result: ScanResult, finding_ids: list[str]) -> dict:
    id_set = set(finding_ids)
    # f.fixable guard is defensive: detect-only findings (e.g. hardcoded
    # colors with no safe automatic replacement) should never actually be
    # written even if an id somehow ends up selected.
    selected = [f for f in scan_result.findings if f.id in id_set and f.fixable]
    if not selected:
        return {"backup_folder": None, "applied_count": 0, "failed_count": 0, "applied": [], "failed": []}
    return fixer.apply_fixes(root_path, selected)
