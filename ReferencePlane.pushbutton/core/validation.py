# -*- coding: utf-8 -*-
"""
validation.py — Pre-flight validation checks for Reference Plane.pushbutton.

Reference Plane.pushbutton / core/
Urbana Bridge Generator — Revit 2024.3 / pyRevit 6.4.0 / IronPython 2.7

Public API:
    required_global_parameters_present(doc, names) -> (bool, list_of_missing)
    active_view_is_supported(doc)                   -> (bool, str reason)

Python compatibility: IronPython 2.7 — no f-strings, no dataclasses.
"""

import clr
clr.AddReference("RevitAPI")

from Autodesk.Revit.DB import (
    GlobalParametersManager,
    ViewPlan,
    ViewType,
)

from core.revit_compat import element_id_value

from core.logging_utils import get_logger


def required_global_parameters_present(doc, names):
    """Check that all named Global Parameters exist in the document.

    Args:
        doc   (Document): Active Revit document.
        names (list):     Exact parameter names to check.

    Returns:
        tuple: (all_present: bool, missing_names: list)
    """
    logger  = get_logger()
    missing = []

    for name in names:
        gp_id = GlobalParametersManager.FindByName(doc, name)
        if gp_id is None or element_id_value(gp_id) == -1:
            missing.append(name)

    if missing:
        logger.warning(
            "Required Global Parameters are missing for Tab 2",
            missing=missing,
        )
        return False, missing

    logger.info("All required Global Parameters present — Tab 2 pre-flight passed")
    return True, []


def active_view_is_supported(doc):
    """Check that the active view can host Reference Planes and Dimensions.

    Validates that the view is a ViewPlan, and specifically either a
    Floor Plan or Structural Plan (EngineeringPlan).

    Args:
        doc (Document): Active Revit document.

    Returns:
        tuple: (is_supported: bool, reason: str)
    """
    logger = get_logger()
    view   = doc.ActiveView

    if view is None:
        reason = (
            "No active view found. "
            "The Urbana Bridge Setup reference-plane operation must be run from a supported plan view. "
            "Please open a Floor Plan or Structural Plan and try again."
        )
        logger.warning(reason)
        return False, reason

    # 1st-level structural test: Is it a plan view class?
    if not isinstance(view, ViewPlan):
        reason = (
            "The active view '{0}' is not a plan view. "
            "The Urbana Bridge Setup reference-plane operation must be run from a supported plan view. "
            "Please open a Floor Plan or Structural Plan and try again."
        ).format(view.Name)
        logger.warning(reason, view_name=view.Name)
        return False, reason

    # 2nd-level test: Distinguish between allowed plan types (Floor/Structural)
    # and rejected plan types (Ceiling/Area).
    # Note: Structural Plans use ViewType.EngineeringPlan in the Revit API.
    supported_plan_types = {ViewType.FloorPlan, ViewType.EngineeringPlan}

    if view.ViewType not in supported_plan_types:
        reason = (
            "The active view '{0}' is an unsupported plan type ({1}). "
            "The Urbana Bridge Setup reference-plane operation must be run from a supported plan view. "
            "Please open a Floor Plan or Structural Plan and try again."
        ).format(view.Name, view.ViewType)
        logger.warning(reason, view_name=view.Name, view_type=str(view.ViewType))
        return False, reason

    logger.info(
        "Active view validated",
        view_name=view.Name,
        view_type=str(view.ViewType),
    )
    return True, "Active view '{0}' is supported.".format(view.Name)
