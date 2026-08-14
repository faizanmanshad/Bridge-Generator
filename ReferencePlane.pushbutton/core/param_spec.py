# -*- coding: utf-8 -*-
"""
param_spec.py — SINGLE SOURCE OF TRUTH for all 25 Global Parameters.

Reference Plane.pushbutton / core/
Urbana Bridge Generator — Revit 2024.3 / pyRevit 6.4.0 / IronPython 2.7

Usage:
    from core.param_spec import DEFINITIONS, PLANE_REQUIRED_GPS

Each entry in DEFINITIONS is a dict:
    name         (str)  — exact name, case-sensitive, spaces/underscores preserved
    group        (str)  — "Construction" | "Dimensions" | "Other"
    value_mm     (float or None) — initial value in mm; None for formula-only params
    formula      (str or None)  — exact Revit formula string; None for base params
    dep_order    (int)  — creation order; lower number = created first
                          base/independent params: 1–16
                          formula-dependent params: 17–25

RULES enforced here:
- Names are exact (Rule #5) — do not normalise.
- Formulas are verbatim (Rule #6) — do not simplify.
- Values are defaults for the reference bridge (Rule #7) — not scattered constants.
- dep_order guarantees base params are always created before formula params.

Python compatibility: IronPython 2.7 — no f-strings, no dataclasses.
"""

# ---------------------------------------------------------------------------
# 25 Global Parameter definitions — ordered by dep_order
# ---------------------------------------------------------------------------
DEFINITIONS = [

    # -----------------------------------------------------------------------
    # BASE / INDEPENDENT PARAMETERS (dep_order 1–16)
    # These carry no formula and must be created first.
    # -----------------------------------------------------------------------

    # Construction group — base
    {
        "name":      "GP_Framing_Height",
        "group":     "Construction",
        "value_mm":  35.0,
        "formula":   None,
        "dep_order": 1,
    },

    # Dimensions group — base
    {
        "name":      "Clear Span",
        "group":     "Dimensions",
        "value_mm":  2500.0,
        "formula":   None,
        "dep_order": 2,
    },
    {
        "name":      "Length",
        "group":     "Dimensions",
        "value_mm":  12000.0,
        "formula":   None,
        "dep_order": 3,
    },
    {
        "name":      "Camber",
        "group":     "Dimensions",
        "value_mm":  40.0,
        "formula":   None,
        "dep_order": 4,
    },
    {
        "name":      "Crank Length",
        "group":     "Dimensions",
        "value_mm":  2000.0,
        "formula":   None,
        "dep_order": 5,
    },
    {
        "name":      "Abutment Width",
        "group":     "Dimensions",
        "value_mm":  3500.0,
        "formula":   None,
        "dep_order": 6,
    },
    {
        "name":      "Bearers Width",
        "group":     "Dimensions",
        "value_mm":  75.0,
        "formula":   None,
        "dep_order": 7,
    },
    {
        "name":      "Connection Plate Thickness",
        "group":     "Dimensions",
        "value_mm":  30.0,
        "formula":   None,
        "dep_order": 8,
    },
    {
        "name":      "Bearer Connection Width",
        "group":     "Dimensions",
        "value_mm":  180.0,
        "formula":   None,
        "dep_order": 9,
    },

    # Other group — base (all independent)
    {
        "name":      "Beam Web",
        "group":     "Other",
        "value_mm":  7.6,
        "formula":   None,
        "dep_order": 10,
    },
    {
        "name":      "Vertical_Joist_Height",
        "group":     "Other",
        "value_mm":  190.0,
        "formula":   None,
        "dep_order": 11,
    },
    {
        "name":      "UB_Height",
        "group":     "Other",
        "value_mm":  402.6,
        "formula":   None,
        "dep_order": 12,
    },
    {
        "name":      "Horizontal_Joist_Height",
        "group":     "Other",
        "value_mm":  45.0,
        "formula":   None,
        "dep_order": 13,
    },
    {
        "name":      "Beam Centerline",
        "group":     "Other",
        "value_mm":  178.0,
        "formula":   None,
        "dep_order": 14,
    },
    {
        "name":      "Horizontal_Joist_Width",
        "group":     "Other",
        "value_mm":  90.0,
        "formula":   None,
        "dep_order": 15,
    },
    {
        "name":      "Bearer Width",
        "group":     "Other",
        "value_mm":  75.0,
        "formula":   None,
        "dep_order": 16,
    },

    # -----------------------------------------------------------------------
    # FORMULA-DEPENDENT PARAMETERS (dep_order 17–25)
    # Created only after all their base dependencies exist.
    # Formulas are preserved verbatim per RULES.md #6.
    # -----------------------------------------------------------------------

    # Construction group — formula-dependent
    {
        "name":      "GP_Horizontal_Joist_Height",
        "group":     "Construction",
        "value_mm":  80.0,   # expected result for validation
        "formula":   "Horizontal_Joist_Height + GP_Framing_Height",
        "dep_order": 17,
    },
    {
        "name":      "GP_Vertical_Joists_Height",
        "group":     "Construction",
        "value_mm":  225.0,  # expected result for validation
        "formula":   "Vertical_Joist_Height + GP_Framing_Height",
        "dep_order": 18,
    },
    {
        "name":      "GP_UB_Height",
        "group":     "Construction",
        "value_mm":  482.6,  # expected result for validation
        "formula":   "UB_Height + GP_Horizontal_Joist_Height",
        "dep_order": 19,
    },

    # Dimensions group — formula-dependent
    {
        "name":      "Bearers Length",
        "group":     "Dimensions",
        "value_mm":  2314.4,  # expected result for validation
        "formula":   "Clear Span - Beam Centerline - Beam Web",
        "dep_order": 20,
    },
    {
        "name":      "Vertical Joist Bounding Distance",
        "group":     "Dimensions",
        "value_mm":  2144.0,  # expected result for validation
        "formula":   "Clear Span - 2 * Beam Centerline",
        "dep_order": 21,
    },

    # Other group — formula-dependent
    # NOTE: the if() formulas with identical true/false branches are preserved
    # verbatim per RULES.md #6. Do not simplify them.
    {
        "name":      "GP_End_Offset_H_V_joist",
        "group":     "Other",
        "value_mm":  5.0,    # expected result for validation
        "formula":   "if(GP_Framing_Height > Camber, -GP_Framing_Height + Camber, -GP_Framing_Height + Camber)",
        "dep_order": 22,
    },
    {
        "name":      "GP_End_Offset_U_Beam",
        "group":     "Other",
        "value_mm":  -40.0,  # expected result for validation
        "formula":   "if(GP_Horizontal_Joist_Height > Camber, -GP_Horizontal_Joist_Height + Camber, -GP_Horizontal_Joist_Height + Camber)",
        "dep_order": 23,
    },
    {
        "name":      "GP_Start_Offset_U_Beam",
        "group":     "Other",
        "value_mm":  -80.0,  # expected result for validation
        "formula":   "-GP_Horizontal_Joist_Height",
        "dep_order": 24,
    },
    {
        "name":      "GP_Start_Offset_H_V_Joist",
        "group":     "Other",
        "value_mm":  -35.0,  # expected result for validation
        "formula":   "-GP_Framing_Height",
        "dep_order": 25,
    },
]

# ---------------------------------------------------------------------------
# Quick lookup: name → definition dict (built once at import time)
# ---------------------------------------------------------------------------
DEFINITIONS_BY_NAME = {d["name"]: d for d in DEFINITIONS}

# ---------------------------------------------------------------------------
# Global Parameters that MUST exist before Tab 2 (Reference Planes) can run.
# Validation will abort cleanly and name any that are missing.
# ---------------------------------------------------------------------------
PLANE_REQUIRED_GPS = [
    "Clear Span",
    "Length",
    "Crank Length",
    "Abutment Width",
    "Bearers Length",
    "Vertical Joist Bounding Distance",
]

# ---------------------------------------------------------------------------
# Reference plane visible extents (mm) — stored here, not scattered in code
# ---------------------------------------------------------------------------
HORIZ_PLANE_EXTENT_MM = 24000.0   # horizontal (width-control) planes
VERT_PLANE_EXTENT_MM  = 10000.0   # vertical (length-control) planes

# ---------------------------------------------------------------------------
# Formula validation tolerances (mm)
# ---------------------------------------------------------------------------
FORMULA_VALIDATION_TOL_MM = 0.01  # 0.01 mm tolerance for float comparison
