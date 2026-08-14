# PHASES.md — Implementation Sequence & Test Plan

## Phase A — Discovery (do this before writing any code)
- [ ] Open `...\Urbana.extension\Urbana.tab\BridgeGenerator.panel\ReferencePlane.pushbutton\` and inventory: `bundle.yaml`, `script.py`, any XAML/UI, imported shared/lib modules, icon.
- [ ] Confirm this is the live/loaded bundle (vs. the older `BridgeGenerator.extension` path referenced in older docs) — check pyRevit extension load state or config if possible.
- [ ] Identify pyRevit engine convention already in use (IronPython/CPython3/`EXEC_PARAMS`) from this and sibling pushbuttons.
- [ ] Search the model / existing Dynamo graphs / other scripts for: existing Global Parameters (any of the 25 names already present?), existing reference planes with meaningful names, existing origin convention (Project Base Point vs Survey Point vs Internal Origin usage), existing reference-plane naming style.
- [ ] Check Git status/history if repo-backed, to understand recent related work.
- [ ] Record findings — these override any default choice in ARCHITECTURE.md/DESIGN.md.

## Phase B — Design Confirmation
- [ ] Finalize file list to add/modify (per ARCHITECTURE.md, adjusted for whatever Phase A found).
- [ ] Finalize `param_spec.py` structure (name, group, spec/type, value, formula, dependency rank) covering all 25 parameters.
- [ ] Finalize reference-plane naming map (DESIGN.md defaults unless an existing convention was found in Phase A).
- [ ] Finalize origin resolution order (ARCHITECTURE.md §7), confirming with Phase A findings.
- [ ] Finalize idempotency identification strategy for planes and dimensions (name-based vs. marker-based).
- [ ] Finalize transaction/rollback boundaries.

## Phase C — Global Parameters (Tab 1)
- [ ] Implement `param_spec.py` with all 25 definitions, ordered so base/independent parameters precede formula parameters:
  - Base first: `GP_Framing_Height`, `Clear Span`, `Length`, `Camber`, `Crank Length`, `Abutment Width`, `Bearers Width`, `Connection Plate Thickness`, `Bearer Connection Width`, `Beam Web`, `Vertical_Joist_Height`, `UB_Height`, `Horizontal_Joist_Height`, `Beam Centerline`, `Horizontal_Joist_Width`, `Bearer Width`.
  - Then formula-dependent: `GP_Horizontal_Joist_Height`, `GP_Vertical_Joists_Height`, `GP_UB_Height`, `Bearers Length`, `Vertical Joist Bounding Distance`, `GP_End_Offset_H_V_joist`, `GP_End_Offset_U_Beam`, `GP_Start_Offset_U_Beam`, `GP_Start_Offset_H_V_Joist`.
- [ ] Implement `global_param_manager.ensure_all()`: idempotent create-or-reuse-or-report-conflict, value + formula application in dependency order, Length-type parameters via correct 2024.3 API, correct group assignment (Construction/Dimensions/Other) where API allows.
- [ ] Wire Tab 1 UI: "Create / Update Global Parameters" button + status list (Missing/Existing/Created/Updated/Formula Applied/Error).
- [ ] **Validate formula results** against expected values (tolerance ~1e-6 or a sensible floating-point epsilon):
  - GP_Horizontal_Joist_Height = 80.0 mm
  - GP_Vertical_Joists_Height = 225.0 mm
  - GP_UB_Height = 482.6 mm
  - Bearers Length = 2314.4 mm
  - Vertical Joist Bounding Distance = 2144.0 mm
  - GP_End_Offset_H_V_joist = 5.0 mm
  - GP_End_Offset_U_Beam = -40.0 mm
  - GP_Start_Offset_U_Beam = -80.0 mm
  - GP_Start_Offset_H_V_Joist = -35.0 mm

## Phase D — Reference Planes (Tab 2)
- [ ] Implement `validation.required_global_parameters_present()` checking: Clear Span, Length, Crank Length, Abutment Width, Bearers Length, Vertical Joist Bounding Distance.
- [ ] Implement `validation.active_view_is_supported()` (appropriate plan-type view only).
- [ ] Implement `origin_provider.get_bridge_center()` per ARCHITECTURE.md §7 (Project Base Point → Survey Point → Internal Origin fallback order, or whichever existing convention Phase A found).
- [ ] Implement `refplane_manager.ensure_horizontal_set()`: 9 planes (center + 4 symmetric pairs) positioned from live GP values, deterministic names, ~24000 mm visible extent.
- [ ] Implement `refplane_manager.ensure_vertical_set()`: 7 planes (Left End, Left Intermediate, Left Crank, Center, Right Crank, Right Intermediate, Right End), ~10000 mm visible extent.
- [ ] Wire Tab 2 UI: required-parameter validation display + "Create / Update Reference Planes" button + status list.

## Phase E — Dimensions & Constraints
- [ ] Width system, per symmetric pair (Vertical Joist Bounding Distance, Bearers Length, Clear Span, Abutment Width):
  - Overall dimension outer-to-outer, associated with the driving Global Parameter.
  - Center-chained dimension (side → center → side) with both segments constrained EQ.
- [ ] Length system:
  - Overall Left End–Right End dimension associated with `Length`.
  - Center-chained EQ dimension (Left End → Center → Right End).
  - Full 6-segment chain (Left End → Left Intermediate → Left Crank → Center → Right Crank → Right Intermediate → Right End) with: both crank segments = `Crank Length`; left two outer segments EQ to each other; right two outer segments EQ to each other.
- [ ] Associate dimensions with Global Parameters via the verified native API wherever supported; document any fallback.
- [ ] Implement idempotent detection so re-running doesn't stack duplicate dimensions/EQ/crank constraints.

## Phase F — Validation & Testing (mandatory, run all)
1. **Clean run** — empty-ish model: both tabs produce exactly the specified 25 parameters, 9 + 7 planes, and correct dimension set.
2. **Re-run test** — click both tabs' buttons a second time: parameter count stays 25 (no renamed duplicates), no duplicate reference planes, no duplicate dimension/EQ/crank chains.
3. **Failure test** — delete/rename one required Global Parameter, run Tab 2: clear error naming the missing parameter, no partial skeleton committed.
4. **Dynamic test — Clear Span**: change value; confirm clear-span planes move, center stays fixed, Bearers Length & Vertical Joist Bounding Distance recalc, their plane pairs update automatically.
5. **Dynamic test — Abutment Width**: change value; confirm only the abutment pair moves, center fixed, other dimensions remain valid.
6. **Dynamic test — Length**: change value; confirm ends move symmetrically, center fixed, intermediate EQ planes rebalance, crank planes stay governed by Crank Length.
7. **Dynamic test — Crank Length**: change value; confirm crank planes move symmetrically, center fixed, ends stay governed by Length, intermediates rebalance.
8. **Max-length test**: set Length ≈ 20000 mm; confirm horizontal reference planes (24000 mm extent) still show visible margin beyond bridge ends.

## Phase G — Report (produce at end of task)
Deliver per PRD.md §Definition of Done:
- A. Exact full paths of every file created/modified.
- B. Summary of what each changed file does.
- C. Confirmation of all 25 parameter names/formulas/expected initial values.
- D. Confirmation of 9 horizontal + 7 vertical reference planes.
- E. Which dimensions were created, which use EQ, which are GP-associated.
- F. How rerunning avoids duplicates (idempotency mechanism used).
- G. Manual test steps to verify the button in Revit 2024.3.
- H. Any Revit API limitations hit and the fallback used.
- I. Confirmation that no unrelated files/pushbuttons were touched.
