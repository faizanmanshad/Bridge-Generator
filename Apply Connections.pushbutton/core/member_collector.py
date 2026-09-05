# -*- coding: utf-8 -*-
"""
member_collector.py — Collect Structural Framing elements from the document.

Apply Connections.pushbutton / core/
Urbana Bridge Generator — Revit 2022 / 2024.3  |  pyRevit 6.4.0  |  IronPython 2.7

Provides an efficient collector for Structural Framing elements.
Candidate collection is limited to the current document scope — no full
solid-vs-solid geometry search is performed at this stage.

Python compatibility: IronPython 2.7 — no f-strings, no dataclasses.
"""

import clr
clr.AddReference("RevitAPI")

from Autodesk.Revit.DB import (          # type: ignore
    FilteredElementCollector,
    BuiltInCategory,
    ElementCategoryFilter,
    LocationCurve,
)

from core.revit_compat import element_id_value


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def collect_structural_framing(doc, exclude_ids=None):
    """Return a list of all Structural Framing elements in the document.

    Args:
        doc:         Autodesk.Revit.DB.Document
        exclude_ids: iterable of ElementId to exclude (e.g. the primary beam).
                     Pass None or empty to exclude nothing.

    Returns:
        list[Element] — may be empty.
    """
    exclude_set = set()
    if exclude_ids:
        for eid in exclude_ids:
            exclude_set.add(element_id_value(eid))

    try:
        collector = (
            FilteredElementCollector(doc)
            .OfCategory(BuiltInCategory.OST_StructuralFraming)
            .WhereElementIsNotElementType()
        )

        results = []
        for elem in collector:
            try:
                eid_int = element_id_value(elem.Id)
                if eid_int in exclude_set:
                    continue

                # Only include elements with a usable LocationCurve
                loc = elem.Location
                if not isinstance(loc, LocationCurve):
                    continue
                if loc.Curve is None:
                    continue

                results.append(elem)
            except Exception:
                continue

        return results

    except Exception:
        return []


def get_location_curve(element):
    """Return the LocationCurve from a Structural Framing element, or None.

    Args:
        element: Autodesk.Revit.DB.Element

    Returns:
        Autodesk.Revit.DB.LocationCurve or None
    """
    try:
        loc = element.Location
        if isinstance(loc, LocationCurve):
            return loc
    except Exception:
        pass
    return None
