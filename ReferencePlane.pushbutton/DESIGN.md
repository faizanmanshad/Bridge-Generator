# DESIGN.md — Detailed Design Reference

## 1. Global Parameter Table (single source of truth → mirror into `param_spec.py`)

Legend: Val = mm value (base params); Formula = exact formula string (formula params); Order = creation order (lower first).

### 1.1 Construction (4)
| Order | Name | Value/Formula |
|---|---|---|
| 1 | `GP_Framing_Height` | 35.0 mm (no formula) |
| 2 | `GP_Horizontal_Joist_Height` | `Horizontal_Joist_Height + GP_Framing_Height` → 80.0 mm |
| 3 | `GP_Vertical_Joists_Height` | `Vertical_Joist_Height + GP_Framing_Height` → 225.0 mm |
| 4 | `GP_UB_Height` | `UB_Height + GP_Horizontal_Joist_Height` → 482.6 mm |

### 1.2 Dimensions (10)
| Order | Name | Value/Formula |
|---|---|---|
| 1 | `Clear Span` | 2500.0 mm |
| 2 | `Length` | 12000.0 mm |
| 3 | `Camber` | 40.0 mm |
| 4 | `Crank Length` | 2000.0 mm |
| 5 | `Abutment Width` | 3500.0 mm |
| 6 | `Bearers Width` | 75.0 mm |
| 7 | `Connection Plate Thickness` | 30.0 mm |
| 8 | `Bearer Connection Width` | 180.0 mm |
| 9 | `Bearers Length` | `Clear Span - Beam Centerline - Beam Web` → 2314.4 mm |
| 10 | `Vertical Joist Bounding Distance` | `Clear Span - 2 * Beam Centerline` → 2144.0 mm |

### 1.3 Other (11)
| Order | Name | Value/Formula |
|---|---|---|
| 1 | `Beam Web` | 7.6 mm |
| 2 | `Vertical_Joist_Height` | 190.0 mm |
| 3 | `UB_Height` | 402.6 mm |
| 4 | `Horizontal_Joist_Height` | 45.0 mm |
| 5 | `Beam Centerline` | 178.0 mm |
| 6 | `Horizontal_Joist_Width` | 90.0 mm |
| 7 | `Bearer Width` | 75.0 mm |
| 8 | `GP_End_Offset_H_V_joist` | `if(GP_Framing_Height > Camber, -GP_Framing_Height + Camber, -GP_Framing_Height + Camber)` → 5.0 mm |
| 9 | `GP_End_Offset_U_Beam` | `if(GP_Horizontal_Joist_Height > Camber, -GP_Horizontal_Joist_Height + Camber, -GP_Horizontal_Joist_Height + Camber)` → -40.0 mm |
| 10 | `GP_Start_Offset_U_Beam` | `-GP_Horizontal_Joist_Height` → -80.0 mm |
| 11 | `GP_Start_Offset_H_V_Joist` | `-GP_Framing_Height` → -35.0 mm |

**Total: 25.** Preserve formulas verbatim, including the redundant-looking `if()` branches — do not simplify.

## 2. Origin Strategy (center of both plane systems)
Per the confirmed correction: the center horizontal and center vertical reference planes must be derived from one of Revit's built-in reference points — **Project Base Point, Survey Point, or Internal Origin** — not an arbitrary coordinate.

**Decision procedure (`origin_provider.get_bridge_center(doc)`):**
1. Inspect the model/Dynamo graphs for an existing convention. If the project already keys bridge geometry off a specific one of the three, use that same one for consistency.
2. If no existing convention: prefer **Project Base Point** (`BasePoint.GetProjectBasePoint(doc)`), since it's the standard structural/civil placement reference and is user-adjustable per project without moving true model geometry.
3. If Project Base Point is unreliable in this document (unplaced, not shared, or returns an unexpected transform), fall back to **Survey Point** (`BasePoint.GetSurveyPoint(doc)`).
4. If neither base point is usable, fall back to **Internal Origin** `XYZ(0,0,0)` in the model's internal coordinate system.
5. Whichever is chosen must be used consistently for both the horizontal-plane center (transverse, offset 0) and vertical-plane center (longitudinal, offset 0) — they share one logical bridge center point, just measured along perpendicular directions.
6. Log and report which origin source was used, every run, so it's auditable and never silently changes between sessions.

## 3. Reference Plane Naming (fallback convention — verify against existing project first per RULES.md #24)

**Width control (horizontal planes, 9 total):**
`Center`, `Vertical Joist Bounding Top`, `Vertical Joist Bounding Bottom`, `Bearer Top`, `Bearer Bottom`, `Clear Span Top`, `Clear Span Bottom`, `Abutment Top`, `Abutment Bottom`

**Length control (vertical planes, 7 total):**
`Left End`, `Left Intermediate`, `Left Crank`, `Center`, `Right Crank`, `Right Intermediate`, `Right End`

> Note: both systems use a plane named `Center` — since they're perpendicular (one horizontal, one vertical), disambiguate internally (e.g. prefix or a `Name`+`SubCategory`/comment marker) so lookups for idempotency never collide.

## 4. Horizontal (Width) Plane Positions — formula-driven, not hard-coded
Relative to center (offset 0), computed at runtime from live GP values:
- ± (Vertical Joist Bounding Distance / 2)
- ± (Bearers Length / 2)
- ± (Clear Span / 2)
- ± (Abutment Width / 2)

With initial values these evaluate to ±1072.0 / ±1157.2 / ±1250.0 / ±1750.0 mm — illustrative only, must be recomputed live.

## 5. Vertical (Length) Plane Positions — formula-driven, not hard-coded
Relative to center (offset 0):
- Ends: ± (Length / 2)
- Crank planes: ± Crank Length
- Intermediate planes: bisect the residual zone between end and crank on each side — i.e. positioned such that `End↔Intermediate` = `Intermediate↔Crank` (EQ), not a fixed number.

With initial values (Length=12000, Crank Length=2000): -6000 / -4000 / -2000 / 0 / +2000 / +4000 / +6000 mm — illustrative only.

## 6. Dimension/Constraint Design
**Per width pair** (4 pairs): overall outer-to-outer dimension → associated with driving GP; plus side→center→side chained dimension with both segments EQ.

**Length system:**
- Overall Left End→Right End dimension → associated with `Length`.
- Left End→Center→Right End chained dimension, both segments EQ.
- Detailed 6-segment chain (End→Intermediate→Crank→Center→Crank→Intermediate→End): the two Crank↔Center segments each = `Crank Length`; the two End↔Intermediate and Intermediate↔Crank segment pairs are EQ within their own side (left pair EQ to each other, right pair EQ to each other — not left EQ right, since Length and Crank Length can differ from the symmetric example numbers).

## 7. Visible Extents
- Horizontal reference planes: ~24000 mm (constant in `core/param_spec.py` or a small `constants.py`, not scattered) — supports bridges up to ~20 m with margin.
- Vertical reference planes: ~10000 mm.

## 8. UI Design
```
┌─────────────────────────────────────────┐
│ Bridge Setup                             │
├───────────────────┬───────────────────── ┤
│ Global Parameters  │ Reference Planes     │
├───────────────────┴───────────────────── ┤
│  (Tab 1 content)                         │
│  Status list: ✓ Created / ✓ Existing /   │
│  ✓ Updated / ✓ Formula Applied /         │
│  ! Missing / ✕ Error                     │
│                                           │
│  [ Create / Update Global Parameters ]   │
├───────────────────────────────────────── ┤
│  (Tab 2 content, shown when selected)    │
│  Required-parameter validation list      │
│  Reference-plane/dimension status list   │
│                                           │
│  [ Create / Update Reference Planes ]    │
├───────────────────────────────────────── ┤
│                              [ Close ]   │
└─────────────────────────────────────────┘
```
Keep it simple — no extra configuration controls for values already exposed as Global Parameters; the GPs themselves are the bridge's configuration surface.

## 9. Visual Design Notes
Match the existing pyRevit/WPF look already used elsewhere in the Urbana extension (fonts, spacing, button style) if any other pushbutton already has a custom-styled window — reuse that resource dictionary rather than introducing a new visual style.
