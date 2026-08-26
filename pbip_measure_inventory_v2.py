import json
import re
import sys
import subprocess
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import pandas as pd


APP_NAME = "PBIP Measure Inventory"
APP_VERSION = "2.0.0"
DEFAULT_OUTPUT_FILENAME = "PowerBI_Measure_Inventory.xlsx"
OUTPUT_SHEET_NAME = "Measure_Inventory"

MEASURE_INVENTORY_COLUMNS = [
    "Table Name",
    "Measure Name",
    "DAX",
    "Direct Visual Usage Count",
    "Pages Used",
    "Visuals Used",
    "Business-Friendly Description",
    "Business-Friendly Metric Formula",
    "Confidence",
]

# QA target format excludes technical/parameter helper measures.
EXCLUDED_MEASURE_NAMES = {"blank"}
EXCLUDED_MEASURE_PREFIXES = ("!",)
EXCLUDED_TABLE_PREFIXES = ("param_",)


def read_text_file(file_path):
    try:
        return file_path.read_text(encoding="utf-8-sig", errors="ignore")
    except Exception as exc:
        print(f"Could not read file: {file_path}")
        print(f"Reason: {exc}")
        return None


def read_json_file(file_path):
    text = read_text_file(file_path)
    if text is None:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        print(f"Invalid JSON skipped: {file_path}")
        print(f"Reason: {exc}")
        return None


def normalize_name(value):
    return "" if value is None else str(value).strip().casefold()


def count_indentation(text):
    expanded_text = text.expandtabs(4)
    return len(expanded_text) - len(expanded_text.lstrip())


def remove_tmdl_quotes(value):
    value = value.strip()
    if len(value) >= 2 and value.startswith("'") and value.endswith("'"):
        value = value[1:-1].replace("''", "'")
    return value.strip()


def should_include_measure(measure):
    # table_name = normalize_name(measure.get("Table Name"))
    # measure_name = normalize_name(measure.get("Measure Name"))

    # if measure_name in EXCLUDED_MEASURE_NAMES:
    #     return False
    # if any(measure_name.startswith(prefix.casefold()) for prefix in EXCLUDED_MEASURE_PREFIXES):
    #     return False
    # if any(table_name.startswith(prefix.casefold()) for prefix in EXCLUDED_TABLE_PREFIXES):
    #     return False
    # return True
    return True


def find_project_folders(pbip_folder):
    if not pbip_folder.exists():
        raise FileNotFoundError(f"The selected folder does not exist:\n{pbip_folder}")
    if not pbip_folder.is_dir():
        raise NotADirectoryError(f"The selected path is not a folder:\n{pbip_folder}")

    report_folders = sorted(
        [
            item
            for item in pbip_folder.iterdir()
            if item.is_dir() and item.name.casefold().endswith(".report")
        ],
        key=lambda item: item.name.casefold(),
    )
    semantic_folders = sorted(
        [
            item
            for item in pbip_folder.iterdir()
            if item.is_dir() and item.name.casefold().endswith(".semanticmodel")
        ],
        key=lambda item: item.name.casefold(),
    )

    if not report_folders:
        raise FileNotFoundError(
            "No folder ending in '.Report' was found inside:\n"
            f"{pbip_folder}\n\nSelect the parent PBIP folder containing both "
            "the .Report and .SemanticModel folders."
        )
    if not semantic_folders:
        raise FileNotFoundError(
            "No folder ending in '.SemanticModel' was found inside:\n"
            f"{pbip_folder}\n\nSelect the parent PBIP folder containing both "
            "the .Report and .SemanticModel folders."
        )

    return report_folders[0], semantic_folders[0]


TABLE_PATTERN = re.compile(
    r"^\s*table\s+(?P<name>'(?:[^']|'')+'|.+?)\s*$", re.IGNORECASE
)
MEASURE_PATTERN = re.compile(
    r"""
    ^(?P<indent>\s*)measure\s+
    (?P<name>'(?:[^']|'')+'|[^=]+?)
    \s*=\s*(?P<expression>.*)$
    """,
    re.IGNORECASE | re.VERBOSE,
)
TMDL_OBJECT_PATTERN = re.compile(
    r"^\s*(measure|column|calculatedColumn|hierarchy|level|partition|"
    r"calculationGroup|calculationItem|relationship)\b",
    re.IGNORECASE,
)
TMDL_METADATA_PATTERN = re.compile(
    r"^\s*(formatString|formatStringDefinition|displayFolder|description|"
    r"isHidden|dataCategory|lineageTag|sourceLineageTag|detailRowsDefinition|"
    r"changedProperty|annotation)(\s*:|\s+)",
    re.IGNORECASE,
)


def remove_common_indentation(lines):
    non_blank_lines = [line for line in lines if line.strip()]
    if not non_blank_lines:
        return []
    minimum_indent = min(count_indentation(line) for line in non_blank_lines)
    return [
        line.expandtabs(4)[minimum_indent:] if line.strip() else ""
        for line in lines
    ]


def clean_dax_expression(expression_lines):
    cleaned_lines = []
    metadata_prefixes = (
        "annotation ",
        "formatString:",
        "formatStringDefinition:",
        "displayFolder:",
        "description:",
        "lineageTag:",
        "sourceLineageTag:",
        "dataCategory:",
        "isHidden:",
    )
    for line in expression_lines:
        stripped = line.strip()
        if stripped == "```":
            continue
        if stripped.startswith(metadata_prefixes):
            continue
        cleaned_lines.append(line.rstrip())

    while cleaned_lines and not cleaned_lines[0].strip():
        cleaned_lines.pop(0)
    while cleaned_lines and not cleaned_lines[-1].strip():
        cleaned_lines.pop()

    cleaned_lines = remove_common_indentation(cleaned_lines)
    return "\n".join(cleaned_lines).strip()


def parse_tmdl_file(tmdl_file):
    text = read_text_file(tmdl_file)
    if text is None:
        return []

    lines = text.splitlines()
    measures = []
    table_name = tmdl_file.stem

    for line in lines:
        table_match = TABLE_PATTERN.match(line)
        if table_match:
            table_name = remove_tmdl_quotes(table_match.group("name"))
            break

    line_index = 0
    while line_index < len(lines):
        current_line = lines[line_index]
        measure_match = MEASURE_PATTERN.match(current_line)
        if not measure_match:
            line_index += 1
            continue

        measure_name = remove_tmdl_quotes(measure_match.group("name"))
        measure_indent = count_indentation(measure_match.group("indent"))
        first_expression_line = measure_match.group("expression").rstrip()
        expression_lines = []
        if first_expression_line:
            expression_lines.append(first_expression_line)

        line_index += 1
        while line_index < len(lines):
            next_line = lines[line_index]
            if not next_line.strip():
                expression_lines.append("")
                line_index += 1
                continue

            next_indent = count_indentation(next_line)
            if next_indent <= measure_indent and TMDL_OBJECT_PATTERN.match(next_line):
                break

            if TMDL_METADATA_PATTERN.match(next_line):
                metadata_indent = next_indent
                line_index += 1
                while line_index < len(lines):
                    nested_line = lines[line_index]
                    if not nested_line.strip():
                        line_index += 1
                        continue
                    if count_indentation(nested_line) > metadata_indent:
                        line_index += 1
                    else:
                        break
                continue

            expression_lines.append(next_line)
            line_index += 1

        measures.append(
            {
                "Table Name": table_name,
                "Measure Name": measure_name,
                "DAX": clean_dax_expression(expression_lines),
            }
        )

    return measures


def extract_all_measures(semantic_folder):
    tmdl_files = sorted(
        semantic_folder.rglob("*.tmdl"), key=lambda item: str(item).casefold()
    )
    all_measures = []
    for tmdl_file in tmdl_files:
        all_measures.extend(parse_tmdl_file(tmdl_file))

    unique_measures = {}
    for measure in all_measures:
        unique_key = (
            normalize_name(measure["Table Name"]),
            normalize_name(measure["Measure Name"]),
        )
        if unique_key not in unique_measures:
            unique_measures[unique_key] = measure

    return list(unique_measures.values())


def create_measure_lookups(measures):
    exact_lookup = {}
    name_lookup = {}
    for measure in measures:
        table_key = normalize_name(measure["Table Name"])
        measure_key = normalize_name(measure["Measure Name"])
        exact_lookup[(table_key, measure_key)] = measure
        name_lookup.setdefault(measure_key, []).append(measure)
    return exact_lookup, name_lookup


def get_page_name(page_data, page_folder):
    if not isinstance(page_data, dict):
        return page_folder.name
    return page_data.get("displayName") or page_data.get("name") or page_folder.name


def get_visual_type(visual_data):
    if not isinstance(visual_data, dict):
        return "Unknown"
    visual = visual_data.get("visual")
    if isinstance(visual, dict):
        return visual.get("visualType") or visual_data.get("visualType") or "Unknown"
    return visual_data.get("visualType") or "Unknown"


def extract_literal_value(obj):
    if not isinstance(obj, dict):
        return None
    current_object = obj.get("expr", obj)
    if not isinstance(current_object, dict):
        return None
    literal = current_object.get("Literal")
    if not isinstance(literal, dict):
        return None
    value = literal.get("Value")
    if not isinstance(value, str):
        return None
    value = value.strip()
    if len(value) >= 2 and value.startswith("'") and value.endswith("'"):
        value = value[1:-1].replace("''", "'")
    return value.strip()


def find_title_in_collection(collection):
    if not isinstance(collection, dict):
        return None
    title_objects = collection.get("title")
    if not isinstance(title_objects, list):
        return None
    for title_object in title_objects:
        if not isinstance(title_object, dict):
            continue
        properties = title_object.get("properties")
        if not isinstance(properties, dict):
            continue
        title = extract_literal_value(properties.get("text"))
        if title:
            return title
    return None


def get_visual_name(visual_data, visual_type):
    visual_id = visual_data.get("name", "UnknownVisual")
    visual = visual_data.get("visual")
    if isinstance(visual, dict):
        title = find_title_in_collection(visual.get("visualContainerObjects"))
        if title:
            return title
        title = find_title_in_collection(visual.get("objects"))
        if title:
            return title
    root_title = visual_data.get("title")
    if isinstance(root_title, str) and root_title.strip():
        return root_title.strip()
    return f"Unnamed {visual_type} ({visual_id})"


def get_measure_entity(measure_object):
    if not isinstance(measure_object, dict):
        return ""
    expression = measure_object.get("Expression")
    if not isinstance(expression, dict):
        return ""
    source_reference = expression.get("SourceRef")
    if not isinstance(source_reference, dict):
        return ""
    entity = source_reference.get("Entity")
    return entity.strip() if isinstance(entity, str) else ""


def find_measure_references(obj, results):
    if isinstance(obj, dict):
        for key, value in obj.items():
            if key.casefold() == "measure" and isinstance(value, dict):
                measure_name = value.get("Property")
                measure_table = get_measure_entity(value)
                if isinstance(measure_name, str) and measure_name.strip():
                    results.add((measure_table, measure_name.strip()))
            find_measure_references(value, results)
    elif isinstance(obj, list):
        for list_item in obj:
            find_measure_references(list_item, results)


def resolve_measure(table_name, measure_name, exact_lookup, name_lookup):
    table_key = normalize_name(table_name)
    measure_key = normalize_name(measure_name)
    exact_match = exact_lookup.get((table_key, measure_key))
    if exact_match:
        return exact_match
    possible_matches = name_lookup.get(measure_key, [])
    return possible_matches[0] if len(possible_matches) == 1 else None


def extract_visual_measure_usage(
    report_folder, exact_lookup, name_lookup, progress_callback=None
):
    matched_records = []
    unmatched_count = 0
    page_count = 0
    visual_count = 0
    pages_folder = report_folder / "definition" / "pages"

    if not pages_folder.exists():
        raise FileNotFoundError(
            f"The expected report pages folder was not found:\n{pages_folder}"
        )

    page_json_files = sorted(
        pages_folder.glob("*/page.json"), key=lambda item: str(item).casefold()
    )
    if not page_json_files:
        raise FileNotFoundError(f"No page.json files were found inside:\n{pages_folder}")

    total_pages = len(page_json_files)
    for page_number, page_json_file in enumerate(page_json_files, start=1):
        page_folder = page_json_file.parent
        page_data = read_json_file(page_json_file)
        if page_data is None:
            continue

        page_count += 1
        page_name = get_page_name(page_data, page_folder)
        if progress_callback:
            progress_callback(
                f"Scanning page {page_number} of {total_pages}: {page_name}",
                page_number,
                total_pages,
            )

        visuals_folder = page_folder / "visuals"
        if not visuals_folder.exists():
            continue

        visual_json_files = sorted(
            visuals_folder.glob("*/visual.json"),
            key=lambda item: str(item).casefold(),
        )
        for visual_json_file in visual_json_files:
            visual_data = read_json_file(visual_json_file)
            if visual_data is None:
                continue

            visual_count += 1
            visual_type = get_visual_type(visual_data)
            visual_name = get_visual_name(visual_data, visual_type)
            measure_references = set()
            find_measure_references(visual_data, measure_references)

            for table_name, measure_name in measure_references:
                matched_measure = resolve_measure(
                    table_name, measure_name, exact_lookup, name_lookup
                )
                if matched_measure:
                    matched_records.append(
                        {
                            "Page Name": page_name,
                            "Visual Name": visual_name,
                            "Visual Type": visual_type,
                            "Measure Table": matched_measure["Table Name"],
                            "Measure Name": matched_measure["Measure Name"],
                        }
                    )
                else:
                    unmatched_count += 1

    return matched_records, unmatched_count, page_count, visual_count


def build_measure_inventory(measures, matched_records):
    usage_lookup = {}
    for record in matched_records:
        key = (
            normalize_name(record["Measure Table"]),
            normalize_name(record["Measure Name"]),
        )
        usage_lookup.setdefault(key, {"visuals": set()})
        usage_lookup[key]["visuals"].add(
            (
                record["Page Name"],
                record["Visual Name"],
                record["Visual Type"],
            )
        )

    inventory_records = []
    for measure in measures:
        if not should_include_measure(measure):
            continue

        key = (
            normalize_name(measure["Table Name"]),
            normalize_name(measure["Measure Name"]),
        )
        visual_usages = usage_lookup.get(key, {"visuals": set()})["visuals"]
        sorted_usages = sorted(
            visual_usages,
            key=lambda item: (
                item[0].casefold(),
                item[1].casefold(),
                item[2].casefold(),
            ),
        )
        sorted_pages = sorted({item[0] for item in sorted_usages}, key=str.casefold)

        page_text = ", ".join(sorted_pages)
        visual_text = "; ".join(
            f"{page}: {visual}" for page, visual, _visual_type in sorted_usages
        )

        inventory_records.append(
            {
                "Table Name": measure["Table Name"],
                "Measure Name": measure["Measure Name"],
                "DAX": measure["DAX"],
                "Direct Visual Usage Count": len(sorted_usages),
                "Pages Used": page_text,
                "Visuals Used": visual_text,
                "Business-Friendly Description": "",
                "Business-Friendly Metric Formula": "",
                "Confidence": "",
            }
        )

    return inventory_records


def export_to_excel(output_file, inventory_records):
    inventory_df = pd.DataFrame(
        inventory_records, columns=MEASURE_INVENTORY_COLUMNS
    )
    if not inventory_df.empty:
        inventory_df = (
            inventory_df.drop_duplicates(
                subset=["Table Name", "Measure Name"], keep="first"
            )
            .sort_values(
                by=["Table Name", "Measure Name"],
                key=lambda series: series.astype(str).str.casefold(),
            )
            .reset_index(drop=True)
        )

    output_file.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(output_file, engine="xlsxwriter") as writer:
        inventory_df.to_excel(writer, sheet_name=OUTPUT_SHEET_NAME, index=False)

        workbook = writer.book
        worksheet = writer.sheets[OUTPUT_SHEET_NAME]
        worksheet.hide_gridlines(2)
        worksheet.freeze_panes(1, 0)
        worksheet.autofilter(0, 0, len(inventory_df), len(MEASURE_INVENTORY_COLUMNS) - 1)
        worksheet.set_row(0, 34)

        header_format = workbook.add_format({
            "bold": True,
            "font_color": "#FFFFFF",
            "bg_color": "#1F4E78",
            "align": "center",
            "valign": "vcenter",
            "text_wrap": True,
            "border": 1,
        })
        imported_format = workbook.add_format({
            "font_color": "#008000",
            "valign": "top",
            "text_wrap": True,
        })
        dax_format = workbook.add_format({
            "font_name": "Consolas",
            "font_size": 9,
            "font_color": "#008000",
            "valign": "top",
            "text_wrap": True,
        })
        centered_imported_format = workbook.add_format({
            "font_color": "#008000",
            "align": "center",
            "valign": "top",
        })
        ai_input_format = workbook.add_format({
            "font_color": "#0000FF",
            "bg_color": "#FFF2CC",
            "valign": "top",
            "text_wrap": True,
        })
        ai_confidence_format = workbook.add_format({
            "font_color": "#0000FF",
            "bg_color": "#FFF2CC",
            "align": "center",
            "valign": "top",
        })

        for column_index, header in enumerate(MEASURE_INVENTORY_COLUMNS):
            worksheet.write(0, column_index, header, header_format)

        widths = {
            "Table Name": 28,
            "Measure Name": 38,
            "DAX": 80,
            "Direct Visual Usage Count": 18,
            "Pages Used": 36,
            "Visuals Used": 60,
            "Business-Friendly Description": 58,
            "Business-Friendly Metric Formula": 68,
            "Confidence": 14,
        }

        for column_index, header in enumerate(MEASURE_INVENTORY_COLUMNS):
            if header == "DAX":
                cell_format = dax_format
            elif header == "Direct Visual Usage Count":
                cell_format = centered_imported_format
            elif header == "Confidence":
                cell_format = ai_confidence_format
            elif header in {
                "Business-Friendly Description",
                "Business-Friendly Metric Formula",
            }:
                cell_format = ai_input_format
            else:
                cell_format = imported_format
            worksheet.set_column(
                column_index, column_index, widths.get(header, 22), cell_format
            )

def open_file_or_folder(path):
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"The path does not exist:\n{path}")
    if sys.platform.startswith("win"):
        if path.is_file():
            subprocess.Popen(["cmd", "/c", "start", "", str(path)], shell=False)
        else:
            subprocess.Popen(["explorer", str(path)])
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])


class PBIPMeasureInventoryApp:
    def __init__(self, root):
        self.root = root
        self.root.title(f"{APP_NAME} {APP_VERSION}")
        self.root.geometry("820x580")
        self.root.minsize(760, 540)

        self.pbip_folder_var = tk.StringVar()
        self.output_file_var = tk.StringVar()
        self.status_var = tk.StringVar(
            value="Select a PBIP project folder to begin."
        )

        self.create_styles()
        self.create_interface()

    def create_styles(self):
        style = ttk.Style()
        try:
            style.theme_use("vista")
        except tk.TclError:
            pass
        style.configure("Title.TLabel", font=("Segoe UI", 18, "bold"))
        style.configure(
            "Subtitle.TLabel", font=("Segoe UI", 10), foreground="#555555"
        )
        style.configure("Section.TLabel", font=("Segoe UI", 10, "bold"))
        style.configure("Primary.TButton", font=("Segoe UI", 10, "bold"))

    def create_interface(self):
        main_frame = ttk.Frame(self.root, padding=24)
        main_frame.pack(fill="both", expand=True)

        ttk.Label(main_frame, text=APP_NAME, style="Title.TLabel").pack(anchor="w")
        ttk.Label(
            main_frame,
            text=(
                "Create a single AI-ready Measure_Inventory workbook from a PBIP project."
            ),
            style="Subtitle.TLabel",
        ).pack(anchor="w", pady=(2, 22))

        ttk.Label(
            main_frame, text="PBIP project folder", style="Section.TLabel"
        ).pack(anchor="w", pady=(0, 5))
        project_frame = ttk.Frame(main_frame)
        project_frame.pack(fill="x", pady=(0, 16))
        self.project_entry = ttk.Entry(
            project_frame, textvariable=self.pbip_folder_var
        )
        self.project_entry.pack(side="left", fill="x", expand=True)
        ttk.Button(
            project_frame, text="Browse", command=self.browse_pbip_folder
        ).pack(side="left", padx=(8, 0))

        ttk.Label(
            main_frame, text="Excel output file", style="Section.TLabel"
        ).pack(anchor="w", pady=(0, 5))
        output_frame = ttk.Frame(main_frame)
        output_frame.pack(fill="x", pady=(0, 22))
        self.output_entry = ttk.Entry(
            output_frame, textvariable=self.output_file_var
        )
        self.output_entry.pack(side="left", fill="x", expand=True)
        ttk.Button(
            output_frame, text="Browse", command=self.browse_output_file
        ).pack(side="left", padx=(8, 0))

        self.generate_button = ttk.Button(
            main_frame,
            text="Generate AI-Ready Measure Inventory",
            command=self.generate_inventory,
            style="Primary.TButton",
        )
        self.generate_button.pack(anchor="w", pady=(0, 20))

        status_frame = ttk.LabelFrame(main_frame, text="Status", padding=14)
        status_frame.pack(fill="both", expand=True)
        self.status_label = ttk.Label(
            status_frame,
            textvariable=self.status_var,
            justify="left",
            anchor="nw",
            wraplength=720,
        )
        self.status_label.pack(fill="both", expand=True)

        self.progress_bar = ttk.Progressbar(
            main_frame, mode="determinate", maximum=100
        )
        self.progress_bar.pack(fill="x", pady=(16, 12))

        action_frame = ttk.Frame(main_frame)
        action_frame.pack(fill="x")
        self.open_file_button = ttk.Button(
            action_frame,
            text="Open Excel File",
            command=self.open_output_file,
            state="disabled",
        )
        self.open_file_button.pack(side="left")
        self.open_folder_button = ttk.Button(
            action_frame,
            text="Open Output Folder",
            command=self.open_output_folder,
            state="disabled",
        )
        self.open_folder_button.pack(side="left", padx=(8, 0))

    def browse_pbip_folder(self):
        selected_folder = filedialog.askdirectory(
            title="Select the folder containing the .Report and .SemanticModel folders"
        )
        if not selected_folder:
            return
        selected_path = Path(selected_folder)
        self.pbip_folder_var.set(str(selected_path))
        self.output_file_var.set(str(selected_path / DEFAULT_OUTPUT_FILENAME))
        self.status_var.set(
            "PBIP project selected. Click Generate AI-Ready Measure Inventory."
        )

    def browse_output_file(self):
        current_output = self.output_file_var.get().strip()
        initial_directory = None
        initial_filename = DEFAULT_OUTPUT_FILENAME
        if current_output:
            current_path = Path(current_output)
            initial_directory = str(current_path.parent)
            initial_filename = current_path.name

        selected_file = filedialog.asksaveasfilename(
            title="Save measure inventory",
            defaultextension=".xlsx",
            filetypes=[("Excel Workbook", "*.xlsx")],
            initialdir=initial_directory,
            initialfile=initial_filename,
        )
        if selected_file:
            self.output_file_var.set(selected_file)

    def update_progress(self, message, current_value=None, total_value=None):
        self.status_var.set(message)
        if current_value is not None and total_value:
            self.progress_bar["value"] = current_value / total_value * 100
        self.root.update_idletasks()

    def set_processing_state(self, processing):
        self.generate_button.configure(state="disabled" if processing else "normal")
        if processing:
            self.open_file_button.configure(state="disabled")
            self.open_folder_button.configure(state="disabled")

    def generate_inventory(self):
        pbip_text = self.pbip_folder_var.get().strip()
        output_text = self.output_file_var.get().strip()
        if not pbip_text:
            messagebox.showwarning(APP_NAME, "Select a PBIP project folder first.")
            return
        if not output_text:
            messagebox.showwarning(APP_NAME, "Select an Excel output file first.")
            return

        pbip_folder = Path(pbip_text)
        output_file = Path(output_text)
        if output_file.suffix.casefold() != ".xlsx":
            output_file = output_file.with_suffix(".xlsx")
            self.output_file_var.set(str(output_file))

        self.set_processing_state(True)
        self.progress_bar["value"] = 0

        try:
            self.update_progress("Checking the selected PBIP project...")
            report_folder, semantic_folder = find_project_folders(pbip_folder)

            self.update_progress("Reading semantic-model TMDL files...")
            all_measures = extract_all_measures(semantic_folder)
            if not all_measures:
                raise ValueError(
                    "No DAX measures were found in the semantic-model TMDL files."
                )

            exact_lookup, name_lookup = create_measure_lookups(all_measures)
            self.update_progress(
                f"Found {len(all_measures)} measures. Scanning report usage..."
            )
            matched_records, unmatched_count, page_count, visual_count = (
                extract_visual_measure_usage(
                    report_folder=report_folder,
                    exact_lookup=exact_lookup,
                    name_lookup=name_lookup,
                    progress_callback=self.update_progress,
                )
            )

            inventory_records = build_measure_inventory(
                all_measures, matched_records
            )
            excluded_count = len(all_measures) - len(inventory_records)
            unused_measure_count = sum(
                1
                for record in inventory_records
                if record["Direct Visual Usage Count"] == 0
            )

            self.update_progress("Creating the single-sheet Excel workbook...")
            export_to_excel(output_file, inventory_records)

            self.progress_bar["value"] = 100
            completion_message = (
                "Inventory completed successfully.\n\n"
                f"Measures found in TMDL: {len(all_measures)}\n"
                f"Measures included in target output: {len(inventory_records)}\n"
                f"Technical/helper measures excluded: {excluded_count}\n"
                f"Pages scanned: {page_count}\n"
                f"Visuals scanned: {visual_count}\n"
                f"Measures without direct visual usage: {unused_measure_count}\n"
                f"Unmatched report references detected: {unmatched_count}\n\n"
                f"Output:\n{output_file}"
            )
            self.status_var.set(completion_message)
            self.open_file_button.configure(state="normal")
            self.open_folder_button.configure(state="normal")
            messagebox.showinfo(APP_NAME, completion_message)

        except PermissionError:
            self.progress_bar["value"] = 0
            error_message = (
                "The Excel output file could not be written.\n\n"
                "Close the workbook if it is open, then try again."
            )
            self.status_var.set(error_message)
            messagebox.showerror(APP_NAME, error_message)
        except Exception as exc:
            self.progress_bar["value"] = 0
            error_message = f"The inventory could not be generated.\n\n{exc}"
            self.status_var.set(error_message)
            messagebox.showerror(APP_NAME, error_message)
        finally:
            self.set_processing_state(False)

    def open_output_file(self):
        output_text = self.output_file_var.get().strip()
        if output_text:
            try:
                open_file_or_folder(Path(output_text))
            except Exception as exc:
                messagebox.showerror(APP_NAME, str(exc))

    def open_output_folder(self):
        output_text = self.output_file_var.get().strip()
        if output_text:
            try:
                open_file_or_folder(Path(output_text).parent)
            except Exception as exc:
                messagebox.showerror(APP_NAME, str(exc))


def main():
    root = tk.Tk()
    PBIPMeasureInventoryApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
