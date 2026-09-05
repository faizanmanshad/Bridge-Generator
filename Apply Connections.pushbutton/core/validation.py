# -*- coding: utf-8 -*-
"""
validation.py — Pre-flight checks for Apply Connections.pushbutton.

Apply Connections.pushbutton / core/
Urbana Bridge Generator — Revit 2022 / 2024.3  |  pyRevit 6.4.0  |  IronPython 2.7

All validation functions return a (success, message) tuple:
    (True,  "")                  — passed
    (False, "Human-readable…")   — failed, caller should surface this message

Python compatibility: IronPython 2.7 — no f-strings, no dataclasses.
"""

import clr
clr.AddReference("RevitAPI")

from Autodesk.Revit.DB import (       # type: ignore
    BuiltInCategory,
    LocationCurve,
)


# ---------------------------------------------------------------------------
# Validation: Connection type selection
# ---------------------------------------------------------------------------

def validate_connection_selection(connection_type_id):
    """Check that a structural connection type has been chosen.

    Args:
        connection_type_id: Revit ElementId, or None if nothing selected.

    Returns:
        (True, "") or (False, error_message)
    """
    if connection_type_id is None:
        return False, "No connection type selected. Please choose a structural connection type from the dropdown."
    return True, ""


# ---------------------------------------------------------------------------
# Validation: Main beam element
# ---------------------------------------------------------------------------

def validate_main_beam(doc, element_id):
    """Validate that element_id refers to a usable Structural Framing element.

    Checks:
      1. element_id is not None.
      2. Element exists in the document.
      3. Element is in the Structural Framing category.
      4. Element has a valid LocationCurve (not a point element).

    Args:
        doc:        Revit Document.
        element_id: ElementId to validate, or None.

    Returns:
        (True, "", element) or (False, error_message, None)
    """
    if element_id is None:
        return False, "No main beam selected. Please click 'Select Main Beam' and pick a structural framing element.", None

    try:
        element = doc.GetElement(element_id)
    except Exception as ex:
        return False, "Could not retrieve element from document: {0}".format(ex), None

    if element is None:
        return False, "The selected element no longer exists in the document. Please re-select.", None

    # Category check
    cat = element.Category
    if cat is None or cat.Id.IntegerValue != int(BuiltInCategory.OST_StructuralFraming):
        cat_name = cat.Name if cat else "Unknown"
        return (
            False,
            "The selected element '{0}' is not a Structural Framing member "
            "(category: {1}). Please select a beam.".format(element.Name, cat_name),
            None,
        )

    # Location geometry check
    loc = element.Location
    if not isinstance(loc, LocationCurve):
        return (
            False,
            "The selected element does not have a linear location (LocationCurve). "
            "Please select a standard structural beam.",
            None,
        )

    curve = loc.Curve
    if curve is None:
        return False, "The selected element's location curve is null. Cannot process.", None

    return True, "", element


# ---------------------------------------------------------------------------
# Validation: Detected joints
# ---------------------------------------------------------------------------

def validate_joints(joints):
    """Check that at least one valid joint was detected.

    Args:
        joints: list of JointRecord objects (may be empty or None).

    Returns:
        (True, "") or (False, error_message)
    """
    if not joints:
        return (
            False,
            "No valid beam-to-beam joints were detected for the selected main beam. "
            "Verify that secondary beams are modelled close enough to the primary beam.",
        )
    return True, ""


# ---------------------------------------------------------------------------
# Validation: Secondary candidate member
# ---------------------------------------------------------------------------

def validate_candidate(doc, candidate_id, primary_id):
    """Validate that a candidate secondary member is usable.

    Args:
        doc:          Revit Document.
        candidate_id: ElementId of the candidate secondary member.
        primary_id:   ElementId of the primary main beam (must differ).

    Returns:
        (True, "", element) or (False, error_message, None)
    """
    from core.revit_compat import element_ids_equal

    if element_ids_equal(candidate_id, primary_id):
        return False, "Candidate is the same element as the primary beam.", None

    try:
        element = doc.GetElement(candidate_id)
    except Exception as ex:
        return False, "Could not retrieve candidate element: {0}".format(ex), None

    if element is None:
        return False, "Candidate element no longer exists in the document.", None

    cat = element.Category
    if cat is None or cat.Id.IntegerValue != int(BuiltInCategory.OST_StructuralFraming):
        return False, "Candidate is not in the Structural Framing category.", None

    loc = element.Location
    if not isinstance(loc, LocationCurve):
        return False, "Candidate does not have a LocationCurve.", None

    return True, "", element
