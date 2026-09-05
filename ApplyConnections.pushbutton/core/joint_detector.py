# -*- coding: utf-8 -*-
"""
joint_detector.py — Structural joint detection framework.

ApplyConnections.pushbutton / core/
Urbana Bridge Generator — Revit 2022 / 2024.3  |  pyRevit 6.4.0  |  IronPython 2.7

Provides a reusable framework for detecting structural joints between members.
The framework dispatches to connection-type-specific rules.

Currently implemented:
    Beam–Beam     — endpoint-to-curve proximity, bidirectional check

Designed for extension:
    Bearer–Beam, Post–Beam, Joist–Bearer, etc. can each add their rule
    function without modifying the core detection loop.

Detection strategy (in order, per design spec):
    1. LocationCurve — primary geometric source
    2. Beam endpoints
    3. Spatial/proximity mathematics
    4. No expensive solid-vs-solid geometry

Python compatibility: IronPython 2.7 — no f-strings, no dataclasses.
"""

import clr
clr.AddReference("RevitAPI")

from core.member_collector  import collect_structural_framing
from core.joint_geometry    import (
    get_start_end,
    curve_direction,
    distance_point_to_segment,
    closest_point_on_segment,
    parametric_position,
    angle_between_deg,
    normalize,
    DEFAULT_PROXIMITY_TOLERANCE,
)
from core.joint_model       import (
    JointRecord,
    CONNECTION_END_START,
    CONNECTION_END_END,
    CONNECTION_END_MID,
)


# ---------------------------------------------------------------------------
# Public entry point — Beam-to-Beam detection
# ---------------------------------------------------------------------------

def detect_beam_beam_joints(doc, primary_element, bridge_type, logger=None,
                             tolerance=DEFAULT_PROXIMITY_TOLERANCE):
    """Detect valid Beam-to-Beam joints for the given primary main beam.

    Algorithm:
        1. Get primary LocationCurve + start/end points.
        2. Collect all Structural Framing members (excluding primary).
        3. For each candidate: apply _beam_beam_rule().
        4. Filter None results.
        5. Return list[JointRecord].

    The 'primary beam' is the user-selected main beam.
    Each JointRecord identifies one valid secondary member and joint location.

    Args:
        doc:             Autodesk.Revit.DB.Document
        primary_element: the selected main beam Element
        bridge_type:     str — "concrete" or "timber" (passed into JointRecord)
        logger:          ApplyConnectionsLogger or None
        tolerance:       float — proximity tolerance in Revit internal units

    Returns:
        list[JointRecord] — sorted by parametric position along primary beam.
    """
    from core.member_collector import get_location_curve

    primary_id = primary_element.Id

    # --- Get primary curve ---
    primary_loc = get_location_curve(primary_element)
    if primary_loc is None:
        if logger:
            logger.warning("Primary element has no LocationCurve; aborting detection.",
                           primary_id=str(primary_id))
        return []

    primary_curve = primary_loc.Curve
    primary_start, primary_end = get_start_end(primary_curve)
    primary_dir = curve_direction(primary_start, primary_end)

    if logger:
        logger.debug(
            "Starting Beam-Beam detection",
            primary_id=str(primary_id),
            tolerance=tolerance,
            bridge_type=bridge_type,
        )

    # --- Collect candidates ---
    candidates = collect_structural_framing(doc, exclude_ids=[primary_id])

    if logger:
        logger.debug("Candidates collected", count=len(candidates))

    # --- Apply rule to each candidate ---
    joints = []
    for candidate in candidates:
        try:
            record = _beam_beam_rule(
                primary_id     = primary_id,
                primary_start  = primary_start,
                primary_end    = primary_end,
                primary_dir    = primary_dir,
                candidate      = candidate,
                bridge_type    = bridge_type,
                tolerance      = tolerance,
            )
            if record is not None:
                joints.append(record)
                if logger:
                    logger.debug(
                        "Joint detected",
                        **record.diagnostic_dict()
                    )
        except Exception as ex:
            if logger:
                logger.warning(
                    "Error evaluating candidate",
                    candidate_id=str(candidate.Id),
                    error=str(ex),
                )
            continue

    # Sort by parametric position along primary beam (start → end)
    joints.sort(key=lambda r: r.primary_t if r.primary_t is not None else 0.5)

    if logger:
        logger.info(
            "Beam-Beam detection complete",
            primary_id=str(primary_id),
            joints_found=len(joints),
        )

    return joints


# ---------------------------------------------------------------------------
# Beam-to-Beam rule — bidirectional endpoint proximity
# ---------------------------------------------------------------------------

def _beam_beam_rule(primary_id, primary_start, primary_end, primary_dir,
                    candidate, bridge_type, tolerance):
    """Determine whether a candidate forms a valid Beam-to-Beam joint.

    Strategy — bidirectional proximity check:
        A) Does either endpoint of the CANDIDATE fall within [tolerance]
           of the primary beam's line segment?
        B) Does either endpoint of the PRIMARY beam fall within [tolerance]
           of the candidate's line segment?

    Either condition qualifies as a joint.

    The joint XYZ is the closest point on the primary segment to the
    triggering secondary endpoint (or vice versa).

    Args:
        primary_id:    ElementId of the primary beam.
        primary_start: XYZ start of primary curve.
        primary_end:   XYZ end of primary curve.
        primary_dir:   normalised XYZ direction of primary beam.
        candidate:     Autodesk.Revit.DB.Element — the secondary candidate.
        bridge_type:   str
        tolerance:     float — Revit internal units.

    Returns:
        JointRecord or None
    """
    from core.member_collector import get_location_curve

    cand_loc = get_location_curve(candidate)
    if cand_loc is None:
        return None

    cand_curve = cand_loc.Curve
    cand_start, cand_end = get_start_end(cand_curve)
    cand_dir = curve_direction(cand_start, cand_end)

    # ---- Check A: candidate endpoints against primary segment ----
    dist_cs = distance_point_to_segment(cand_start, primary_start, primary_end)
    dist_ce = distance_point_to_segment(cand_end,   primary_start, primary_end)

    # ---- Check B: primary endpoints against candidate segment ----
    dist_ps = distance_point_to_segment(primary_start, cand_start, cand_end)
    dist_pe = distance_point_to_segment(primary_end,   cand_start, cand_end)

    # Find the minimum distance and determine which endpoint is the joint
    best_dist    = None
    joint_xyz    = None
    conn_end     = None
    secondary_pt = None  # the point on the secondary that is closest

    candidates_check = [
        (dist_cs, cand_start, CONNECTION_END_START, "A_start"),
        (dist_ce, cand_end,   CONNECTION_END_END,   "A_end"),
    ]

    for (d, sec_pt, end_label, _tag) in candidates_check:
        if d < tolerance:
            if best_dist is None or d < best_dist:
                best_dist    = d
                secondary_pt = sec_pt
                conn_end     = end_label
                joint_xyz    = closest_point_on_segment(sec_pt, primary_start, primary_end)

    # Also check primary endpoints against candidate segment (check B)
    # This catches cases where the primary end touches the secondary midspan.
    for (d, prim_pt) in [(dist_ps, primary_start), (dist_pe, primary_end)]:
        if d < tolerance:
            cand_closest = closest_point_on_segment(prim_pt, cand_start, cand_end)
            t_on_cand = parametric_position(prim_pt, cand_start, cand_end)
            if t_on_cand < 0.1:
                cend = CONNECTION_END_START
            elif t_on_cand > 0.9:
                cend = CONNECTION_END_END
            else:
                cend = CONNECTION_END_MID

            if best_dist is None or d < best_dist:
                best_dist    = d
                joint_xyz    = cand_closest
                secondary_pt = cand_closest
                conn_end     = cend

    if best_dist is None:
        # No proximity match — not a joint
        return None

    # --- Compute angle between members ---
    angle = angle_between_deg(primary_dir, cand_dir)

    # --- Parametric position along primary beam ---
    if joint_xyz is not None:
        t_primary = parametric_position(joint_xyz, primary_start, primary_end)
        # Clamp for reporting
        t_primary = max(0.0, min(1.0, t_primary))
    else:
        t_primary = 0.5

    return JointRecord(
        primary_id     = primary_id,
        secondary_id   = candidate.Id,
        joint_xyz      = joint_xyz,
        primary_dir    = primary_dir,
        secondary_dir  = cand_dir,
        angle_deg      = angle,
        connection_end = conn_end if conn_end is not None else CONNECTION_END_MID,
        primary_t      = t_primary,
        bridge_type    = bridge_type,
        proximity_dist = best_dist,
    )
