# -*- coding: utf-8 -*-
"""
connection_catalog.py — Retrieve available Structural Connection types.

Apply Connections.pushbutton / core/
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

_HANDLER_TYPE_CLASS = None   # Set to the class if available, None otherwise

try:
    from Autodesk.Revit.DB.Structure import StructuralConnectionHandlerType  # type: ignore
    _HANDLER_TYPE_CLASS = StructuralConnectionHandlerType
except (ImportError, Exception):
    _HANDLER_TYPE_CLASS = None


def is_available():
    """Return True if StructuralConnectionHandlerType can be used."""
    return _HANDLER_TYPE_CLASS is not None


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
# Catalog retrieval
# ---------------------------------------------------------------------------

def get_connection_types(doc):
    """Return a sorted list of ConnectionTypeItem objects from the document.

    Returns an empty list if:
      - StructuralConnectionHandlerType is not available in this Revit build.
      - No connection types are loaded in the current document.

    Args:
        doc: Autodesk.Revit.DB.Document — the active project document.

    Returns:
        list[ConnectionTypeItem]  — may be empty.
    """
    if not is_available():
        return []

    try:
        collector = (
            FilteredElementCollector(doc)
            .OfClass(_HANDLER_TYPE_CLASS)
        )
        items = []
        for elem in collector:
            try:
                name = elem.Name or "Unnamed Connection"
                items.append(ConnectionTypeItem(name, elem.Id))
            except Exception:
                # Skip any element that can't be read
                continue

        # Sort alphabetically for predictable UI ordering
        items.sort(key=lambda x: x.name.lower())
        return items

    except Exception:
        return []


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
