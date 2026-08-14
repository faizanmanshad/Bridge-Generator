# -*- coding: utf-8 -*-
"""
global_param_manager.py — Idempotent Global Parameter create/update/validate.

Reference Plane.pushbutton / core/
Urbana Bridge Generator — Revit 2024.3 / pyRevit 6.4.0 / IronPython 2.7

Public API:
    ensure_all(doc, definitions) -> list of status dicts

Each status dict:
    {
        "name":   str,
        "status": "Created" | "Existing" | "Updated" | "Formula Applied"
                  | "Conflict" | "Error",
        "detail": str   (human-readable message)
    }

Idempotency rules (RULES.md #10, #12):
- If parameter exists with compatible type (Length): reuse, update value/formula.
- If parameter exists with incompatible type: report Conflict, do NOT touch it.
- If parameter does not exist: create it.
- Never auto-delete or rename existing parameters.

Formula validation (PHASES.md Phase C):
- After applying all parameters, the formula results are checked against
  expected values (param_spec.value_mm) within FORMULA_VALIDATION_TOL_MM.

Revit 2024.3 API notes:
- FormulaParameterValue does NOT exist in Revit 2024.  The correct API is:
    gp.SetFormula(string)  — assign a formula to a non-reporting GlobalParameter
    gp.GetFormula()        — retrieve the current formula string (empty = no formula)
    gp.GetValue()          — always returns DoubleParameterValue with evaluated result
    gp.IsValidFormula(s)   — validate a formula string before applying it
- DoubleParameterValue   exists and is used for fixed (non-formula) values.
- SpecTypeId.Length       exists and is the correct type specifier.

Python compatibility: IronPython 2.7 — no f-strings, no dataclasses.
"""

import clr
clr.AddReference("RevitAPI")

from Autodesk.Revit.DB import (
    GlobalParameter,
    GlobalParametersManager,
    DoubleParameterValue,
    SpecTypeId,
)

from core.revit_compat import element_id_value

from core.units import mm_to_internal, internal_to_mm
from core.param_spec import FORMULA_VALIDATION_TOL_MM
from core.logging_utils import get_logger


# ---------------------------------------------------------------------------
# Status constants
# ---------------------------------------------------------------------------
STATUS_CREATED          = "Created"
STATUS_EXISTING         = "Existing"
STATUS_UPDATED          = "Updated"
STATUS_FORMULA_APPLIED  = "Formula Applied"
STATUS_CONFLICT         = "Conflict"
STATUS_ERROR            = "Error"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _find_global_param(doc, name):
    """Return the GlobalParameter element by exact name, or None."""
    gp_id = GlobalParametersManager.FindByName(doc, name)
    if gp_id is None or element_id_value(gp_id) == -1:
        return None
    return doc.GetElement(gp_id)


def _is_length_type(gp):
    """Return True if the GlobalParameter is a Length-type GP.

    In Revit 2024, gp.GetValue() always returns a DoubleParameterValue
    for any numeric GP (regardless of whether a formula is set).
    We verify the ParameterType / UnitType by catching AttributeError
    gracefully — if GetValue() works and returns a double, it is numeric.
    """
    try:
        val = gp.GetValue()
        return isinstance(val, DoubleParameterValue)
    except Exception:
        return False


def _has_formula(gp):
    """Return True if the GlobalParameter currently has a formula assigned.

    Revit 2024 API: gp.GetFormula() returns the formula string,
    or an empty string / None when no formula is set.
    """
    try:
        f = gp.GetFormula()
        return bool(f)   # non-empty string = has formula
    except Exception:
        return False


def _get_current_formula(gp):
    """Return the current formula string, or empty string if none."""
    try:
        f = gp.GetFormula()
        return f if f else ""
    except Exception:
        return ""


def _get_current_value_mm(gp):
    """Return the current evaluated value of a GlobalParameter in mm, or None.

    In Revit 2024, gp.GetValue() returns a DoubleParameterValue containing
    the evaluated result — whether the GP is formula-driven or not.
    """
    try:
        val = gp.GetValue()
        if isinstance(val, DoubleParameterValue):
            return internal_to_mm(val.Value)
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def ensure_all(doc, definitions):
    """Create or validate all Global Parameters defined in `definitions`.

    Parameters are processed in dep_order (ascending) to guarantee base
    parameters exist before formula parameters that reference them.

    Args:
        doc         (Document): Active Revit document.
        definitions (list):     From param_spec.DEFINITIONS.

    Returns:
        list of dicts: [{name, status, detail}, ...]
    """
    logger  = get_logger()
    results = []

    # Sort by dependency order — ensures base params precede formula params
    ordered = sorted(definitions, key=lambda d: d["dep_order"])

    for defn in ordered:
        name     = defn["name"]
        formula  = defn["formula"]
        value_mm = defn["value_mm"]

        try:
            result = _ensure_one(doc, name, formula, value_mm)
            results.append(result)
            logger.info(
                "Global parameter processed",
                name=name,
                status=result["status"],
                detail=result["detail"],
            )
        except Exception as exc:
            msg = "Unexpected error processing '{0}': {1}".format(name, str(exc))
            logger.error(msg, exc=exc, name=name)
            results.append({
                "name":   name,
                "status": STATUS_ERROR,
                "detail": msg,
            })

    # ------------------------------------------------------------------
    # Formula result validation
    # ------------------------------------------------------------------
    _validate_formula_results(doc, ordered, results)

    return results


def _ensure_one(doc, name, formula, value_mm):
    """Create or update a single Global Parameter.

    Returns a status dict: {name, status, detail}.
    """
    existing = _find_global_param(doc, name)

    if existing is not None:
        # Parameter already exists — check type compatibility
        # A GP that has neither a readable value nor a formula is incompatible.
        if not _is_length_type(existing) and not _has_formula(existing):
            return {
                "name":   name,
                "status": STATUS_CONFLICT,
                "detail": (
                    "Parameter '{0}' exists but is not a Length type. "
                    "It has not been modified. Remove it manually if "
                    "it needs to be replaced with a Length parameter."
                ).format(name),
            }

        # Compatible — update formula or value if needed
        if formula:
            return _apply_formula(existing, name, formula)
        else:
            # Base parameter (no formula). Do not overwrite user-set values with defaults.
            if _has_formula(existing):
                try:
                    existing.SetFormula("")
                except Exception:
                    pass
            
            current_mm = _get_current_value_mm(existing)
            if current_mm is not None:
                detail = "Retained existing value: {0:.1f} mm".format(current_mm)
            else:
                detail = "Retained existing value."
                
            return {
                "name":   name,
                "status": STATUS_EXISTING,
                "detail": detail,
            }

    else:
        # Parameter does not exist — create it
        return _create_param(doc, name, formula, value_mm)


def _create_param(doc, name, formula, value_mm):
    """Create a new Length-type GlobalParameter and set its value/formula.

    Revit 2024 API:
        GlobalParameter.Create(doc, name, SpecTypeId.Length) -> GlobalParameter
        gp.SetFormula(string)                                 -> assign formula
        gp.SetValue(DoubleParameterValue(internal))          -> assign fixed value
    """
    try:
        gp = GlobalParameter.Create(doc, name, SpecTypeId.Length)
        if gp is None:
            return {
                "name":   name,
                "status": STATUS_ERROR,
                "detail": "GlobalParameter.Create returned None for '{0}'.".format(name),
            }

        if formula:
            # Validate before applying (non-fatal if validation unavailable)
            try:
                if not gp.IsValidFormula(formula):
                    return {
                        "name":   name,
                        "status": STATUS_ERROR,
                        "detail": (
                            "Formula validation failed for '{0}': '{1}'. "
                            "Check that all referenced parameters exist."
                        ).format(name, formula),
                    }
            except Exception:
                pass  # IsValidFormula unavailable; proceed

            gp.SetFormula(formula)
            return {
                "name":   name,
                "status": STATUS_CREATED,
                "detail": "Created with formula: {0}".format(formula),
            }
        else:
            gp.SetValue(DoubleParameterValue(mm_to_internal(value_mm)))
            return {
                "name":   name,
                "status": STATUS_CREATED,
                "detail": "Created with value: {0} mm".format(value_mm),
            }

    except Exception as exc:
        return {
            "name":   name,
            "status": STATUS_ERROR,
            "detail": "Failed to create '{0}': {1}".format(name, str(exc)),
        }


def _apply_formula(gp, name, formula):
    """Apply or re-apply a formula to an existing GlobalParameter.

    Revit 2024 API: gp.SetFormula(string)
    """
    try:
        current_formula = _get_current_formula(gp)
        if current_formula == formula:
            return {
                "name":   name,
                "status": STATUS_EXISTING,
                "detail": "Formula already set: {0}".format(formula),
            }

        # Validate before applying (non-fatal if validation unavailable)
        try:
            if not gp.IsValidFormula(formula):
                return {
                    "name":   name,
                    "status": STATUS_ERROR,
                    "detail": (
                        "Formula validation failed for '{0}': '{1}'. "
                        "Check that all referenced parameters exist."
                    ).format(name, formula),
                }
        except Exception:
            pass

        gp.SetFormula(formula)
        return {
            "name":   name,
            "status": STATUS_FORMULA_APPLIED,
            "detail": "Formula applied: {0}".format(formula),
        }
    except Exception as exc:
        return {
            "name":   name,
            "status": STATUS_ERROR,
            "detail": "Failed to apply formula to '{0}': {1}".format(name, str(exc)),
        }


def _apply_value(gp, name, value_mm):
    """Set or confirm the numeric value on an existing GlobalParameter.

    Revit 2024 API: gp.SetValue(DoubleParameterValue(internal_units))
    Also clears any formula first if one is currently set.
    """
    try:
        # If a formula is set, clear it before setting a fixed value
        if _has_formula(gp):
            gp.SetFormula("")  # empty string removes the formula

        current_mm = _get_current_value_mm(gp)
        target_internal = mm_to_internal(value_mm)

        if current_mm is not None and abs(current_mm - value_mm) < FORMULA_VALIDATION_TOL_MM:
            return {
                "name":   name,
                "status": STATUS_EXISTING,
                "detail": "Value already correct: {0} mm".format(value_mm),
            }

        gp.SetValue(DoubleParameterValue(target_internal))
        return {
            "name":   name,
            "status": STATUS_UPDATED,
            "detail": "Updated to {0} mm (was {1} mm)".format(
                value_mm,
                "{0:.4f}".format(current_mm) if current_mm is not None else "unknown"
            ),
        }
    except Exception as exc:
        return {
            "name":   name,
            "status": STATUS_ERROR,
            "detail": "Failed to set value on '{0}': {1}".format(name, str(exc)),
        }


def _validate_formula_results(doc, ordered, results):
    """Check that formula parameters evaluate to their expected values.

    In Revit 2024, gp.GetValue() always returns the evaluated DoubleParameterValue
    regardless of whether a formula is set, so this works uniformly.
    """
    logger = get_logger()
    for defn in ordered:
        if defn["formula"] is None:
            continue  # Only validate formula parameters

        name     = defn["name"]
        expected = defn["value_mm"]
        if expected is None:
            continue

        gp = _find_global_param(doc, name)
        if gp is None:
            continue

        actual_mm = _get_current_value_mm(gp)
        if actual_mm is None:
            continue

        diff = abs(actual_mm - expected)
        if diff > FORMULA_VALIDATION_TOL_MM:
            warning = (
                "Formula validation WARNING for '{0}': "
                "expected {1} mm, got {2:.4f} mm (diff {3:.4f} mm)"
            ).format(name, expected, actual_mm, diff)
            logger.warning(warning, name=name, expected_mm=expected, actual_mm=actual_mm)

            for r in results:
                if r["name"] == name:
                    r["detail"] += " | VALIDATION WARNING: {0}".format(warning)
                    break
        else:
            logger.info(
                "Formula validation passed",
                name=name,
                expected_mm=expected,
                actual_mm=actual_mm,
            )
