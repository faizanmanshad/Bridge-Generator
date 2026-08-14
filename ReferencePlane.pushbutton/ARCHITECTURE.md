# ARCHITECTURE.md — Reference Plane.pushbutton

## 1. Environment
- Revit 2024.3, pyRevit 6.4.0, Windows, metric project (mm).
- Python engine: **do not assume** — inspect the existing bundle's `bundle.yaml` / `script.py` / `config.json` engine declaration and other pushbuttons in the same panel/extension first, and match that convention (IronPython 2.7 vs CPython3 vs pyRevit's `EXEC_PARAMS`). Only fall back to a new choice if the tool truly has none yet, and document the choice.
- Prefer `Autodesk.Revit.DB`, pyRevit framework utilities (`pyrevit.forms`, `pyrevit.script`), and .NET classes already available in-process. Avoid new third-party pip packages.

## 2. Confirmed Bundle Location
```
...\Urbana.extension\Urbana.tab\BridgeGenerator.panel\ReferencePlane.pushbutton\
```
Typical pyRevit bundle contents to inspect before editing:
```
ReferencePlane.pushbutton\
  bundle.yaml
  icon.png
  script.py            (main entry — extend, don't replace, unless empty/stub)
  ui/ (if XAML already used)          e.g. ReferencePlaneUI.xaml
  lib/ or shared modules under the extension root (e.g. ...\Urbana.extension\lib\)
```

## 3. Recommended Module Layout
Keep it inside the existing pushbutton folder (or the extension's shared `lib/` if that convention already exists there — inspect first). Do not fragment into dozens of files; group by responsibility:

```
ReferencePlane.pushbutton/
  script.py                    # pyRevit entry point — thin: builds UI, wires button click → managers
  ui/
    BridgeSetupWindow.xaml     # two-tab WPF window (Global Parameters / Reference Planes)
    BridgeSetupWindow.py       # code-behind / view-model, event handlers only — no geometry logic here
  core/
    param_spec.py              # SINGLE SOURCE OF TRUTH: the 25-parameter definition table
                                #   (name, group, formula, value, dependency order)
    units.py                   # mm_to_internal(), internal_to_mm() — centralized conversion helpers
    global_param_manager.py    # ensure/create/update/validate Global Parameters, idempotent
    refplane_manager.py        # ensure/create/update reference planes, idempotent, deterministic naming
    dimension_manager.py       # driving dimensions, EQ constraints, GP↔dimension association
    origin_provider.py         # resolves bridge center from Project Base Point / Survey Point /
                                #   Internal Origin per DESIGN.md decision procedure
    validation.py               # pre-flight checks: active view, required GPs present, formula results
    logging_utils.py            # thin wrapper over pyrevit logger / output console
  bundle.yaml
  icon.png
```

Rationale: this mirrors the spec's required separation (§38 of the master prompt) — UI, parameter definitions, unit conversion, GP manager, reference-plane manager, dimension/constraint manager, validation, logging — without over-fragmenting.

## 4. Data Flow
```
script.py (button click)
   → BridgeSetupWindow (WPF, 2 tabs)
        Tab 1 click "Create/Update Global Parameters"
           → global_param_manager.ensure_all(param_spec.DEFINITIONS)
              → units.mm_to_internal() for each Length value
              → idempotent create-or-reuse-or-report-conflict per parameter
              → apply formulas in dependency order from param_spec
              → status list returned to UI (Missing/Existing/Created/Updated/Formula Applied/Error)

        Tab 2 click "Create/Update Reference Planes"
           → validation.required_global_parameters_present([...6 names...])
           → validation.active_view_is_supported(doc.ActiveView)
           → origin_provider.get_bridge_center(doc)   # Project Base Point / Survey Point / Internal Origin
           → refplane_manager.ensure_horizontal_set(...)   # 9 planes, symmetric around center
           → refplane_manager.ensure_vertical_set(...)     # 7 planes, symmetric around center
           → dimension_manager.ensure_width_dimensions(...)   # overall + EQ chains, GP-associated
           → dimension_manager.ensure_length_dimensions(...)  # overall + EQ + crank chains, GP-associated
           → status list returned to UI
```

## 5. Transaction Strategy
Use a `TransactionGroup` wrapping the whole Tab-2 operation (and, separately, the whole Tab-1 operation), with named sub-`Transaction`s for logical stages (Global Parameters; Reference Planes; Dimensions/Constraints). On any unrecoverable error mid-stage, roll back the active transaction/group rather than committing a half-built skeleton. Call `doc.Regenerate()` only at points where subsequent API calls require newly created references to exist (e.g., after creating reference planes and before creating dimensions that reference them) — not after every statement.

## 6. Idempotency Strategy (applies to both managers)
- **Global Parameters**: look up by exact name via `GlobalParametersManager`/`GlobalParameter.FindByName`-equivalent API on the document; if found and type-compatible, reuse and update value/formula; if found but incompatible, do not touch — report conflict; if not found, create.
- **Reference Planes**: identify "ours" deterministically, not by ElementId. Recommended: a dedicated shared parameter or the plane's `Name` property (Revit reference planes support a `Name`) set to the deterministic names in DESIGN.md, looked up via `FilteredElementCollector(doc).OfClass(ReferencePlane)` filtered by name. Never touch reference planes that don't match these exact names.
- **Dimensions**: since dimensions have no persistent "name" by default, identify automation-owned dimensions via a marker (e.g., a shared parameter/comment tag set on creation, or by recomputing the expected reference pair and checking whether a dimension already spans that exact pair) before creating a new one.

## 7. Origin Resolution (`origin_provider.py`)
Order of preference, first that resolves successfully and matches the existing bridge-generator/Dynamo convention wins:
1. If existing Dynamo graphs/manually-built bridge files already reference a specific one of {Project Base Point, Survey Point, Internal Origin} for bridge center — use that same one (inspect `BasePoint` elements in the model, `BasePoint.GetProjectBasePoint(doc)` / `BasePoint.GetSurveyPoint(doc)`).
2. Otherwise default to **Project Base Point** (`BasePoint.GetProjectBasePoint(doc).Position`) as the most common Revit convention for structure-level origin, falling back to **Internal Origin** `(0,0,0)` only if the Project Base Point is not usable (e.g., not placed / shared-coordinates issue) in the active document.
3. Document whichever is chosen, in code comments and in the completion report — this must not silently vary between runs.

## 8. UI Framework
Inspect the existing pushbutton first. If it already uses WPF/XAML or `pyrevit.forms`, extend that. If nothing exists, build a lightweight WPF window (XAML) with two `TabItem`s, per DESIGN.md, keeping all Revit-document logic out of the code-behind (call into `core/` managers only).

## 9. Error Handling & Logging
- User-facing errors are specific and actionable (see RULES.md §Error Messaging).
- `logging_utils.py` logs milestones (start, N existing, N created, formula applied, skeleton created, validation passed) at INFO; avoid per-coordinate spam once stable.
- No bare `except: pass` — always log and surface.

## 10. Dependencies on Existing Project Assets
Before writing code, inspect: existing Global Parameters already in the model, existing reference planes/dimensions, existing Dynamo graphs (for naming/origin conventions), any shared `lib/` modules at the extension level, and Git history/status if the extension is repo-backed. Reuse conventions found there over anything in this document.
