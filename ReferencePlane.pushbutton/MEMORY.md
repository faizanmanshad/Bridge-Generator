# MEMORY.md — Persistent Context for the AI Agent

Purpose: this file is the "memory" the agent (or a future session/agent) should read first, before PRD/ARCHITECTURE/RULES/PHASES/DESIGN, to avoid re-deciding settled questions or repeating mistakes.

## Project Identity
- Project: Urbana Bridge Generator, pyRevit plugin suite for Autodesk Revit 2024.3 (pyRevit 6.4.0, Windows, metric/mm).
- Current task scope: **one tool only** — `Reference Plane.pushbutton`, which builds the parametric control skeleton (Global Parameters → Driving Dimensions → Reference Planes) that all future bridge-generation tools depend on.
- Two bridge families exist conceptually (Timber Deck, Concrete Deck) but neither is in scope here — this tool is shared foundational infrastructure for both.

## Confirmed / Corrected Facts (override anything older)
1. **Authoritative on-disk path (confirmed by user):**
   ```
   D:\All Revit\19 Revit Plugins\RevitExtensions\Urbana.extension\Urbana.tab\BridgeGenerator.panel\ReferencePlane.pushbutton
   ```
   Earlier docs mentioned `BridgeGenerator.extension` at the RevitExtensions root — that is superseded/secondary; the filesystem inspection result is authoritative. If both exist, verify which is actually loaded by pyRevit before acting.
2. **Origin/center correction (confirmed by user):** the center reference planes (both horizontal and vertical) must be derived from Revit's **Project Base Point, Survey Point, or Internal Origin** — whichever works best for this use case — not an arbitrary/hard-coded model point. See DESIGN.md §2 and ARCHITECTURE.md §7 for the resolution order (existing convention → Project Base Point → Survey Point → Internal Origin).

## Non-Negotiable Constraints (see RULES.md for full list)
- Exact-name, exact-formula preservation for all 25 Global Parameters (case/spacing/underscore sensitive; do not "clean up" formulas even if redundant-looking).
- Full idempotency: rerunning either tab must never create duplicates (parameters, planes, or dimensions).
- Never bulk-delete or auto-replace existing elements; report conflicts instead of resolving them destructively.
- No static/hard-coded coordinate geometry — everything must trace back to live Global Parameters + EQ constraints.
- Strict scope: only this one pushbutton; no beams/bearers/joists/decks/etc.; no other pushbuttons touched.

## Key Numbers (initial/reference bridge — for validation, not universal constants)
- 25 Global Parameters total (Construction 4 / Dimensions 10 / Other 11).
- 9 horizontal reference planes (1 center + 4 symmetric pairs: Vertical Joist Bounding Distance, Bearers Length, Clear Span, Abutment Width).
- 7 vertical reference planes (Left End, Left Intermediate, Left Crank, Center, Right Crank, Right Intermediate, Right End).
- Validation targets: GP_UB_Height=482.6mm, GP_Horizontal_Joist_Height=80mm, GP_Vertical_Joists_Height=225mm, Bearers Length=2314.4mm, Vertical Joist Bounding Distance=2144mm, GP_End_Offset_H_V_joist=5mm, GP_End_Offset_U_Beam=-40mm, GP_Start_Offset_U_Beam=-80mm, GP_Start_Offset_H_V_Joist=-35mm.

## Open Items to Resolve During Phase A (Discovery) — not yet known
- Whether `Urbana.extension` or `BridgeGenerator.extension` is the one actually loaded/active in pyRevit.
- Existing pyRevit Python engine convention used by this bundle/sibling buttons.
- Whether existing Dynamo graphs or manual bridge files already imply a specific origin choice (Project Base Point vs Survey Point vs Internal Origin) or a specific reference-plane naming convention — if so, that overrides the DESIGN.md fallback naming/origin scheme.
- Whether the pushbutton already has WPF/XAML UI to extend, or needs one built from scratch.
- Whether any of the 25 Global Parameters already exist in target models (affects idempotency test expectations on first real run).

## Document Map
- **PRD.md** — what & why (requirements, scope, definition of done).
- **ARCHITECTURE.md** — how the code is organized (modules, data flow, transactions, idempotency mechanics, origin resolution).
- **RULES.md** — hard constraints the agent must never violate.
- **PHASES.md** — step-by-step build + mandatory test plan (A→G).
- **DESIGN.md** — full parameter table, naming conventions, plane positions, dimension/constraint design, UI layout.
- **MEMORY.md** (this file) — durable cross-session context, confirmed corrections, open questions.

## Session Log
| Date | Change |
|---|---|
| Initial | Master automation spec received (62 sections) covering full Reference Plane.pushbutton scope. |
| Correction 1 | Confirmed exact on-disk pushbutton path under `Urbana.extension`. |
| Correction 2 | Center reference planes must use Project Base Point / Survey Point / Internal Origin (whichever fits best), not an arbitrary point. |

*(Future sessions: append new rows here rather than editing history, so context stays traceable.)*
