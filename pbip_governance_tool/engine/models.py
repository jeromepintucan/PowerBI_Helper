from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Optional
import uuid


@dataclass
class VisualRef:
    """Points at one visual.json (or one visualContainer in a legacy report.json)."""
    id: str
    report_name: str          # e.g. "Sales.Report"
    report_path: str          # absolute path to the *.Report folder
    page_id: str
    page_name: str            # display name if available, else page id
    visual_id: str
    visual_type: str
    title: str                # best-effort human title for the visual
    file_path: str            # file that must be re-written to apply a fix
    format: str                # "pbir" or "legacy"
    # For legacy format, the visual's JSON lives inside a "config" string
    # nested inside a bigger report.json. container_index locates it.
    container_index: Optional[int] = None
    page_index: Optional[int] = None


@dataclass
class Finding:
    """One rule violation found on one visual."""
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:10])
    rule_id: str = ""
    rule_label: str = ""
    severity: str = "warning"
    visual: VisualRef = None
    detail: str = ""
    items: list = field(default_factory=list)  # short chip labels, e.g. per-icon
    expected: list = field(default_factory=list)  # "should be" values, e.g. approved palette
    fixable: bool = True
    # Whether this finding is swept in by "Fix Selected" / "select all" by
    # default. False means: fixable if the user deliberately checks it, but
    # never auto-included - used for findings with no single "obviously
    # correct" replacement (e.g. a hardcoded color snapped to its nearest
    # palette match), where a human should eyeball it first.
    auto_select: bool = True
    fix_payload: Any = None   # rule-specific data the fixer needs

    def to_dict(self):
        v = self.visual
        return {
            "id": self.id,
            "rule_id": self.rule_id,
            "rule_label": self.rule_label,
            "severity": self.severity,
            "detail": self.detail,
            "items": self.items,
            "expected": self.expected,
            "fixable": self.fixable,
            "auto_select": self.auto_select,
            "report_name": v.report_name if v else None,
            "page_name": v.page_name if v else None,
            "visual_type": v.visual_type if v else None,
            "visual_title": v.title if v else None,
            "visual_id": v.visual_id if v else None,
            "file_path": v.file_path if v else None,
        }


@dataclass
class ScanResult:
    root_path: str
    reports_scanned: list = field(default_factory=list)
    visuals_scanned: int = 0
    pages_scanned: int = 0
    findings: list = field(default_factory=list)
    warnings: list = field(default_factory=list)  # e.g. legacy-format notices

    def to_dict(self):
        by_rule = {}
        for f in self.findings:
            by_rule.setdefault(f.rule_id, []).append(f.to_dict())
        return {
            "root_path": self.root_path,
            "reports_scanned": self.reports_scanned,
            "visuals_scanned": self.visuals_scanned,
            "pages_scanned": self.pages_scanned,
            "total_findings": len(self.findings),
            "findings_by_rule": by_rule,
            "findings": [f.to_dict() for f in self.findings],
            "warnings": self.warnings,
        }
