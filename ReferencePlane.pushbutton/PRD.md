# PRD.md — Urbana Bridge Generator: Reference Plane.pushbutton

## 1. Product Summary
Build/extend a single pyRevit pushbutton, **Reference Plane.pushbutton**, that establishes the parametric control skeleton (Global Parameters + Reference Planes + driving dimensions + constraints) that all future Urbana bridge-generation tools will depend on.

This is not a standalone script — it is foundational infrastructure. Nothing downstream (beams, bearers, joists, decks, etc.) is in scope for this task.

## 2. Confirmed Location (authoritative)
```
D:\All Revit\19 Revit Plugins\RevitExtensions\Urbana.extension\Urbana.tab\BridgeGenerator.panel\ReferencePlane.pushbutton
```
This is the real, on-disk, working extension path and is the single source of truth for where work happens. The agent must inspect this folder before writing any code. Do not create a second/parallel extension. Do not rename or restructure the extension tree.

> Historical note: earlier documentation referenced `BridgeGenerator.extension` at the RevitExtensions root. If both exist, the filesystem is authoritative — verify which one is actually wired into pyRevit (has valid `bundle.yaml`/`icon`/is loaded) before touching anything, and prefer the confirmed `Urbana.extension` path above unless inspection proves otherwise.

## 3. Target Users
Internal Urbana engineers/drafters using Revit 2024.3 + pyRevit 6.4.0 on Windows, working in millimetres, building timber-deck and concrete-deck pedestrian/vehicle bridges.

## 4. Problem Statement
Bridge control geometry (widths, spans, longitudinal stations) is currently set up manually and inconsistently. This tool must let a user click one button and get a reliable, named, formula-driven, re-runnable control skeleton that later automation (including existing Dynamo graphs) can trust.

## 5. Functional Requirements

### 5.1 Entry Point
One pyRevit pushbutton → one popup window with two tabs:
- **Tab 1: Global Parameters**
- **Tab 2: Reference Planes**

### 5.2 Tab 1 — Global Parameters
Creates/validates exactly **25 Global Parameters** (Construction: 4, Dimensions: 10, Other: 11) with exact names, values, formulas, and groups as specified in the master spec (see DESIGN.md §Parameter Table for the full authoritative list — names are case-sensitive, spaces/underscores are intentional and must not be normalized).

Must be:
- Idempotent (safe to click repeatedly — no `Clear Span 1`, `Clear Span 2`)
- Dependency-ordered (base params before formula params)
- Status-transparent (Missing / Existing / Created / Updated / Formula Applied / Error)
- Non-destructive on conflict (never auto-delete/replace an incompatible existing parameter — report it)

### 5.3 Tab 2 — Reference Planes
Creates the parametric skeleton:
- **9 horizontal reference planes** (width/transverse control): 1 center + 4 symmetric pairs (Vertical Joist Bounding Distance, Bearers Length, Clear Span, Abutment Width)
- **7 vertical reference planes** (length/longitudinal control): Left End, Left Intermediate, Left Crank, Center, Right Crank, Right Intermediate, Right End

Requires the following Global Parameters to already exist before running: Clear Span, Length, Crank Length, Abutment Width, Bearers Length, Vertical Joist Bounding Distance. If any are missing, abort cleanly and name the missing ones — no partial skeleton is ever committed.

### 5.4 Dimensions & Constraints
- Overall driving dimension per symmetric pair, associated with its Global Parameter
- Center-based EQ chain per pair (keeps pair centered)
- Longitudinal: overall Length dimension, EQ half-length chain, crank-length segments (2×), outer EQ segments (2×2)
- Wherever the Revit 2024.3 API supports it, dimensions must be natively associated with Global Parameters so changing a parameter moves the geometry — this is not simulated by recalculating coordinates unless a verified API limitation forces a documented fallback.

### 5.5 Center / Origin Reference (correction applied)
The intersection of the central horizontal and central vertical reference planes is the bridge's logical center. For establishing this center, the tool must reference **Revit's Project Base Point, Survey Point, or Internal Origin** — whichever is proven most reliable for this use case during implementation (see DESIGN.md §Origin Strategy for the decision procedure). It must **not** invent a new arbitrary origin convention. If the existing bridge-generator/Dynamo workflow already establishes a working origin convention among these, that existing convention takes priority.

## 6. Non-Functional Requirements
- Metric units throughout the UI/spec layer; correct mm↔internal-unit conversion via a centralized helper for all API calls (Revit 2024.3 unit API).
- Parameters created as Length-type Global Parameters (not text/number/integer).
- Transaction-safe: rollback on failure, no half-built skeleton.
- Active-view validated before any dimension/reference-plane work; no silent view switching.
- Idempotent end-to-end: rerunning either tab twice produces no duplicates.

## 7. Explicit Out of Scope
Universal Beams, Bearers, Packers, Joists, Bearings, Decks (timber/concrete), connections, bracing, handrails, shop drawings, sheets, unrelated annotations, Dynamo bridge-component generation, and any modification to unrelated pushbuttons/extensions.

## 8. Definition of Done
1. Button opens the two-tab popup.
2. Tab 1 reliably creates/validates all 25 parameters with correct formulas and values, re-runnable with zero duplicates.
3. Tab 2 creates the 9 + 7 reference-plane skeleton with correct dimensions/EQ/crank constraints, associated with Global Parameters where the API allows, re-runnable with zero duplicates.
4. Center of the skeleton is derived from Project Base Point / Survey Point / Internal Origin per DESIGN.md.
5. Changing Clear Span, Abutment Width, Length, or Crank Length after creation updates the skeleton through native Revit parametric behavior (validated by the dynamic tests in PHASES.md).
6. Missing-dependency and re-run scenarios are handled gracefully with clear user-facing messages.
7. No unrelated files/pushbuttons were modified.
8. A completion report is produced per the deliverables list in PHASES.md.

## 9. Success Metrics
- 0 duplicate parameters/planes/dimensions across repeated runs.
- 100% of the 25 parameters match name/value/formula spec exactly.
- All 4 dynamic parametric tests (Clear Span, Abutment Width, Length, Crank Length) pass.
- Tool usable on bridges from 8 m to 20 m without reference-plane extents running out.
