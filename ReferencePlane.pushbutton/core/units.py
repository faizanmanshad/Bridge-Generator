# -*- coding: utf-8 -*-
"""
units.py — Centralised mm ↔ Revit internal-unit conversion helpers.

Reference Plane.pushbutton / core/
Urbana Bridge Generator — Revit 2024.3 / pyRevit 6.4.0 / IronPython 2.7

Revit 2024 stores all lengths in decimal feet (internal units).
All domain values in this plugin are expressed in millimetres.
NEVER pass raw mm values into Revit API calls — always use these helpers.

Public API:
    mm_to_internal(mm_value)  -> float  (mm → decimal feet)
    internal_to_mm(ft_value)  -> float  (decimal feet → mm)

Python compatibility: IronPython 2.7 — no f-strings, no dataclasses.
"""

import clr
clr.AddReference("RevitAPI")

from Autodesk.Revit.DB import UnitUtils, UnitTypeId


def mm_to_internal(mm_value):
    """Convert millimetres to Revit internal units (decimal feet).

    Args:
        mm_value (float|int): Length in millimetres.

    Returns:
        float: Equivalent length in Revit internal units.
    """
    return UnitUtils.ConvertToInternalUnits(float(mm_value), UnitTypeId.Millimeters)


def internal_to_mm(internal_value):
    """Convert Revit internal units (decimal feet) to millimetres.

    Args:
        internal_value (float): Length in Revit internal units.

    Returns:
        float: Equivalent length in millimetres.
    """
    return UnitUtils.ConvertFromInternalUnits(float(internal_value), UnitTypeId.Millimeters)
