# BI Dev Assistant

A small desktop app that scans a Power BI Project (PBIP) folder against fifteen
generic governance conventions, shows you exactly what's wrong and where, and
can auto-fix whatever's safely fixable — with an automatic backup before it
touches anything. Before each scan, a checklist lets you pick which checks to
actually run. A **Dashboard family** dropdown can additionally bring in rules
specific to one named dashboard's own data model, layered on top of the
generic ones - see "Dashboard family rules" below.

A second tab, **Performance test**, measures how fast each page of the
*published* report responds in the Power BI service - see "Performance test
tab" below.

## The rules

1. **Slicers** — the "Header icons" section (Format pane → General → Header
   icons) must be **disabled** on every slicer.
2. **All other visuals** — header icons must be **enabled**, but with
   **only the Focus mode icon on**. Every other icon (Options `...`, Filter,
   Pin, Drill, See data) is turned off. Power BI has no native "show icons
   only while in focus mode" toggle, so this is the closest real
   equivalent: a clean header that still lets people expand a visual.
3. **Sorting** — any visual that already has an explicit, author-set sort
   configured must sort **Ascending**. The tool only flips the direction of
   an *existing* sort (same field); it never invents a sort where none
   exists. It also ignores Power BI's own auto-generated default sort
   (`isDefaultSort: true` in the query definition) - verified against a real
   1200+ visual project, every Card visual (and others) carries a harmless
   "sort by the first measure, Descending" artifact with no user-facing sort
   control at all; flagging that would be a false positive, so only sorts a
   report author actually configured are checked.
   Slicers are skipped here; rule 12 owns slicer sorting, so a descending
   slicer isn't flagged twice.
4. **Color palette** — visual/data colors must come from the approved WTW
   brand sequence (from `WTW_Data_Visualization_Color_Catalog.xlsx`'s
   "Power BI Sequence" sheet — shown as swatches right in the app so you can
   see the reference at a glance). This checks two things:
   - The report's **custom theme file**'s `dataColors` array must match the
     approved 10-color sequence exactly, in order. There's exactly one
     correct value, so in principle this could be a safe auto-fix - but
     Power BI shares this one palette report-wide: any shape, icon, or
     button whose fill was set from the "Theme colors" swatch (not just
     chart data points) resolves against the exact same array, so
     overwriting it can silently recolor those too as a side effect,
     confirmed against a real project (an "Info" navigation button and
     hundreds of action-button/shape tiles all reference theme colors this
     way). There's no reliably-confirmed way yet to tell which theme slot a
     shape "means" versus a chart, so **whenever the scan finds any
     shape/icon/button that also references the theme, this finding is
     downgraded to "Not auto-selected"** (still fixable if you check the
     box yourself, with a note in its detail naming how many objects and a
     few examples) - only when nothing else in the report shares the
     palette does it stay auto-selected. If you do apply it and notice
     something you didn't intend to change, your original theme file is
     sitting untouched in the backup folder - copy it back.
   - Any **hardcoded color** on a visual's own data properties (or baked
     into the theme's per-visual-type defaults) that isn't in the approved
     palette gets flagged too, shown with an arrow to the **nearest approved
     match** by color distance. These start **unchecked** ("Not
     auto-selected" badge) since there's no way to know the *intended*
     color automatically — but the checkbox isn't disabled: look at the
     current → nearest-match swatches, and if the snap looks right, check
     the box yourself to include it in "Fix Selected". "select all" /
     "select all pages" never sweeps these in automatically; it's always a
     deliberate per-item choice. This deliberately only looks at genuine
     data-color properties (`dataPoint`, `colors`, `plotArea`, `dataBars`,
     `tree`, `ribbonChart`) - text, labels, axes, legends, titles, gridlines
     and backgrounds are never touched or flagged.

   **Why most visuals aren't listed individually:** a visual only shows up
   here if it has a *hardcoded* color override. Most charts (pie, donut,
   treemap, etc.) don't set their own colors at all - they cycle through the
   report theme's `dataColors` sequence automatically, so if that sequence
   is wrong, every one of those visuals looks wrong on screen even though
   none of them are individually flagged. Fixing the one theme finding
   recolors all of them at once; the app says this explicitly above the
   findings list.
5. **Export** — the report's `Export data` setting (report.json's
   `settings.exportDataMode`) must not permit exporting the full underlying
   data. Fully disabled (`None`) or restricted to summarized data only
   (`AllowSummarized`) both count as compliant; anything else — including
   the setting being absent entirely, which Power BI treats as unrestricted
   — is flagged. The auto-fix always sets it to `None`, the one
   unambiguous, always-safe value; if your convention is "summarized data
   is fine", change `export_compliant_values` in
   `config/rule_config.json` so the scan stops flagging that state (the fix
   still defaults to the strictest option). Microsoft doesn't publish an
   authoritative list of `exportDataMode` values, so this is deliberately
   editable if a project uses a different exact string.
6. **Filter pane** — every filter card that shows up in the Filters pane
   must be both **locked** and **hidden** from viewers, in **all three**
   scopes the pane shows: report-level ("Filters on all pages"),
   page-level ("Filters on this page"), and per-visual ("Filters on this
   visual"). This applies whether or not the card currently has an active
   condition picked — a field sitting at **"(All)"** is still a real,
   visible card, and real dashboards routinely lock+hide those too (a
   viewer could otherwise still expand the card and pick a restrictive
   value even though it looks inert). Checked and auto-fixable at all
   three levels: the report (`report.json`), each page (`page.json`), and
   each visual (`visual.json`). The page-level scope was added after real
   testing turned up an active, unlocked, unhidden date filter sitting in a
   page's own filter pane that the report- and visual-level checks never
   looked at; the "check every card, not just active ones" behavior was
   added after further testing found real filter cards left at "(All)"
   that were hidden but not locked — a genuine compliance gap the original
   "only check active filters" logic silently missed. Any field a visual
   is bound to, in any query role (Category, Values, Y, Rows, Columns,
   Series, Tooltips, etc.) on any visual type — not just a slicer — is a
   further special case: Power BI shows it as a card in that visual's own
   Filters pane purely from the query definition, and never writes an
   actual filterConfig entry for it until someone locks or hides it by
   hand at least once — so a field that was never manually touched has
   nothing on disk for the usual fix to patch, no matter how many times it
   runs. This tool detects that case separately and creates the missing
   entry outright, in the same minimal shape Power BI itself writes.
   Confirmed against a real project on two different fronts: two slicers
   each had a different, already-compliant filter entry (a cross-filter)
   but no entry at all for their own bound field; and separately, a donut
   chart's own Category and Y-role fields had no filterConfig entry either
   — proving this isn't slicer-specific. Either way, that field's own card
   stayed unlocked forever until this was added.
7. **Legends** — a visual's legend must not have **bold** explicitly turned
   off. The WTW theme already defaults legends to bold, so (like rule 2)
   this only catches an explicit override — if a visual doesn't set the
   property at all, it's already inheriting the correct bold default and
   isn't flagged.
8. **Data sources** — every table's data-source connection (the
   `Source = ...` Power Query M steps inside its paired `.SemanticModel`
   folder's `.tmdl` files — a table's actual data connection, not the
   report's own JSON) must resolve to a path under the approved **TCTO**
   SharePoint location (`https://wtwonline.sharepoint.com/sites/
   tctPA_RETNADataI/Projects/` by default — editable via
   `tcto_path_prefixes` in `config/rule_config.json`). A source connects to
   this by reference more often than by a literal path in the same line —
   e.g. `Source = Excel.Workbook(Web.Contents(SourceFile), null, true)`,
   where `SourceFile` is a shared parameter declared once in
   `expressions.tmdl` — so the check resolves that reference to its actual
   value before comparing. A source that resolves to somewhere else is
   flagged, showing the actual resolved path; one whose exact target can't
   be determined at all (still just a bare function call, nothing literal
   to go on) is flagged too, since it can't be confirmed compliant either.
   A **compliant** source isn't shown in the results at all — only
   something that needs a look is. Power BI's own in-memory constructed
   tables (a small manually-entered table's `Table.FromRows(...)` encoding,
   a `Row("Blank", BLANK())` placeholder, or an Auto date/time hidden
   table's `Calendar(...)`) are recognized as never being an external
   connection and are skipped entirely — there's nothing to review there.
   This is **detection-only**: there's no safe automatic fix for a data
   connection, so nothing here is ever auto-fixable — it's meant to be
   looked over by a person, same as the opt-in color findings.
9. **0.5 white visual border** — pie/donut/bar/column-family charts must show a
   **white, 0.5pt border** around each data point. In the Format pane this
   shows up under a different section label depending on the chart type
   ("Slices → Border" for pie/donut, "Columns"/"Bars" → Border for bar/
   column charts) but it's the exact same underlying setting either way.
   Scoped to the chart types that actually expose this control: pie,
   donut, bar/clustered bar, column/clustered column, 100%-stacked bar/
   column, and the line-and-stacked-column combo chart — no table, card,
   slicer, textbox, gauge, plain line chart, or custom visual has this
   setting, so those are never checked. A chart of one of those types that
   never had the border configured at all is flagged too, not silently
   skipped. This **is auto-fixable and auto-selected**: the fix always
   writes a literal white hex color (not a theme-color reference), since a
   theme-color index only means "white" in a theme whose color-role
   ordering happens to put white at that slot — a literal hex is correct
   regardless of theme.
10. **Table/matrix alignment** — a table (`tableEx`) or matrix (`pivotTable`)
    visual's cell values and column headers must be centered. Power BI's own
    default when nothing is set isn't centered at all — text columns default
    left, numbers default right — so an untouched table/matrix is a
    violation here, not a pass, unlike most other rules in this tool.
    - **Table**: checks the table-wide **Values** and **Column headers**
      Format-pane sections. A per-column **Specific column** override
      (`columnFormatting[]`, scoped to one column via a `selector`) always
      wins over that table-wide default for whichever column it targets —
      confirmed against a real table where fixing only the table-wide
      default left an overridden column visibly unchanged — so every
      value column is also checked for an *existing* override that isn't
      Center, and that override is fixed in place. A column with no
      override at all is left alone: it already correctly inherits the
      table-wide default once that's centered, so nothing new is invented
      for it.
    - **Matrix**: column headers work the same way as a Table's, but a
      Matrix has **no table-wide default for its value cells** — grounded
      against three real matrices, none of which ever had an "alignment"
      property on the general Values section. Centering a Matrix's values
      is only possible one column at a time via the Format pane's
      **Specific column** override, so this checks (and fixes) every value
      column in the visual individually. Row headers are deliberately not
      checked — none of the three real matrices had an alignment property
      there either, consistent with Power BI Desktop not exposing that
      control (row headers are always left-aligned to show the hierarchy).
    - **Excluded pages**: entire pages whose display name contains "info"
      (case-insensitive, e.g. "Info (Plan design)") are skipped outright -
      editable via `alignment_excluded_page_substrings` in
      `config/rule_config.json`. Confirmed against a real counter-example:
      that page's Matrix is a flat glossary list with no Values well at
      all, so it has no "Specific column" override to fix - only a "Row
      headers > Alignment" control this tool doesn't yet know how to write
      safely. Rather than risk guessing at that different Matrix layout,
      the whole page is excluded.
    Both are **auto-fixable and auto-selected**.
11. **'Visualize population' button** — every action button labeled
    "Visualize population", on every page, must have its bookmark action
    pointing at the "Employee demographics" bookmark (resolved by that
    bookmark's display name, not a hardcoded id, so a re-exported bookmark
    with a different internal name is still found correctly). Applies to
    every dashboard: a project with no such button produces nothing, and
    one that has the button but no "Employee demographics" bookmark at all
    gets a single scan warning instead of a guessed fix. Matched by the
    button's title, not a raw text search - a raw search would have
    produced a false positive in the DC & Benefit Affordability Dashboard
    against an unrelated "More details" button carrying leftover
    "Visualize population" text in one of its style-state variants.
    **Auto-fixable and auto-selected**: sets only the button's bookmark
    action type and target; navigation section, show/hide and styling are
    left untouched.
12. **Filter values are sorted properly** — every slicer must be sorted by
    its **own field, ascending**: in the slicer's **… > Sort by** menu, its own
    field is ticked and **Sort ascending** is ticked. A slicer with no explicit
    sort stored is already in that state, because it's Power BI's default.
    That covers 143 of 145 slicers in the DC & Benefit Affordability
    Dashboard, including the Age slicer. Flagged: an explicit sort on a field
    the slicer doesn't display (real example: a Heat map slicer showing
    'Value view type'[View] but sorted by 'Benefit type filter'[Type]), or
    any explicit Descending sort. Fix removes the explicit sort, returning
    the slicer to own field, ascending. Slicers only.
    **Fixable, but never auto-selected**: every finding starts unchecked so
    you can review it first and tick only the ones you want fixed.
13. **If 'Employee demographics' is not available, visualize population
    should be removed** — when the report has no "Employee demographics"
    page (matched case-insensitively; the real page is named "Employee
    Demographics"), every "Visualize population" button leads nowhere and is
    flagged. Fix deletes the button's file and its now-empty folder, the
    same delete fix used by the Plan design privacy-note rule, backed up
    first. Only the button is removed; its visual group and the other
    visuals on the page are left as they are. When the page does exist,
    this rule produces nothing and rule 11 checks the button's bookmark
    instead. Rule 11 skips the button entirely when the page is missing,
    so the two rules never try to fix the same button.
    **Auto-fixable and auto-selected.**
14. **Employee demographics page has a Previous page button (action type:
    Back)** — the "Employee demographics" page (matched case-insensitively)
    must have an action button titled "Previous page" whose action type is
    **Back**. If the button's type is anything else, the fix sets it to Back
    and changes nothing else. The button may still carry old bookmark or
    navigation properties, as the real one does; Power BI ignores those for
    a Back action, so they're left alone. If the page has no Previous page
    button at all, it's flagged for review only, because a new button's
    position and styling can't be safely guessed. If the page doesn't exist,
    this rule produces nothing.
    **Auto-fixable and auto-selected** (except the missing-button case).
15. **Segmentation tabs are single select only** — every slicer named
    "Segmentations Slicer" (its Selection-pane name, case-insensitive) must
    have **Selection > Single select** on. Covers both standard slicers and
    the tile-style `advancedSlicerVisual` ones. Grounded against 8 real ones
    across 7 pages: that toggle is stored as `strictSingleSelect`. This rule
    never touches the separate `singleSelect` property, which is the
    "Multi-select with CTRL" toggle the ordinary filter slicers use. Matched
    by name, so a slicer only counts once it's actually named "Segmentations
    Slicer".
    **Auto-fixable and auto-selected.**

All defaults were agreed with you up front. If your team's convention
differs (e.g. you also want the Options icon kept on, the approved palette
changes, or a different export-setting string counts as compliant), edit
`config/rule_config.json` and rebuild the .exe (see below) — running from
source (Option B) picks up the edit on next launch with no rebuild needed.

## Dashboard family rules

The 15 rules above apply to any PBIP project. A **Dashboard family** dropdown
above the checklist lets you additionally bring in rules that only make
sense for one specific, named dashboard's own data model - they show up as
extra checklist items *on top of* the generic 15, never replacing them.

Currently defined:

- **DC & Benefit Affordability Dashboard**
  1. **Headcount measure source** — `MeasureTable[Headcount]`'s active DAX
     must reference `Employee_Segments[EmployeeNumber]`, not
     `Baseline[EmployeeNumber]`. Old versions of the measure's logic that
     are kept around as `//`/`--`-commented history don't count - only
     active code is checked.
  2. **Baseline relationship cardinality** — the relationship joining
     `Baseline[EmployeeNumber]` to `Employee_Segments[EmployeeNumber]` must
     be declared 1:1 and active in `relationships.tmdl`.
  3. **Plan design: no privacy note** — the "Plan design" page must not
     have a "Data privacy note" card on it. That disclaimer belongs only on
     pages where headcount suppression actually applies; it has a habit of
     coming back (e.g. copy-pasting a page template that includes it), so
     this catches the regression. Matched by the visual's title text
     (which Power BI Desktop's Selection pane shows as its label even
     though the title itself isn't displayed on canvas) - report authors
     should keep using the exact name "Data privacy note" for this card on
     every page for the match to stay reliable. **Unlike the other two,
     this one IS auto-fixable and auto-selected**: the fix deletes the
     matching visual's file (and its now-empty folder) outright, not just a
     property patch. It's still backed up first like every other fix, but
     it's a more consequential action than the rest of the rules in this
     tool take - the match is narrow (right page + exact title text) to
     keep false positives very unlikely.
  4. **Disclaimer Banner shown by default** — the "Disclaimer Banner"
     visual group (a layout container, not a single visual - Power BI's own
     `visualGroup` object) must not be hidden by default on a page. This
     checks only the group container's own visibility, never its individual
     pieces (Close Button, Title, Message, Confidentiality Footer, WTW
     Logo, Background) - each of those stays whatever it already is. Which
     pages get checked is configurable via its own row of checkboxes right
     under this rule in the checklist (default: Benefit affordability, DC,
     Plan design - the three pages that actually have this banner today); a
     selected page with no "Disclaimer Banner" group at all, or that
     doesn't exist, shows up as a scan warning rather than a finding, since
     there's nothing safe to auto-create there. **Auto-fixable and
     auto-selected**: the fix removes the group's `isHidden` flag entirely
     (matching how every currently-shown group/visual in this project is
     encoded - absence of the key, not an explicit `false`), so the banner
     shows again the next time the page loads. Viewers can still dismiss it
     via its own Close button afterward - this only controls whether it
     shows *by default*, not permanently.

The first two are **detection-only** - rewriting DAX logic or a
relationship's structural cardinality isn't something this tool does
automatically, and selecting either always shows "review only, never
auto-fixed" in the checklist. The cardinality check also only confirms
what's *declared* in the model file; actually verifying matching row counts
on both sides of the relationship needs a live data connection in Power BI
Desktop, which a static file scan can't do.

To add a new dashboard family: add an entry to `DASHBOARD_FAMILIES` and one
or more `RULE_CHECKLIST` items (with a matching `family` value) in
`ui/index.html`, then add the matching rule id/label/check function in
`engine/rules.py` and wire it into `engine/service.py`'s `run_scan()` -
follow the `dc_benefit_*` rules as the template. Ground any new rule against
a real copy of that dashboard's actual files first, the same way these two
were - don't guess at TMDL syntax or DAX patterns from documentation alone.

## Performance test tab

The **Performance test** tab (next to **Governance scan** at the top of the
app) measures the response time of each page of a **published** report in
the Power BI service. It automates the manual check: open the page, press
F12, go to the Network tab, filter to xhr, sort by Time and read the top
`query` row. It does that for every page and puts the results in one table.
The scan tab checks the PBIP files; this tab checks the live report.

**Page load = the page's slowest visual query.** A page's queries run in
parallel, so the page has finished when its slowest query finishes. Queries
of 0.5 s, 1 s and 1.5 s mean the page loaded in 1.5 s, not 3 s. Power BI's
own start-up isn't included. On the DC & Benefit Affordability Dashboard
this gave 0.7-1.6 s per page, in line with checking it by hand.

How to use it:

1. Paste the published report link
   (`https://app.powerbi.com/groups/<workspace>/reports/<report>/<page>?experience=power-bi`).
2. Optionally choose the dashboard's PBIP folder. It's used for the page list
   when the list can't be read from the published report.
3. Choose loads per page (default 3) and click **Run performance test**.
4. A Microsoft Edge window opens with its own profile (`edge_profile\` next
   to the app). Your normal Edge isn't touched. Sign in to Power BI there the
   first time; it stays signed in afterwards. Leave the window alone while it
   runs; the app closes it at the end.

What happens and what you get:

- The report is opened once as a warm-up (not counted). Then every page,
  hidden ones included, is opened by its link, round-robin, for the number of
  loads you chose.
- A request counts as a visual query when it's xhr/fetch and its address ends
  in `/query` or `/querydata`. Preflight (`OPTIONS`) rows and cancelled
  queries are skipped; background-worker requests are included, as DevTools
  shows them.
- A page is done once no query has started or finished for 5 seconds. Any
  page is stopped after 3 minutes and marked `timeout`.
- The table shows one row per page: **Page load (avg)**, **Page load (1st
  run)** (so caching can't hide a slow first open), **Queries** and
  **Status**. Every load's time is listed under the page name. There are no
  pass/fail thresholds. Click a heading to sort.
- Every test is saved as a CSV in `perf_results\` next to the app.
  **Export CSV...** saves a copy anywhere. Both open in Excel.

Things to know:

- **Redirected pages:** if Power BI shows a different page than the one
  asked for (possible for hidden or drillthrough pages), the row is marked
  `redirected` rather than reporting the wrong page's time.
- **Default view only:** each page is measured as it first opens. Bookmark
  pop-ups aren't clicked, and drillthrough pages open without a selection.
- **"DevTools connection never became available":** usually an Edge window
  from an earlier test is still open on the tester's profile. The app reuses
  or closes it automatically; if the message still appears, close every Edge
  window the app opened (or end "Microsoft Edge" in Task Manager) and retry.
  Only if it persists could an organisation policy be blocking it (see
  `edge://policy`, `RemoteDebuggingAllowed`).
- **Size:** no browser-automation library is bundled. The app drives the Edge
  that's already installed, using a small built-in DevTools connection
  (`perftest/`), so it adds almost nothing to the .exe.
- **Tests:** `tests/perf/` contains a simulated Edge + Power BI report and an
  end-to-end test (`python tests/perf/test_runner.py`).

## Getting the app

You have two options:

### Option A - packaged .exe (recommended, no Python needed to *run* it)

Building it still needs Python once, on any machine with this folder:

1. Double-click **`build_exe.bat`**. First run installs PyInstaller and
   builds the app (a minute or two). It builds to `C:\pbip_build\dist\`
   rather than this folder - PyInstaller bundles some deeply-nested .NET
   runtime files, and if this folder's own path is already long (common
   with cloud-synced or deeply nested folders), the combined path can
   exceed Windows' 260-character limit and fail partway through with a
   confusing `FileNotFoundError`. Building to `C:\` sidesteps that.
2. Copy the resulting **`BI Dev Assistant.exe`** - just that one
   file - anywhere you like (Desktop, a shared drive, another machine,
   etc.) and double-click it. No Python install needed, no other files or
   folders required alongside it.
3. It self-extracts to a temp folder on every launch (that's how a
   single-file PyInstaller build works), so it opens a little slower than a
   folder-based build would - a few seconds, not more. `app.log` and the
   `backups\` folder are still created right next to wherever you put the
   `.exe`, and persist between runs as normal.
4. No console/terminal window opens - just the app itself. If the window
   ever doesn't appear, `app.log` next to the `.exe` is the only place
   startup errors get recorded, since there's no console to show them in.
5. Because everything is baked into the one file, `config/rule_config.json`
   is no longer separately editable after building - to change the icon
   list or approved palette, edit the file in this project folder and run
   `build_exe.bat` again.

Rebuild any time you change the Python code or `config/rule_config.json`
(`build_exe.bat` again).

### Option B - run from source

1. Install Python 3.10+ if you don't have it (`python --version` in a
   terminal to check).
2. Double-click **`run_windows.bat`**. The first run installs the two
   required packages for your user account (needs internet access once);
   every run after that is instant.

If you'd rather do it manually:

```
py -3 -m pip install --user -r requirements.txt
py -3 main.py
```

Either way, the app is a native window (built on the Edge WebView2 runtime,
which ships with Windows 11) — no browser tab, nothing to host.

> **Note:** `run_windows.bat` deliberately does *not* create a Python
> virtual environment. On some locked-down / corporate Windows machines,
> creating a fresh venv triggers Python's `ensurepip` step to reinstall
> `pip` from scratch, and that step can get corrupted by antivirus or
> file-sync tools mid-write (you'll see an error like `No module named
> 'pip._vendor.urllib3.packages'`). Installing straight into your existing
> user Python sidesteps that entirely.

## How to use it

1. Click **Choose PBIP Folder…** and pick the folder that contains your
   `.pbip` file (or a parent folder if you keep several PBIP projects
   together — the scanner finds every `*.Report` folder underneath).
2. Under **Checks to run**, untick anything you don't want this scan to
   look at (e.g. just want to check colors this time? untick the rest). All
   10 generic rules are on by default. If this PBIP is one of the named
   dashboards with its own extra rules, pick it from the **Dashboard
   family** dropdown first - its rules get added to the checklist,
   pre-checked, on top of the generic ones.
3. Click **Scan**. You'll get a summary: reports/pages/visuals scanned, and
   a count of findings for each rule you selected.
4. A **Pages** panel lists every page (plus a `(Report Theme)` entry for
   theme-level color findings) with a checkbox and a count. Uncheck any
   page you want to leave alone for now — its findings are excluded from
   "Fix Selected" but stay visible below so you can still review them.
   Click a page name to jump straight to just its findings.
   "select all pages" / "select no pages" bulk-toggle everything at once.
5. Expand a rule to see exactly which page and visual each finding is on,
   with plain-English detail (color findings show swatches, not just hex
   codes). Everything auto-selected is pre-checked; untick anything you
   don't want touched. Findings marked **"Not auto-selected"** start
   unchecked but are still fixable — check the box yourself once you've
   eyeballed it (e.g. a hardcoded color's nearest-match swatch). Each
   expanded rule also has its own **"select all in this rule"** /
   **"select none in this rule"** links — handy if you already scanned and
   now only want to fix one or two specific rules without unchecking
   everything else by hand or rescanning.
6. Click **Fix Selected**. The app backs up every file it's about to change
   into a `backups\<project name>_<timestamp>\` folder *inside the app's own
   folder* — never inside your PBIP project — then overwrites the originals
   in place. It re-scans automatically afterwards so you can confirm the
   remaining count.
7. Open the project in Power BI Desktop as usual and check it looks right.
   If anything's off, your originals are sitting untouched in that backups
   folder — just copy them back.

Rule 8 is the one exception to "everything lives in the .Report folder": it
looks in the *sibling* `.SemanticModel` folder (same project, same base
name) that PBIP always generates next to the report, and reads its `.tmdl`
files as plain text (TMDL isn't JSON). If that folder isn't found next to a
report, a warning explains why rule 8 found nothing for it rather than
staying silent.

## Formats supported

- **PBIR (enhanced report format)** — the modern PBIP layout where each
  visual is its own `visual.json` file. This is the primary, well-tested
  path.
- **Legacy PBIP** (a single `report.json` per report, visuals nested as
  stringified JSON inside it) — supported on a best-effort basis. The app
  will flag in a warning banner if a report is in this format; double-check
  those fixes before trusting them, since this older format is less
  consistently documented.

## A note on accuracy

The property names for header icons and sort direction were verified
directly against a real `visual.json` written by Power BI Desktop
(visualContainer schema 2.12.0) — not just documentation. Two important,
confirmed behaviors: Power BI never writes `"show": true` or any property
for "Focus mode" at all, since both are already the default and PBI only
persists settings that differ from default — so this tool doesn't write
them either, it only explicitly disables the *other* icons. Visual
*groups* (layout containers, distinct from real visuals) are detected and
skipped entirely, since they have no header icons of their own.

`isLockedInViewMode`/`isHiddenInViewMode` (filter pane) and `legend[].
properties.bold` were likewise confirmed directly against real project
JSON. `settings.exportDataMode`'s exact set of valid values is the one
property here Microsoft doesn't document publicly — see rule 5 above for
how that's handled conservatively and made configurable.

Rule 9's `objects.dataPoint[].properties.borderShow`/`borderColor`/
`borderSize` were confirmed the same way — including a live before/after
test where a real donut chart's border width was deliberately changed in
Power BI Desktop and re-scanned, which is also what confirmed the setting
is *not* nested under a `"slices"`/`"columns"`/`"bars"` key (those are only
the Format pane's section labels) and is the same `dataPoint` object
regardless of chart type.

Still, schema details can vary across Power BI Desktop versions — **run it
on a copy or a test branch first**, confirm the diffs look right, and keep
the auto-generated backups until you're confident.

## Project layout

```
main.py                 - app entry point (pywebview window + JS bridge)
engine/
  appdirs.py             - resolves persistent app dir (backups/log) vs. bundled resource dir (ui/config, sys._MEIPASS in the onefile build)
  scanner.py             - discovers *.Report folders, pages, visuals (skips visual groups)
  rules.py                - the rule checks (reads config/rule_config.json)
  fixer.py                - applies selected fixes, file by file (+ theme file)
  backup.py               - snapshots files before they're overwritten (into backups/, next to the app)
  jsonutil.py             - reads/writes PBI's boolean property encoding
  models.py               - Finding / ScanResult data structures
  service.py              - ties scan + fix together for the UI, threads the rule checklist through
perftest/                - Performance test tab: runner.py (warm-up, page list, loads, timing, CSV),
                           pages.py (link parsing, page list), edge.py (launch/reuse/close the test Edge),
                           cdp.py + ws.py (DevTools connection, standard library only)
tests/perf/              - simulated Edge + Power BI report and end-to-end test for the tab
ui/index.html            - the whole front-end (single file, no CDN deps); both tabs
config/rule_config.json  - editable icon list, approved palette, color object allowlist
requirements.txt
run_windows.bat          - run from source
build_exe.bat            - build a standalone .exe with PyInstaller
```
