# RULES.md — Hard Constraints for the Coding Agent

These rules override convenience, style preference, and "cleaner code" instincts. If a rule conflicts with a stylistic improvement, the rule wins.

## Scope Discipline
1. Work only inside `...\Urbana.extension\Urbana.tab\BridgeGenerator.panel\ReferencePlane.pushbutton\` (plus shared extension-level `lib/` if that's the established convention). Do not touch other pushbuttons, panels, or tabs.
2. Do not create a second/duplicate bridge-generator extension. Do not rename or restructure the extension tree because older docs used different folder names — the filesystem is authoritative.
3. Do not implement anything from the explicit out-of-scope list (beams, bearers, joists, decks, connections, bracing, handrails, sheets, unrelated annotations, Dynamo bridge-component generation). This task ends at: Global Parameters + Reference Planes + Dimensions + Constraints.
4. Do not begin the next bridge-generation stage under any pretext.

## Parameter Naming & Values
5. The 25 Global Parameter names are exact and case-sensitive. Do not normalize capitalization, replace spaces with underscores, or change singular/plural. `GP_UB_Height` ≠ `gp_ub_height` ≠ `GP UB Height`.
6. Preserve formulas **exactly as specified**, including ones that look mathematically redundant (e.g. the `if(...)` formulas with identical true/false branches). Do not "simplify" them. Any cleanup is an explicitly separate future task.
7. All given values (482.6 mm, 2500.0 mm, etc.) are initial/reference values for reproducing a known bridge, not universal constants — treat them as the default data in `param_spec.py`, not hard-coded literals scattered through logic.

## Units
8. Never pass raw millimetre values into Revit API properties expecting internal units. Always go through the centralized `mm_to_internal()` / `internal_to_mm()` helpers. No scattered conversion constants.
9. Create parameters using the Length-type Global Parameter API — not text, integer, or dimensionless number.

## Idempotency (non-negotiable)
10. Running Tab 1 twice must not create `Clear Span 1`, `Clear Span 2`, or any renamed duplicate. Existing compatible parameters are reused/updated in place.
11. Running Tab 2 twice must not create duplicate reference planes or duplicate dimension/EQ/crank constraints. Automation-owned elements must be identified deterministically (by name or marker), never by remembered ElementId or creation order.
12. If a parameter/element with the expected name exists but is incompatible with what the automation expects, do not delete or silently replace it — report the conflict clearly and stop that item (don't abort the whole run unless it blocks required dependencies).

## Non-Destructive Behavior
13. Never bulk-delete reference planes, dimensions, or Global Parameters ("delete all X and recreate"). Only ever touch elements confidently identified as belonging to this automation by exact name/marker.
14. Never delete a user's unrelated manually-created geometry, even if its position looks "wrong" or unexpected.
15. Do not silently switch the active view. If the active view can't support the operation, abort with a clear message and make no partial changes.

## Parametric Integrity
16. Static coordinate math alone (compute mm, place plane, done) is NOT sufficient and does not satisfy this task. The required chain is: Global Parameter → Driving Dimension → Reference Plane → (future) Bridge Component. Use native Revit Global-Parameter-to-Dimension association wherever the Revit 2024.3 API supports it; do not fake it by only recalculating coordinates unless a verified API limitation forces that fallback — and if so, document the limitation explicitly in the completion report.
17. Do not hard-code derived offsets (±1750, ±1250, -6000, +4000, etc.) as permanent geometry constants. They must be computed from the live Global Parameter values plus EQ constraints at run time.
18. Center reference planes (horizontal & vertical) must be positioned using **Project Base Point, Survey Point, or Internal Origin** — whichever proves most reliable for this use case (see ARCHITECTURE.md §7 / DESIGN.md §Origin Strategy) — not an arbitrary screen-based or hard-coded model point. If an existing bridge-generator/Dynamo convention already picks one of these three, that convention wins.

## API Usage
19. Do not invent Revit API method/class names. Verify exact Revit 2024.3 API members (Global Parameter creation, formula application, ReferencePlane creation, obtaining valid References from a ReferencePlane, dimension creation, GP↔dimension association, equality constraints) against the installed API/documentation before writing calls.
20. Use proper `Transaction`/`TransactionGroup` scoping for every document modification. No modifications outside a transaction. Roll back cleanly on failure — never leave a half-built skeleton committed.
21. Call `doc.Regenerate()` only where the next step genuinely requires it (e.g., new references needed by subsequent dimensioning), not reflexively after every line.

## Verification Before Building
22. Inspect the existing `ReferencePlane.pushbutton` (script.py, bundle.yaml, any XAML, imported shared modules) and the wider extension before writing or overwriting anything. Modify the smallest reasonable scope; extend, don't blindly replace, working files.
23. Determine the pyRevit Python engine from existing bundle/script conventions rather than assuming one.
24. Determine naming conventions for reference planes from existing manual bridge files / Dynamo graphs / scripts first; only fall back to the conceptual names in DESIGN.md if nothing established exists.
25. Only use Dynamo in this tool if inspection proves this specific module already intentionally depends on it. Otherwise implement directly via pyRevit/Revit API.

## Error Messaging
26. User-facing errors must explain what failed in domain terms (e.g. "Could not create the Clear Span driving dimension because the active view does not provide valid references for the required Reference Planes"), not raw exception text like "Object reference not set to an instance of an object." Technical detail can be included as secondary/debug info.
27. Never silently swallow exceptions. Every failure path logs and surfaces a message.

## Single Source of Truth
28. Parameter names/groups/values/formulas/dependencies live in exactly one place (`core/param_spec.py`). UI and creation logic both read from it — never duplicate the numbers in two files.

## Source-of-Truth Priority (when information conflicts)
1. The user's latest explicit requirements (this conversation / master prompt / corrections).
2. Existing verified working project files on disk.
3. Supplied bridge reference screenshots/spec numbers.
4. Existing bridge Dynamo/reference implementations.
5. Older architectural documentation (e.g., outdated folder-naming docs).
6. The agent's own assumptions — lowest priority, must be flagged if used.

## When Genuinely Unclear
29. Investigate the project (code, model, Dynamo, existing planes/GPs, nearby pushbuttons) before asking or guessing. Only flag something as unresolved if the project itself lacks enough information to decide safely. Never invent bridge-engineering rules that were not specified anywhere.
