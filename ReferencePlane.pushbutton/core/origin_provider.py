# -*- coding: utf-8 -*-
"""
origin_provider.py — Resolve bridge center from Revit's built-in reference points.

Reference Plane.pushbutton / core/
Urbana Bridge Generator — Revit 2024.3 / pyRevit 6.4.0 / IronPython 2.7

Resolution order (ARCHITECTURE.md §7 / DESIGN.md §2):
    1. Project Base Point  (BasePoint.GetProjectBasePoint)
    2. Survey Point        (BasePoint.GetSurveyPoint)
    3. Internal Origin     XYZ(0, 0, 0)

The chosen source is logged every run so it is auditable and never silently
changes between sessions.

Public API:
    get_bridge_center(doc) -> (XYZ, str)
        XYZ  — bridge center in Revit internal coordinates
        str  — human-readable description of the source used

Python compatibility: IronPython 2.7 — no f-strings, no dataclasses.
"""

import clr
clr.AddReference("RevitAPI")

from Autodesk.Revit.DB import BasePoint, XYZ

from core.logging_utils import get_logger


def get_bridge_center(doc):
    """Return the bridge's logical center point and a description of its source.

    The center point is the intersection of the central horizontal reference
    plane and the central vertical reference plane.  Both planes share this
    single logical point, measured along perpendicular directions.

    Resolution order:
        1. Project Base Point position
        2. Survey Point position
        3. Internal Origin (0, 0, 0)

    Args:
        doc (Document): Active Revit document.

    Returns:
        tuple: (XYZ center_point, str source_description)
    """
    logger = get_logger()

    # --- Attempt 1: Project Base Point ---
    try:
        pbp = BasePoint.GetProjectBasePoint(doc)
        if pbp is not None:
            pos = pbp.Location.Point
            if pos is not None:
                source = "Project Base Point"
                logger.info(
                    "Bridge center resolved",
                    source=source,
                    x_internal=pos.X,
                    y_internal=pos.Y,
                    z_internal=pos.Z,
                )
                return pos, source
    except Exception as exc:
        logger.warning(
            "Could not read Project Base Point — trying Survey Point",
            exc=str(exc),
        )

    # --- Attempt 2: Survey Point ---
    try:
        sp = BasePoint.GetSurveyPoint(doc)
        if sp is not None:
            pos = sp.Location.Point
            if pos is not None:
                source = "Survey Point"
                logger.info(
                    "Bridge center resolved",
                    source=source,
                    x_internal=pos.X,
                    y_internal=pos.Y,
                    z_internal=pos.Z,
                )
                return pos, source
    except Exception as exc:
        logger.warning(
            "Could not read Survey Point — falling back to Internal Origin",
            exc=str(exc),
        )

    # --- Fallback: Internal Origin ---
    pos    = XYZ(0.0, 0.0, 0.0)
    source = "Internal Origin (0,0,0)"
    logger.info(
        "Bridge center resolved",
        source=source,
        x_internal=0.0,
        y_internal=0.0,
        z_internal=0.0,
    )
    return pos, source
