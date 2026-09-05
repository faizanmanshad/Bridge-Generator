# -*- coding: utf-8 -*-
"""
selection.py — Revit element selection wrapper.

Apply Connections.pushbutton / core/
Urbana Bridge Generator — Revit 2022 / 2024.3  |  pyRevit 6.4.0  |  IronPython 2.7

Provides a clean PickObject wrapper that:
  - Restricts selection to Structural Framing elements via an ISelectionFilter.
  - Returns (element, cancelled) tuples — never raises on user cancellation.
  - Distinguishes user Escape (clean cancel) from genuine errors.

Python compatibility: IronPython 2.7 — no f-strings, no dataclasses.
"""

import clr
clr.AddReference("RevitAPI")
clr.AddReference("RevitAPIUI")

from Autodesk.Revit.DB import (          # type: ignore
    BuiltInCategory,
    LocationCurve,
)
from Autodesk.Revit.UI.Selection import (  # type: ignore
    ISelectionFilter,
    ObjectType,
)
from Autodesk.Revit.Exceptions import (   # type: ignore
    OperationCanceledException,
)


# ---------------------------------------------------------------------------
# Selection filter — restrict to Structural Framing only
# ---------------------------------------------------------------------------

class StructuralFramingFilter(ISelectionFilter):
    """ISelectionFilter that accepts only Structural Framing elements.

    Elements outside this category are immediately rejected with a tooltip
    displayed by the Revit selection mechanism.
    """

    def AllowElement(self, element):
        """Return True only for Structural Framing members."""
        try:
            cat = element.Category
            if cat is None:
                return False
            return cat.Id.IntegerValue == int(BuiltInCategory.OST_StructuralFraming)
        except Exception:
            return False

    def AllowReference(self, reference, point):
        """Allow all face/edge references from filtered elements."""
        return True


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def pick_structural_framing(uidoc):
    """Prompt the user to select ONE Structural Framing element in the model.

    The prompt message guides the user. If they press Escape, we return
    (None, True) — a clean cancellation. Genuine runtime errors are re-raised.

    Args:
        uidoc: Autodesk.Revit.UI.UIDocument — the active UI document.

    Returns:
        (element, False)  — element is an Autodesk.Revit.DB.Element
        (None,    True)   — user cancelled (Escape key or similar)
    """
    sel_filter  = StructuralFramingFilter()
    prompt_text = "Select the Main Beam (Structural Framing element)"

    try:
        reference = uidoc.Selection.PickObject(
            ObjectType.Element,
            sel_filter,
            prompt_text,
        )
        element = uidoc.Document.GetElement(reference.ElementId)
        return element, False

    except OperationCanceledException:
        # User pressed Escape — clean, expected cancellation
        return None, True

    except Exception:
        # Re-raise unexpected errors so the caller can log and surface them
        raise


def describe_element(element):
    """Return a concise display string for a structural framing element.

    Format:  FamilyName : TypeName : ElementId

    Args:
        element: Autodesk.Revit.DB.Element

    Returns:
        str
    """
    if element is None:
        return "(none)"

    try:
        family_name = element.Symbol.Family.Name
    except Exception:
        family_name = "Unknown Family"

    try:
        type_name = element.Symbol.Name
    except Exception:
        type_name = "Unknown Type"

    try:
        from core.revit_compat import element_id_value
        eid = element_id_value(element.Id)
    except Exception:
        eid = "?"

    return "{0} : {1} : {2}".format(family_name, type_name, eid)
