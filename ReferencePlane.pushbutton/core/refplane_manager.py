# -*- coding: utf-8 -*-
"""
refplane_manager.py — Idempotent Reference Plane create/ensure.

Reference Plane.pushbutton / core/
Urbana Bridge Generator — Revit 2024.3 / pyRevit 6.4.0 / IronPython 2.7

Creates the bridge's parametric reference-plane skeleton around a given center
point (from origin_provider.get_bridge_center).

Horizontal planes (9 total) — transverse / width control:
    Center                      (offset 0)
    Vertical Joist Bounding Top / Bottom   (± Vertical Joist Bounding Distance / 2)
    Bearer Top / Bottom                    (± Bearers Length / 2)
    Clear Span Top / Bottom                (± Clear Span / 2)
    Abutment Top / Bottom                  (± Abutment Width / 2)

Vertical planes — longitudinal / length control:
  NORMAL CRNK (7 planes):
    Left End / Right End         (± Length / 2)
    Left Crank / Right Crank     (± Crank Length from center)
    Left Intermediate / Right Intermediate  (bisect End↔Crank on each side)
    Center (longitudinal)        (offset 0)
  SPECIAL 2/2 (5 planes — when Crank Length = 0):
    Left End / Right End         (± Length / 2)
    Left Intermediate / Right Intermediate  (± Length / 4 from center)
    Center                       (offset 0)
    No Left Crank or Right Crank planes.

Idempotency: planes are identified by their exact Name property.
    FilteredElementCollector(doc).OfClass(ReferencePlane) filtered by .Name.
    Only planes matching our exact deterministic names are touched.

Topology transitions:
    2/2 → 3-part: Left Crank and Right Crank are created fresh.
    3-part → 2/2: Left Crank and Right Crank are removed by exact name
                  (these names are only used by automation, never by users).

Dynamic visible extents:
    Horizontal planes: max(HORIZ_PLANE_EXTENT_MM, Length + 4000) so they
                       are always visible for a 24m bridge.
    Vertical planes:   VERT_PLANE_EXTENT_MM (fixed — bridge width is small).

Public API:
    ensure_horizontal_set(doc, center_xyz, gp_values) -> list of status dicts
    ensure_vertical_set(doc, center_xyz, gp_values,
                        is_special_22=False)       -> list of status dicts

Python compatibility: IronPython 2.7 — no f-strings, no dataclasses.
"""

import clr
clr.AddReference("RevitAPI")

from Autodesk.Revit.DB import (
    FilteredElementCollector,
    ReferencePlane,
    XYZ,
    ElementTransformUtils,
)

from core.revit_compat import element_id_value
from core.units import mm_to_internal
from core.param_spec import HORIZ_PLANE_EXTENT_MM, VERT_PLANE_EXTENT_MM
from core.logging_utils import get_logger


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _get_existing_planes_by_name(doc):
    """Return dict {name: ReferencePlane element} for all planes in the doc."""
    planes = {}
    for rp in FilteredElementCollector(doc).OfClass(ReferencePlane).ToElements():
        n = rp.Name
        if n:
            planes[n] = rp
    return planes


def _create_horizontal_plane(doc, view, name, y_offset_internal, half_extent_internal, center_z_internal):
    """Create a horizontal reference plane (runs left-right in plan) at y_offset.

    In Revit plan view:
        - A "horizontal" line runs along the X direction.
        - Normal is along +Y.
        - The plane is offset from center in the Y direction.

    Args:
        y_offset_internal: offset from center in internal units (+ = up/north in plan)
        half_extent_internal: half-length of the visible line in internal units
        center_z_internal: elevation of the bridge center
    """
    bubble_end = XYZ(-half_extent_internal, y_offset_internal, center_z_internal)
    free_end   = XYZ( half_extent_internal, y_offset_internal, center_z_internal)
    cut_vec    = XYZ(0.0, 0.0, 1.0)

    rp = doc.Create.NewReferencePlane(bubble_end, free_end, cut_vec, view)
    rp.Name = name
    return rp


def _create_vertical_plane(doc, view, name, x_offset_internal, half_extent_internal, center_z_internal):
    """Create a vertical reference plane (runs up-down in plan) at x_offset.

    In Revit plan view:
        - A "vertical" line runs along the Y direction.
        - Normal is along +X.
        - The plane is offset from center in the X direction.
    """
    bubble_end = XYZ(x_offset_internal, -half_extent_internal, center_z_internal)
    free_end   = XYZ(x_offset_internal,  half_extent_internal, center_z_internal)
    cut_vec    = XYZ(0.0, 0.0, 1.0)

    rp = doc.Create.NewReferencePlane(bubble_end, free_end, cut_vec, view)
    rp.Name = name
    return rp


def _status(name, action, detail="", elem_id=None):
    return {"name": name, "status": action, "detail": detail, "id": elem_id}


def _is_valid_orientation(rp, is_horizontal):
    """Check if the reference plane has the correct vertical 3D orientation for plan visibility.
    
    A valid plan reference line must be a vertical plane in 3D space, meaning its Normal
    must be entirely horizontal (Z = 0). The old broken planes were created with Normal = Z, 
    making them flat on the ground.
    """
    try:
        plane = rp.GetPlane()
        normal = plane.Normal
        
        # If the normal points up/down (Z is close to 1 or -1), it's a flat horizontal plane.
        # It MUST be near 0 for the plane to be vertical.
        if abs(normal.Z) > 0.01:
            return False
            
        if is_horizontal:
            # A horizontal line in plan (runs along X) must have a normal along Y
            if abs(normal.Y) < 0.99:
                return False
        else:
            # A vertical line in plan (runs along Y) must have a normal along X
            if abs(normal.X) < 0.99:
                return False
                
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def ensure_horizontal_set(doc, center_xyz, gp_values):
    """Create or confirm the 9 horizontal (width-control) reference planes.

    Args:
        doc        (Document): Active Revit document.
        center_xyz (XYZ):      Bridge logical center (from origin_provider).
        gp_values  (dict):     {gp_name: value_mm} for all live GP values.
                               Keys needed: "Vertical Joist Bounding Distance",
                               "Bearers Length", "Clear Span", "Abutment Width".

    Returns:
        list of status dicts: [{name, status, detail}, ...]
    """
    logger  = get_logger()
    results = []
    view    = doc.ActiveView

    existing = _get_existing_planes_by_name(doc)

    half_extent_mm = max(
        HORIZ_PLANE_EXTENT_MM / 2.0,
        gp_values.get("Length", 0.0) / 2.0 + 2000.0,
    )
    half_extent = mm_to_internal(half_extent_mm)
    cy          = center_xyz.Y  # center offset in Y (internal units)
    cz          = center_xyz.Z  # center elevation

    # Pair definitions: (base_name, top_name, bottom_name, gp_key)
    pairs = [
        ("Vertical Joist Bounding", "Vertical Joist Bounding Top",
         "Vertical Joist Bounding Bottom", "Vertical Joist Bounding Distance"),
        ("Bearers",                 "Bearer Top",
         "Bearer Bottom",           "Bearers Length"),
        ("Clear Span",              "Clear Span Top",
         "Clear Span Bottom",       "Clear Span"),
        ("Abutment",                "Abutment Top",
         "Abutment Bottom",         "Abutment Width"),
    ]

    # Center plane (H_Center — disambiguated from longitudinal V_Center)
    center_name = "H_Center"
    if center_name in existing:
        rp_existing = existing[center_name]
        if _is_valid_orientation(rp_existing, is_horizontal=True):
            current_y = rp_existing.GetPlane().Origin.Y
            delta_y = cy - current_y
            if abs(delta_y) > 1e-5:
                try:
                    ElementTransformUtils.MoveElement(doc, rp_existing.Id, XYZ(0.0, delta_y, 0.0))
                    results.append(_status(center_name, "Updated",
                                           "Moved to correct Y offset",
                                           rp_existing.Id))
                    logger.info("H_Center moved to correct Y", delta_y_internal=delta_y)
                except Exception as exc:
                    results.append(_status(center_name, "Error", str(exc)))
            else:
                results.append(_status(center_name, "Existing",
                                       "Horizontal center plane already present and valid",
                                       rp_existing.Id))
                logger.info("H_Center already exists and valid — skipping creation")
        else:
            try:
                doc.Delete(rp_existing.Id)
                rp = _create_horizontal_plane(doc, view, center_name, cy, half_extent, cz)
                results.append(_status(center_name, "Existing - Repaired",
                                       "Old broken geometry detected and repaired",
                                       rp.Id))
                logger.info("H_Center repaired", y_internal=cy)
            except Exception as exc:
                results.append(_status(center_name, "Error", str(exc)))
                logger.error("Failed to repair H_Center", exc=exc)
    else:
        try:
            rp = _create_horizontal_plane(doc, view, center_name, cy, half_extent, cz)
            results.append(_status(center_name, "Created",
                                   "Horizontal center plane created at Y=0 (center)",
                                   rp.Id))
            logger.info("H_Center created", y_internal=cy)
        except Exception as exc:
            results.append(_status(center_name, "Error", str(exc)))
            logger.error("Failed to create H_Center", exc=exc)

    # 4 symmetric pairs
    for base, top_name, bot_name, gp_key in pairs:
        half_mm = gp_values.get(gp_key, 0.0) / 2.0
        half_int = mm_to_internal(half_mm)

        y_top = cy + half_int
        y_bot = cy - half_int

        for (plane_name, y_val) in [(top_name, y_top), (bot_name, y_bot)]:
            if plane_name in existing:
                rp_existing = existing[plane_name]
                if _is_valid_orientation(rp_existing, is_horizontal=True):
                    current_y = rp_existing.GetPlane().Origin.Y
                    delta_y = y_val - current_y
                    if abs(delta_y) > 1e-5:
                        try:
                            ElementTransformUtils.MoveElement(doc, rp_existing.Id, XYZ(0.0, delta_y, 0.0))
                            results.append(_status(plane_name, "Updated",
                                                   "Moved to offset ±{0:.1f} mm".format(half_mm),
                                                   rp_existing.Id))
                            logger.info("Plane moved", name=plane_name, delta_y_internal=delta_y)
                        except Exception as exc:
                            results.append(_status(plane_name, "Error", str(exc)))
                    else:
                        results.append(_status(plane_name, "Existing",
                                               "Already present at offset ±{0:.1f} mm".format(half_mm),
                                               rp_existing.Id))
                        logger.info("Plane already exists and valid — skipping", name=plane_name)
                else:
                    try:
                        doc.Delete(rp_existing.Id)
                        rp = _create_horizontal_plane(doc, view, plane_name, y_val, half_extent, cz)
                        results.append(_status(
                            plane_name, "Existing - Repaired",
                            "Old broken geometry detected and repaired",
                            rp.Id
                        ))
                        logger.info("Horizontal plane repaired", name=plane_name, y_internal=y_val)
                    except Exception as exc:
                        results.append(_status(plane_name, "Error", str(exc)))
                        logger.error("Failed to repair plane", name=plane_name, exc=exc)
            else:
                try:
                    rp = _create_horizontal_plane(doc, view, plane_name, y_val, half_extent, cz)
                    results.append(_status(
                        plane_name, "Created",
                        "Created at Y offset {0:.2f} mm from center".format(
                            half_mm if "Top" in plane_name else -half_mm
                        ),
                        rp.Id
                    ))
                    logger.info("Horizontal plane created", name=plane_name, y_internal=y_val)
                except Exception as exc:
                    results.append(_status(plane_name, "Error", str(exc)))
                    logger.error("Failed to create plane", name=plane_name, exc=exc)

    return results


def _remove_planes_by_name(doc, names):
    """Delete automation-owned reference planes identified by exact name.

    Only the names supplied are targeted.  User-created planes with other
    names are never touched.

    These names ("Left Crank", "Right Crank") are exclusively used by this
    automation and are safe to remove when switching to the 2/2 topology.

    Args:
        doc   (Document): Active Revit document.
        names (list):     Exact plane names to delete.

    Returns:
        list: Names of planes that were successfully removed.
    """
    logger   = get_logger()
    existing = _get_existing_planes_by_name(doc)
    removed  = []
    for name in names:
        rp = existing.get(name)
        if rp is not None:
            try:
                doc.Delete(rp.Id)
                removed.append(name)
                logger.info("Removed automation plane for topology transition", name=name)
            except Exception as exc:
                logger.warning(
                    "Could not remove plane during topology transition",
                    name=name,
                    exc=str(exc),
                )
    return removed


def ensure_vertical_set(doc, center_xyz, gp_values, is_special_22=False):
    """Create or confirm the vertical (length-control) reference planes.

    For normal CRNK configurations (A/B/C) creates 7 planes:
        Left End, Left Intermediate, Left Crank, V_Center,
        Right Crank, Right Intermediate, Right End

    For the special 2/2 configuration (is_special_22=True) creates 5 planes:
        Left End, Left Intermediate, V_Center,
        Right Intermediate, Right End

    When switching from normal to 2/2, removes Left Crank and Right Crank
    if they exist (these names are only used by automation).

    Args:
        doc           (Document): Active Revit document.
        center_xyz    (XYZ):      Bridge logical center.
        gp_values     (dict):     Keys needed: "Length", and for normal: "Crank Length".
        is_special_22 (bool):     True for the 4m CRNK-2/2 special topology.

    Returns:
        list of status dicts: [{name, status, detail}, ...]
    """
    logger  = get_logger()
    results = []
    view    = doc.ActiveView

    existing = _get_existing_planes_by_name(doc)

    half_extent = mm_to_internal(VERT_PLANE_EXTENT_MM / 2.0)
    cx          = center_xyz.X   # center offset in X (internal units)
    cz          = center_xyz.Z   # center elevation

    length_mm     = gp_values.get("Length", 12000.0)
    half_length_mm = length_mm / 2.0

    if is_special_22:
        # Special 2/2 topology: remove crank planes if they exist from a
        # previous normal configuration, then create the 5-plane layout.
        removed = _remove_planes_by_name(doc, ["Left Crank", "Right Crank"])
        if removed:
            logger.info(
                "Removed crank planes for 2/2 topology transition",
                removed=removed,
            )
            # Refresh existing map after deletion
            existing = _get_existing_planes_by_name(doc)

        # Left Intermediate and Right Intermediate are at ±Length/4 from center
        # (midpoint between the End and V_Center)
        half_intermed_mm = half_length_mm / 2.0

        plane_defs = [
            ("Left End",           -half_length_mm),
            ("Left Intermediate",  -half_intermed_mm),
            ("V_Center",            0.0),
            ("Right Intermediate",  half_intermed_mm),
            ("Right End",           half_length_mm),
        ]
        logger.info(
            "Using 2/2 special five-plane topology",
            length_mm=length_mm,
            half_intermed_mm=half_intermed_mm,
        )
    else:
        # Normal 3-part topology: 7 planes.
        crank_mm      = gp_values.get("Crank Length", 2000.0)
        # Intermediate planes bisect the zone between End and Crank on each side.
        intermed_mm   = (half_length_mm + crank_mm) / 2.0

        plane_defs = [
            ("Left End",           -half_length_mm),
            ("Left Intermediate",  -intermed_mm),
            ("Left Crank",         -crank_mm),
            ("V_Center",            0.0),
            ("Right Crank",         crank_mm),
            ("Right Intermediate",  intermed_mm),
            ("Right End",           half_length_mm),
        ]
        logger.info(
            "Using normal 7-plane topology",
            length_mm=length_mm,
            crank_mm=crank_mm,
        )

    for (plane_name, x_offset_mm) in plane_defs:
        x_val = cx + mm_to_internal(x_offset_mm)

        if plane_name in existing:
            rp_existing = existing[plane_name]
            if _is_valid_orientation(rp_existing, is_horizontal=False):
                current_x = rp_existing.GetPlane().Origin.X
                delta_x = x_val - current_x
                if abs(delta_x) > 1e-5:
                    try:
                        ElementTransformUtils.MoveElement(doc, rp_existing.Id, XYZ(delta_x, 0.0, 0.0))
                        results.append(_status(plane_name, "Updated",
                                               "Moved to X offset {0:.1f} mm".format(x_offset_mm),
                                               rp_existing.Id))
                        logger.info("Vertical plane moved", name=plane_name, delta_x_internal=delta_x)
                    except Exception as exc:
                        results.append(_status(plane_name, "Error", str(exc)))
                else:
                    results.append(_status(plane_name, "Existing",
                                           "Already present at X offset {0:.1f} mm".format(x_offset_mm),
                                           rp_existing.Id))
                    logger.info("Plane already exists and valid — skipping", name=plane_name)
            else:
                try:
                    doc.Delete(rp_existing.Id)
                    rp = _create_vertical_plane(doc, view, plane_name, x_val, half_extent, cz)
                    results.append(_status(
                        plane_name, "Existing - Repaired",
                        "Old broken geometry detected and repaired",
                        rp.Id
                    ))
                    logger.info("Vertical plane repaired", name=plane_name, x_internal=x_val)
                except Exception as exc:
                    results.append(_status(plane_name, "Error", str(exc)))
                    logger.error("Failed to repair plane", name=plane_name, exc=exc)
        else:
            try:
                rp = _create_vertical_plane(doc, view, plane_name, x_val, half_extent, cz)
                results.append(_status(
                    plane_name, "Created",
                    "Created at X offset {0:.1f} mm from center".format(x_offset_mm),
                    rp.Id
                ))
                logger.info("Vertical plane created", name=plane_name, x_internal=x_val)
            except Exception as exc:
                results.append(_status(plane_name, "Error", str(exc)))
                logger.error("Failed to create plane", name=plane_name, exc=exc)

    return results


def read_gp_values_mm(doc, gp_names):
    """Read current values of named Global Parameters, returning {name: mm}.

    Used to compute plane positions from live GP values at run time.
    Falls back to 0.0 and logs a warning if a parameter cannot be read.

    Revit 2024 API note:
        gp.GetValue() always returns DoubleParameterValue with the evaluated
        result, regardless of whether the GP is formula-driven or a fixed value.
        FormulaParameterValue does not exist in Revit 2024.

    Args:
        doc      (Document): Active Revit document.
        gp_names (list):     Parameter names to read.

    Returns:
        dict: {name: float_mm}
    """
    from Autodesk.Revit.DB import (
        GlobalParametersManager,
        DoubleParameterValue,
    )
    from core.units import internal_to_mm
    logger = get_logger()
    values = {}
    for name in gp_names:
        gp_id = GlobalParametersManager.FindByName(doc, name)
        if gp_id is None or element_id_value(gp_id) == -1:
            logger.warning("GP not found when reading value", name=name)
            values[name] = None
            continue
        gp = doc.GetElement(gp_id)
        try:
            val = gp.GetValue()
            if isinstance(val, DoubleParameterValue):
                # In Revit 2024, GetValue() always returns DoubleParameterValue
                # with the evaluated result (covers both fixed values and formulas).
                values[name] = internal_to_mm(val.Value)
            else:
                logger.warning(
                    "Unexpected GP value type — defaulting to 0.0",
                    name=name,
                    val_type=type(val).__name__,
                )
                values[name] = 0.0
        except Exception as exc:
            logger.error("Could not read GP value", name=name, exc=exc)
            values[name] = 0.0
    return values
