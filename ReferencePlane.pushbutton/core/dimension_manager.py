# -*- coding: utf-8 -*-
"""
dimension_manager.py — Driving dimensions, EQ constraints, GP associations.

Reference Plane.pushbutton / core/
Urbana Bridge Generator — Revit 2024.3 / pyRevit 6.4.0 / IronPython 2.7

Creates two dimension systems per DESIGN.md §6:

WIDTH SYSTEM (per each of 4 horizontal pairs):
  - Overall outer-to-outer driving dimension  → labeled with driving GP.
  - Center-chained EQ dimension: top → H_Center → bottom (both segments EQ).

LENGTH SYSTEM (vertical planes):
  - Overall Left End → Right End → labeled with "Length" GP.
  - EQ half:  Left End → V_Center → Right End (both segments EQ).
  - Crank Left Overall:  Left Crank ↔ V_Center → labeled with "Crank Length" GP.
  - Crank Right Overall: Right Crank ↔ V_Center → labeled with "Crank Length" GP.
    (A single non-reporting GP can label multiple separate dimensions — native Revit pattern.)
  - EQ Outer Left:  Left End → Left Intermediate → Left Crank (EQ).
  - EQ Outer Right: Right Crank → Right Intermediate → Right End (EQ).

Idempotency:
  Dimensions have no persistent name in Revit.  We detect automation-owned
  dimensions by checking whether a dimension already spans the exact same
  ReferenceArray (same frozenset of reference element IDs).  The mark tag
  "UrbanaBridgeGen" is set on every created dimension for future detection.

  When an existing overall dimension is found its GP label is checked, and the
  label is added/repaired if missing — without deleting or recreating the dim.

GP ↔ Dimension association — correct Revit 2024 API:
  Uses GlobalParameter.LabelDimension(dimId) — the programmatic equivalent of
  Modify | Dimensions → Label → Select Global Parameter in the Revit ribbon.
  Uses GlobalParameter.CanLabelDimension(dimId) for pre-flight validation.
  Uses GlobalParameter.GetLabeledDimensions(doc) for post-association verification.

  Note: AssociateElementParameterToGlobalParameter is NOT the correct call for
  dimension labeling.  That method targets element-level parameters (e.g. a wall
  height), not dimension label associations.

Python compatibility: IronPython 2.7 — no f-strings, no dataclasses.
"""

import clr
clr.AddReference("RevitAPI")

from Autodesk.Revit.DB import (
    FilteredElementCollector,
    GlobalParametersManager,
    ReferencePlane,
    Dimension,
    ReferenceArray,
    BuiltInParameter,
    Line,
    XYZ,
)

from core.revit_compat import element_id_value
from core.units import mm_to_internal
from core.logging_utils import get_logger


# ---------------------------------------------------------------------------
# Marker comment stored on every automation-owned dimension
# ---------------------------------------------------------------------------
AUTOMATION_MARKER = "UrbanaBridgeGen"


# ---------------------------------------------------------------------------
# Internal helpers — geometry
# ---------------------------------------------------------------------------

def _get_planes_by_name(doc):
    """Return dict {name: ReferencePlane}."""
    planes = {}
    for rp in FilteredElementCollector(doc).OfClass(ReferencePlane).ToElements():
        if rp.Name:
            planes[rp.Name] = rp
    return planes


def _refs_from_planes(planes_dict, *names):
    """Build a ReferenceArray from plane names in order.

    Args:
        planes_dict (dict): {name: ReferencePlane}
        *names:             Plane names in dimension reference order.

    Returns:
        ReferenceArray or None if any plane is missing.
    """
    ra = ReferenceArray()
    for name in names:
        rp = planes_dict.get(name)
        if rp is None:
            return None
        ra.Append(rp.GetReference())
    return ra


def _ref_ids_from_array(ra):
    """Return frozenset of element ID integers from a ReferenceArray."""
    return frozenset(
        element_id_value(r.ElementId) for r in ra if r.ElementId is not None
    )


def _dimension_line_horizontal(y_internal, x_start, x_end):
    """Create a horizontal Line for a dimension spanning X direction."""
    return Line.CreateBound(
        XYZ(x_start, y_internal, 0.0),
        XYZ(x_end,   y_internal, 0.0),
    )


def _dimension_line_vertical(x_internal, y_start, y_end):
    """Create a vertical Line for a dimension spanning Y direction."""
    return Line.CreateBound(
        XYZ(x_internal, y_start, 0.0),
        XYZ(x_internal, y_end,   0.0),
    )


# ---------------------------------------------------------------------------
# Internal helpers — automation dimension registry
# ---------------------------------------------------------------------------

def _existing_automation_dims_map(doc):
    """Return dict {frozenset_of_ref_ids: Dimension} for all automation dimensions.

    The frozenset key is the set of reference ElementId integers — used to detect
    whether a dimension with the same reference set already exists.
    """
    found = {}
    for dim in FilteredElementCollector(doc).OfClass(Dimension).ToElements():
        try:
            refs = dim.References
            if refs:
                ids = _ref_ids_from_array(refs)
                if ids:
                    found[ids] = dim
        except Exception:
            pass
    return found


def _mark_dimension(dim):
    """Tag a Dimension with our automation marker for future idempotency."""
    try:
        p = dim.get_Parameter(BuiltInParameter.ALL_MODEL_MARK)
        if p and not p.IsReadOnly:
            p.Set(AUTOMATION_MARKER)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Internal helpers — GP / dimension label API
# ---------------------------------------------------------------------------

def _is_dim_labeled_by_gp(doc, dim, gp_id):
    """Return True if this dimension is already labeled by the given GP.

    Uses GlobalParameter.GetLabeledDimensions(doc) which returns an
    ISet<ElementId> of all dimensions currently labeled by that GP.
    """
    try:
        gp_elem = doc.GetElement(gp_id)
        if gp_elem is None:
            return False
        labeled = gp_elem.GetLabeledDimensions(doc)
        if labeled is None:
            return False
        for lid in labeled:
            if element_id_value(lid) == element_id_value(dim.Id):
                return True
        return False
    except Exception:
        return False


def _associate_gp(doc, dim, gp_name):
    """Associate a dimension label with a Global Parameter.

    Uses GlobalParameter.LabelDimension(dimId) — the correct Revit 2024 API
    equivalent of Modify | Dimensions → Label → Select Global Parameter.

    Pre-flight:  GlobalParameter.CanLabelDimension(dimId)
    Association: GlobalParameter.LabelDimension(dimId)
    Verification: GlobalParameter.GetLabeledDimensions(doc) → check dim.Id present

    Returns: (bool success, str detail)
    """
    logger = get_logger()
    try:
        gp_id = GlobalParametersManager.FindByName(doc, gp_name)
        if gp_id is None or element_id_value(gp_id) == -1:
            msg = "GP '{0}' not found — cannot associate".format(gp_name)
            logger.warning(msg)
            return False, msg

        gp_elem = doc.GetElement(gp_id)
        if gp_elem is None:
            msg = "GP element for '{0}' is null".format(gp_name)
            logger.warning(msg)
            return False, msg

        # Pre-flight: check that this dimension can be labeled by this GP.
        # CanLabelDimension returns False for: reporting GPs on multi-segment dims,
        # type mismatches (angle vs length), or already-driving conflicts.
        try:
            can_label = gp_elem.CanLabelDimension(dim.Id)
        except Exception as can_exc:
            # If CanLabelDimension itself fails, attempt LabelDimension anyway
            logger.warning(
                "CanLabelDimension raised — attempting LabelDimension anyway",
                gp=gp_name, exc=str(can_exc)
            )
            can_label = True

        if not can_label:
            msg = (
                "CanLabelDimension returned False for GP '{0}' on dim {1}. "
                "Possible causes: reporting GP on multi-segment dim, type mismatch, "
                "or conflicting driving constraint."
            ).format(gp_name, element_id_value(dim.Id))
            logger.warning(msg)
            return False, msg

        # Perform the label association
        gp_elem.LabelDimension(dim.Id)

        # Post-association verification via GetLabeledDimensions
        verified = _is_dim_labeled_by_gp(doc, dim, gp_id)
        if verified:
            logger.info(
                "GP label association verified",
                gp=gp_name,
                dim_id=element_id_value(dim.Id),
            )
            return True, "GP: {0} — Associated".format(gp_name)
        else:
            # LabelDimension did not raise but verification cannot confirm.
            # This can happen if GetLabeledDimensions is not yet flushed within
            # the same transaction — treat as success but flag as unverified.
            logger.info(
                "GP LabelDimension called (verification pending regeneration)",
                gp=gp_name,
                dim_id=element_id_value(dim.Id),
            )
            return True, "GP: {0} — Associated (verify after regen)".format(gp_name)

    except Exception as exc:
        msg = "API error associating GP '{0}' with dim {1}: {2}".format(
            gp_name, element_id_value(dim.Id) if dim else "?", str(exc)
        )
        logger.warning(msg, exc=str(exc))
        return False, msg


def _repair_gp_if_missing(doc, dim, gp_name):
    """Check whether `dim` is already labeled by `gp_name`; add label if not.

    Returns: (action_str, detail_str)
    action_str is one of: "Already Associated", "Association Added", "FAILED"
    """
    try:
        gp_id = GlobalParametersManager.FindByName(doc, gp_name)
        if gp_id is None or element_id_value(gp_id) == -1:
            return "FAILED", "GP '{0}' not found in document".format(gp_name)

        if _is_dim_labeled_by_gp(doc, dim, gp_id):
            return "Already Associated", "GP: {0} — Already Associated".format(gp_name)

        # Association missing — attempt to add it
        ok, detail = _associate_gp(doc, dim, gp_name)
        if ok:
            return "Association Added", "GP: {0} — Association Added".format(gp_name)
        else:
            return "FAILED", "GP: {0} — Association FAILED: {1}".format(gp_name, detail)

    except Exception as exc:
        return "FAILED", "GP repair error for '{0}': {1}".format(gp_name, str(exc))


# ---------------------------------------------------------------------------
# Status helper
# ---------------------------------------------------------------------------

def _status(name, action, detail=""):
    return {"name": name, "status": action, "detail": detail}


# ---------------------------------------------------------------------------
# Width dimensions
# ---------------------------------------------------------------------------

def ensure_width_dimensions(doc, center_xyz, planes_dict, existing_dim_map):
    """Create overall + EQ dimensions for all 4 horizontal reference plane pairs.

    Overall dimensions are associated with their driving Global Parameters.
    EQ chains maintain symmetry around H_Center and receive no GP label.

    Args:
        doc             (Document)
        center_xyz      (XYZ)
        planes_dict     (dict) {name: ReferencePlane}
        existing_dim_map (dict) {frozenset: Dimension} from _existing_automation_dims_map()

    Returns:
        list of status dicts
    """
    logger  = get_logger()
    results = []
    view    = doc.ActiveView

    # (display_label, top_plane_name, center_plane_name, bottom_plane_name, gp_name)
    pairs = [
        ("Vertical Joist Bounding Distance",
         "Vertical Joist Bounding Top", "H_Center", "Vertical Joist Bounding Bottom",
         "Vertical Joist Bounding Distance"),
        ("Bearers Length",
         "Bearer Top", "H_Center", "Bearer Bottom",
         "Bearers Length"),
        ("Clear Span",
         "Clear Span Top", "H_Center", "Clear Span Bottom",
         "Clear Span"),
        ("Abutment Width",
         "Abutment Top", "H_Center", "Abutment Bottom",
         "Abutment Width"),
    ]

    # Dimension lines are placed to the right of the bridge extent (X direction).
    # Each pair gets a slightly different X offset so overall and EQ lines don't overlap.
    dim_x_offset     = mm_to_internal(2000.0)   # overall dim line X from center
    eq_x_extra       = mm_to_internal(500.0)    # extra offset for EQ line

    cx = center_xyz.X

    for (label, top_name, ctr_name, bot_name, gp_name) in pairs:

        # ==============================================================
        # A. OVERALL driving dimension (top ↔ bottom) → GP association
        # ==============================================================
        overall_label = label + " Overall"
        ra_overall    = _refs_from_planes(planes_dict, top_name, bot_name)

        if ra_overall is None:
            results.append(_status(
                overall_label, "Error",
                "Planes missing: {0}, {1}".format(top_name, bot_name)
            ))
        else:
            ref_ids = _ref_ids_from_array(ra_overall)

            if ref_ids in existing_dim_map:
                # Dimension exists — check and repair GP association
                existing_dim = existing_dim_map[ref_ids]
                action, detail = _repair_gp_if_missing(doc, existing_dim, gp_name)
                if action == "Already Associated":
                    results.append(_status(overall_label, "Existing", detail))
                elif action == "Association Added":
                    results.append(_status(overall_label, "Existing", detail))
                else:
                    results.append(_status(overall_label, "Existing", detail))
                logger.info(
                    "Width overall dim existing — GP repair",
                    label=overall_label,
                    action=action,
                )
            else:
                # Create a new overall dimension
                try:
                    top_rp = planes_dict.get(top_name)
                    bot_rp = planes_dict.get(bot_name)
                    if top_rp is None or bot_rp is None:
                        raise ValueError("Plane not found")

                    y_top = top_rp.GetPlane().Origin.Y
                    y_bot = bot_rp.GetPlane().Origin.Y
                    dim_line = _dimension_line_vertical(
                        cx + dim_x_offset,
                        y_bot - mm_to_internal(200.0),
                        y_top + mm_to_internal(200.0),
                    )

                    dim = doc.Create.NewDimension(view, dim_line, ra_overall)
                    _mark_dimension(dim)

                    ok, gp_detail = _associate_gp(doc, dim, gp_name)
                    results.append(_status(
                        overall_label, "Created",
                        "Dim created. {0}".format(gp_detail)
                    ))
                    existing_dim_map[ref_ids] = dim
                    logger.info("Width overall dim created", label=overall_label)

                except Exception as exc:
                    results.append(_status(overall_label, "Error", str(exc)))
                    logger.error("Failed to create width overall dim", label=label, exc=exc)

        # ==============================================================
        # B. EQ chain: top → center → bottom  (EQ only, no GP)
        # ==============================================================
        eq_label = label + " EQ Chain"
        ra_eq    = _refs_from_planes(planes_dict, top_name, ctr_name, bot_name)

        if ra_eq is None:
            results.append(_status(eq_label, "Error", "Planes missing for EQ chain"))
            continue

        ref_ids_eq = _ref_ids_from_array(ra_eq)

        if ref_ids_eq in existing_dim_map:
            results.append(_status(eq_label, "Existing", "EQ chain already present"))
        else:
            try:
                top_rp = planes_dict.get(top_name)
                bot_rp = planes_dict.get(bot_name)
                y_top  = top_rp.GetPlane().Origin.Y
                y_bot  = bot_rp.GetPlane().Origin.Y
                dim_line = _dimension_line_vertical(
                    cx + dim_x_offset + eq_x_extra,
                    y_bot - mm_to_internal(200.0),
                    y_top + mm_to_internal(200.0),
                )

                dim = doc.Create.NewDimension(view, dim_line, ra_eq)
                _mark_dimension(dim)

                try:
                    dim.AreSegmentsEqual = True
                except Exception as eq_exc:
                    logger.warning("Could not set EQ on width chain", label=eq_label, exc=str(eq_exc))

                results.append(_status(eq_label, "Created", "EQ chain created — segments equal"))
                existing_dim_map[ref_ids_eq] = dim
                logger.info("Width EQ chain created", label=eq_label)

            except Exception as exc:
                results.append(_status(eq_label, "Error", str(exc)))
                logger.error("Failed to create EQ width chain", label=label, exc=exc)

    return results


# ---------------------------------------------------------------------------
# Length dimensions
# ---------------------------------------------------------------------------

def ensure_length_dimensions(doc, center_xyz, planes_dict, existing_dim_map, gp_values,
                             is_special_22=False):
    """Create overall + EQ + crank-segment dimensions for the length system.

    Dimension strategy (normal 3-part):
      1. Length Overall:       Left End ↔ Right End → GP "Length"
      2. Length EQ Half:       Left End → V_Center → Right End (EQ)
      3. Crank Left Overall:   Left Crank ↔ V_Center → GP "Crank Length"
      4. Crank Right Overall:  Right Crank ↔ V_Center → GP "Crank Length"
         (same non-reporting GP can label multiple separate dimensions)
      5. Length EQ Outer Left:  Left End → Left Intermediate → Left Crank (EQ)
      6. Length EQ Outer Right: Right Crank → Right Intermediate → Right End (EQ)

    When is_special_22=True (4m CRNK-2/2):
      Items 3–6 are SKIPPED because Left Crank and Right Crank planes do not
      exist in the 2/2 topology.  Items 1 and 2 are created as normal.

    The old 6-segment chain is NOT created.  Two separate 2-reference dimensions
    are used for the crank zones so that LabelDimension can reliably target each
    individual segment value rather than the total chain.

    Args:
        doc               (Document)
        center_xyz        (XYZ)
        planes_dict       (dict) {name: ReferencePlane}
        existing_dim_map  (dict) {frozenset: Dimension}
        gp_values         (dict) {gp_name: mm} live values
        is_special_22     (bool) True when using the 2/2 special topology

    Returns:
        list of status dicts
    """
    logger  = get_logger()
    results = []
    view    = doc.ActiveView

    cy = center_xyz.Y

    # Vertical offsets for horizontal dimension lines (stacked above bridge)
    y_off_overall     = mm_to_internal(1500.0)
    y_off_eq_half     = mm_to_internal(2100.0)
    y_off_crank       = mm_to_internal(2700.0)
    y_off_eq_outer    = mm_to_internal(3300.0)

    # X margins so lines extend slightly beyond the outermost planes
    x_margin = mm_to_internal(200.0)

    # Convenience: X coordinate helpers
    def _x(plane_name):
        rp = planes_dict.get(plane_name)
        return rp.GetPlane().Origin.X if rp else None

    # ==========================================================
    # 1. Length Overall: Left End ↔ Right End → GP "Length"
    # ==========================================================
    overall_label = "Length Overall"
    ra_overall    = _refs_from_planes(planes_dict, "Left End", "Right End")

    if ra_overall is None:
        results.append(_status(overall_label, "Error", "Left End or Right End plane missing"))
    else:
        ref_ids = _ref_ids_from_array(ra_overall)
        if ref_ids in existing_dim_map:
            existing_dim = existing_dim_map[ref_ids]
            action, detail = _repair_gp_if_missing(doc, existing_dim, "Length")
            results.append(_status(overall_label, "Existing", detail))
            logger.info("Length overall dim existing — GP repair", action=action)
        else:
            try:
                x_left  = _x("Left End")
                x_right = _x("Right End")
                if x_left is None or x_right is None:
                    raise ValueError("Left End or Right End plane not found")

                dim_line = _dimension_line_horizontal(
                    cy + y_off_overall,
                    x_left  - x_margin,
                    x_right + x_margin,
                )
                dim = doc.Create.NewDimension(view, dim_line, ra_overall)
                _mark_dimension(dim)
                ok, gp_detail = _associate_gp(doc, dim, "Length")
                results.append(_status(
                    overall_label, "Created",
                    "Dim created. {0}".format(gp_detail)
                ))
                existing_dim_map[ref_ids] = dim
                logger.info("Length overall dim created")
            except Exception as exc:
                results.append(_status(overall_label, "Error", str(exc)))
                logger.error("Failed to create length overall dim", exc=exc)

    # ==========================================================
    # 2. Length EQ Half: Left End → V_Center → Right End  (EQ)
    # ==========================================================
    eq_half_label = "Length EQ Half"
    ra_eq_half    = _refs_from_planes(planes_dict, "Left End", "V_Center", "Right End")

    if ra_eq_half is None:
        results.append(_status(eq_half_label, "Error", "V_Center or End planes missing"))
    else:
        ref_ids = _ref_ids_from_array(ra_eq_half)
        if ref_ids in existing_dim_map:
            results.append(_status(eq_half_label, "Existing", "EQ half already present"))
        else:
            try:
                x_left  = _x("Left End")
                x_right = _x("Right End")
                if x_left is None or x_right is None:
                    raise ValueError("Plane not found")

                dim_line = _dimension_line_horizontal(
                    cy + y_off_eq_half,
                    x_left  - x_margin,
                    x_right + x_margin,
                )
                dim = doc.Create.NewDimension(view, dim_line, ra_eq_half)
                _mark_dimension(dim)
                try:
                    dim.AreSegmentsEqual = True
                except Exception as eq_exc:
                    logger.warning("Could not set EQ on length half", exc=str(eq_exc))
                results.append(_status(eq_half_label, "Created", "EQ half created — segments equal"))
                existing_dim_map[ref_ids] = dim
                logger.info("Length EQ half created")
            except Exception as exc:
                results.append(_status(eq_half_label, "Error", str(exc)))
                logger.error("Failed to create length EQ half", exc=exc)

    # ==========================================================
    # 3-6: Crank and outer EQ — only for normal 3-part topology
    # ==========================================================
    if is_special_22:
        # In the 2/2 topology there are no Left Crank or Right Crank planes.
        # Crank Length = 0, so these dimensions are architecturally not needed.
        for skip_label in [
            "Crank Left Overall",
            "Crank Right Overall",
            "Length EQ Outer Left",
            "Length EQ Outer Right",
        ]:
            results.append(_status(
                skip_label, "Skipped",
                "Not applicable for 2/2 topology (no crank planes)"
            ))
        return results

    # ==========================================================
    # 3. Crank Left Overall: Left Crank ↔ V_Center → GP "Crank Length"
    # ==========================================================
    #
    # NOTE: A single non-reporting GlobalParameter can label multiple separate
    # dimension elements.  We use two individual 2-reference dimensions (one per
    # side) rather than one multi-segment chain so that LabelDimension targets
    # each segment value directly (= Crank Length) rather than the full chain sum.
    #
    crank_left_label = "Crank Left Overall"
    ra_crank_left    = _refs_from_planes(planes_dict, "Left Crank", "V_Center")

    if ra_crank_left is None:
        results.append(_status(crank_left_label, "Error", "Left Crank or V_Center plane missing"))
    else:
        ref_ids = _ref_ids_from_array(ra_crank_left)
        if ref_ids in existing_dim_map:
            existing_dim = existing_dim_map[ref_ids]
            action, detail = _repair_gp_if_missing(doc, existing_dim, "Crank Length")
            results.append(_status(crank_left_label, "Existing", detail))
            logger.info("Crank left dim existing — GP repair", action=action)
        else:
            try:
                x_left_crank = _x("Left Crank")
                x_vcenter    = _x("V_Center")
                if x_left_crank is None or x_vcenter is None:
                    raise ValueError("Left Crank or V_Center plane not found")

                dim_line = _dimension_line_horizontal(
                    cy + y_off_crank,
                    x_left_crank - x_margin,
                    x_vcenter    + x_margin,
                )
                dim = doc.Create.NewDimension(view, dim_line, ra_crank_left)
                _mark_dimension(dim)
                ok, gp_detail = _associate_gp(doc, dim, "Crank Length")
                results.append(_status(
                    crank_left_label, "Created",
                    "Dim created. {0}".format(gp_detail)
                ))
                existing_dim_map[ref_ids] = dim
                logger.info("Crank left dim created")
            except Exception as exc:
                results.append(_status(crank_left_label, "Error", str(exc)))
                logger.error("Failed to create crank left dim", exc=exc)

    # ==========================================================
    # 4. Crank Right Overall: Right Crank ↔ V_Center → GP "Crank Length"
    # ==========================================================
    crank_right_label = "Crank Right Overall"
    ra_crank_right    = _refs_from_planes(planes_dict, "V_Center", "Right Crank")

    if ra_crank_right is None:
        results.append(_status(crank_right_label, "Error", "V_Center or Right Crank plane missing"))
    else:
        ref_ids = _ref_ids_from_array(ra_crank_right)
        if ref_ids in existing_dim_map:
            existing_dim = existing_dim_map[ref_ids]
            action, detail = _repair_gp_if_missing(doc, existing_dim, "Crank Length")
            results.append(_status(crank_right_label, "Existing", detail))
            logger.info("Crank right dim existing — GP repair", action=action)
        else:
            try:
                x_vcenter     = _x("V_Center")
                x_right_crank = _x("Right Crank")
                if x_vcenter is None or x_right_crank is None:
                    raise ValueError("V_Center or Right Crank plane not found")

                dim_line = _dimension_line_horizontal(
                    cy + y_off_crank,
                    x_vcenter     - x_margin,
                    x_right_crank + x_margin,
                )
                dim = doc.Create.NewDimension(view, dim_line, ra_crank_right)
                _mark_dimension(dim)
                ok, gp_detail = _associate_gp(doc, dim, "Crank Length")
                results.append(_status(
                    crank_right_label, "Created",
                    "Dim created. {0}".format(gp_detail)
                ))
                existing_dim_map[ref_ids] = dim
                logger.info("Crank right dim created")
            except Exception as exc:
                results.append(_status(crank_right_label, "Error", str(exc)))
                logger.error("Failed to create crank right dim", exc=exc)

    # ==========================================================
    # 5. EQ Outer Left: Left End → Left Intermediate → Left Crank  (EQ)
    # ==========================================================
    eq_outer_left_label = "Length EQ Outer Left"
    ra_eq_ol = _refs_from_planes(
        planes_dict, "Left End", "Left Intermediate", "Left Crank"
    )

    if ra_eq_ol is None:
        results.append(_status(
            eq_outer_left_label, "Error",
            "Left End / Left Intermediate / Left Crank plane(s) missing"
        ))
    else:
        ref_ids = _ref_ids_from_array(ra_eq_ol)
        if ref_ids in existing_dim_map:
            results.append(_status(eq_outer_left_label, "Existing", "EQ outer left already present"))
        else:
            try:
                x_le  = _x("Left End")
                x_lc  = _x("Left Crank")
                if x_le is None or x_lc is None:
                    raise ValueError("Plane not found")

                dim_line = _dimension_line_horizontal(
                    cy + y_off_eq_outer,
                    x_le - x_margin,
                    x_lc + x_margin,
                )
                dim = doc.Create.NewDimension(view, dim_line, ra_eq_ol)
                _mark_dimension(dim)
                try:
                    dim.AreSegmentsEqual = True
                except Exception as eq_exc:
                    logger.warning("Could not set EQ on outer left chain", exc=str(eq_exc))
                results.append(_status(eq_outer_left_label, "Created", "EQ outer left created — segments equal"))
                existing_dim_map[ref_ids] = dim
                logger.info("Length EQ outer left created")
            except Exception as exc:
                results.append(_status(eq_outer_left_label, "Error", str(exc)))
                logger.error("Failed to create EQ outer left", exc=exc)

    # ==========================================================
    # 6. EQ Outer Right: Right Crank → Right Intermediate → Right End  (EQ)
    # ==========================================================
    eq_outer_right_label = "Length EQ Outer Right"
    ra_eq_or = _refs_from_planes(
        planes_dict, "Right Crank", "Right Intermediate", "Right End"
    )

    if ra_eq_or is None:
        results.append(_status(
            eq_outer_right_label, "Error",
            "Right Crank / Right Intermediate / Right End plane(s) missing"
        ))
    else:
        ref_ids = _ref_ids_from_array(ra_eq_or)
        if ref_ids in existing_dim_map:
            results.append(_status(eq_outer_right_label, "Existing", "EQ outer right already present"))
        else:
            try:
                x_rc = _x("Right Crank")
                x_re = _x("Right End")
                if x_rc is None or x_re is None:
                    raise ValueError("Plane not found")

                dim_line = _dimension_line_horizontal(
                    cy + y_off_eq_outer,
                    x_rc - x_margin,
                    x_re + x_margin,
                )
                dim = doc.Create.NewDimension(view, dim_line, ra_eq_or)
                _mark_dimension(dim)
                try:
                    dim.AreSegmentsEqual = True
                except Exception as eq_exc:
                    logger.warning("Could not set EQ on outer right chain", exc=str(eq_exc))
                results.append(_status(eq_outer_right_label, "Created", "EQ outer right created — segments equal"))
                existing_dim_map[ref_ids] = dim
                logger.info("Length EQ outer right created")
            except Exception as exc:
                results.append(_status(eq_outer_right_label, "Error", str(exc)))
                logger.error("Failed to create EQ outer right", exc=exc)

    return results


# ---------------------------------------------------------------------------
# Entry point called from Tab 2
# ---------------------------------------------------------------------------

def ensure_all_dimensions(doc, center_xyz, is_special_22=False):
    """Create all width and length dimensions/constraints.

    Args:
        doc           (Document): Active Revit document.
        center_xyz    (XYZ):      Bridge logical center.
        is_special_22 (bool):     True for the 4m CRNK-2/2 special topology.
                                  When True, crank and outer-EQ dimensions are
                                  skipped (no Left Crank / Right Crank planes).

    Returns:
        list of status dicts
    """
    from core.refplane_manager import read_gp_values_mm

    logger  = get_logger()
    results = []

    planes_dict      = _get_planes_by_name(doc)
    existing_dim_map = _existing_automation_dims_map(doc)

    # Read live GP values for any needed computations
    gp_values = read_gp_values_mm(doc, ["Length", "Crank Length"])

    logger.info(
        "Starting dimension/constraint creation",
        n_planes=len(planes_dict),
        n_existing_dims=len(existing_dim_map),
    )

    results.extend(ensure_width_dimensions(doc, center_xyz, planes_dict, existing_dim_map))
    results.extend(ensure_length_dimensions(
        doc, center_xyz, planes_dict, existing_dim_map, gp_values,
        is_special_22=is_special_22,
    ))

    # Summarise associations
    n_associated = sum(
        1 for r in results
        if "Associated" in r.get("detail", "") or "Association Added" in r.get("detail", "")
    )
    n_eq = sum(
        1 for r in results
        if "EQ" in r.get("detail", "") or "EQ" in r.get("name", "")
    )
    logger.info(
        "Dimension/constraint creation complete",
        total=len(results),
        gp_associations=n_associated,
        eq_chains=n_eq,
    )
    return results
