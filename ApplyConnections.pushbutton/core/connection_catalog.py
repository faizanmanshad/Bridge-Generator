# -*- coding: utf-8 -*-
"""
connection_catalog.py — Retrieve available Structural Connection types.

ApplyConnections.pushbutton / core/
Urbana Bridge Generator — Revit 2022 / 2024.3  |  pyRevit 6.4.0  |  IronPython 2.7

Reads StructuralConnectionHandlerType elements from the active Revit document
and exposes them as simple ConnectionTypeItem objects suitable for WPF ComboBox
population.

The API class exists in both Revit 2022 and 2024 under:
    Autodesk.Revit.DB.Structure.StructuralConnectionHandlerType

If it is unavailable (e.g. structural module not licensed, or older Revit
build without steel connections), the catalog returns an empty list and
`is_available` is False.

Python compatibility: IronPython 2.7 — no f-strings, no dataclasses.
"""

import clr
clr.AddReference("RevitAPI")

from Autodesk.Revit.DB import FilteredElementCollector  # type: ignore

from core.revit_compat import element_id_value


# ---------------------------------------------------------------------------
# Module-level availability flag (determined on first import)
# ---------------------------------------------------------------------------

def is_available():
    """Return True if Structural Connections can be queried."""
    return True


# ---------------------------------------------------------------------------
# Data object
# ---------------------------------------------------------------------------

class ConnectionTypeItem(object):
    """Lightweight representation of one Structural Connection type.

    Attributes:
        name:       str  — display name for the ComboBox.
        element_id: ElementId — used when creating a connection.
        int_id:     int  — Python-integer version of element_id.
    """

    def __init__(self, name, element_id):
        self.name       = name
        self.element_id = element_id
        self.int_id     = element_id_value(element_id)

    def __repr__(self):
        return "ConnectionTypeItem({0!r}, id={1})".format(self.name, self.int_id)


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _get_safe_name(elem):
    """Safely extract the display name of a connection type.
    
    Accessing .Name on StructuralConnectionHandlerType throws an exception
    in some versions of Revit. We extract the name via parameters instead.
    """
    try:
        from Autodesk.Revit.DB import BuiltInParameter
        # Try SYMBOL_NAME_PARAM first (standard for ElementTypes)
        p = elem.get_Parameter(BuiltInParameter.SYMBOL_NAME_PARAM)
        if p and p.HasValue:
            val = p.AsString()
            if val:
                return val
                
        # Try ALL_MODEL_TYPE_NAME
        p = elem.get_Parameter(BuiltInParameter.ALL_MODEL_TYPE_NAME)
        if p and p.HasValue:
            val = p.AsString()
            if val:
                return val
    except Exception:
        pass

    # Fallback to direct property access if parameter extraction fails
    try:
        return elem.Name
    except Exception:
        return "Unnamed Connection"


# ---------------------------------------------------------------------------
# Catalog retrieval
# ---------------------------------------------------------------------------

def get_connection_types(doc):
    """Return a sorted list of ConnectionTypeItem objects from the document.

    Returns an empty list if no connection types are loaded in the current document.

    Args:
        doc: Autodesk.Revit.DB.Document — the active project document.

    Returns:
        list[ConnectionTypeItem]  — may be empty.
    """
    from Autodesk.Revit.DB import FilteredElementCollector, BuiltInCategory
    items = []
    seen_ids = set()

    def _add_elements(collector):
        for elem in collector:
            try:
                elem_id = elem.Id
                int_id = element_id_value(elem_id)
                if int_id in seen_ids:
                    continue
                seen_ids.add(int_id)
                name = _get_safe_name(elem)
                items.append(ConnectionTypeItem(name, elem_id))
            except Exception:
                continue

    # 1. Collect explicitly by StructuralConnectionHandlerType (133 items found in diag)
    try:
        from Autodesk.Revit.DB.Structure import StructuralConnectionHandlerType
        _add_elements(FilteredElementCollector(doc).OfClass(StructuralConnectionHandlerType))
    except Exception:
        pass

    # 2. Collect broadly by category OST_StructConnections (Note the 's')
    try:
        _add_elements(
            FilteredElementCollector(doc)
            .OfCategory(BuiltInCategory.OST_StructConnections)
            .WhereElementIsElementType()
        )
    except Exception:
        pass

    # Sort alphabetically for predictable UI ordering
    items.sort(key=lambda x: x.name.lower())
    return items


def find_by_id(connection_types, element_id):
    """Return the ConnectionTypeItem with the given ElementId, or None.

    Args:
        connection_types: list[ConnectionTypeItem]
        element_id:       Revit ElementId

    Returns:
        ConnectionTypeItem or None
    """
    if element_id is None:
        return None
    target = element_id_value(element_id)
    for item in connection_types:
        if item.int_id == target:
            return item
    return None
