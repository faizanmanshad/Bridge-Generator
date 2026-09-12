# -*- coding: utf-8 -*-
"""
joint_detector.py — Structural joint detection framework.

ApplyConnections.pushbutton / core/
Urbana Bridge Generator — Revit 2022 / 2024.3  |  pyRevit 6.4.0  |  IronPython 2.7

Provides a reusable framework for detecting structural joints between members.
The framework dispatches to connection-type-specific rules.

Currently implemented:
    Beam–Beam     — exact same TypeId, project-wide, valid end-to-end splice.
    Bearer–Beam   — distinct TypeIds (beam + bearer), endpoint-to-segment
                    proximity, with anti-parallel guard.

Designed for extension:
    Post–Beam, Joist–Bearer, etc. can each add their rule function without
    modifying the core detection loop.

Python compatibility: IronPython 2.7 — no f-strings, no dataclasses.
"""

import os
import json
import clr  # type: ignore
clr.AddReference("RevitAPI")

from core.member_collector  import collect_structural_framing, get_location_curve
from core.joint_geometry    import (
    get_start_end,
    curve_direction,
    distance_point_to_point,
    distance_point_to_segment,
    is_parallel,
    DEFAULT_PROXIMITY_TOLERANCE,
    add,
    scale
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
        1. Read exact selected beam TypeId.
        2. Collect all Structural Framing members matching that exact TypeId.
        3. Evaluate all unique pairs (A, B) using end-to-end splice logic.
        4. Log results to diagnostic JSON.
        5. Return (joints, matching_count, valid_splice_count).
    """
    primary_id = primary_element.Id
    try:
        target_type_id = primary_element.GetTypeId()
    except Exception as ex:
        if logger:
            logger.error("Failed to get TypeId from primary element", exc=ex)
        return [], 0, 0

    if logger:
        logger.debug(
            "Starting Beam-Beam detection",
            primary_id=str(primary_id),
            target_type_id=str(target_type_id),
            tolerance=tolerance,
            bridge_type=bridge_type,
        )

    # --- Collect ALL candidates of same type (including primary) ---
    # We do not exclude primary_id, because primary_id might form a valid pair with something else!
    matching_beams = collect_structural_framing(doc, exclude_ids=None, target_type_id=target_type_id)
    matching_count = len(matching_beams)

    if logger:
        logger.debug("Candidates collected", count=matching_count)

    joints = []
    diagnostic_logs = []
    
    # --- Generate Unique Pairs ---
    # Compare every beam with every other beam, avoiding duplicates A-B and B-A
    for i in range(matching_count):
        for j in range(i + 1, matching_count):
            beam_a = matching_beams[i]
            beam_b = matching_beams[j]
            
            # Pair Key ensures consistency
            id_a = beam_a.Id.IntegerValue
            id_b = beam_b.Id.IntegerValue
            
            # Always pass the smaller ID as "primary" and larger as "secondary"
            # just for consistent ordering in JointRecord, this is purely arbitrary
            if id_a < id_b:
                primary = beam_a
                secondary = beam_b
            else:
                primary = beam_b
                secondary = beam_a
                
            record, log_entry = _beam_beam_rule(
                primary, secondary, bridge_type, tolerance
            )
            
            diagnostic_logs.append(log_entry)
            
            if record is not None:
                joints.append(record)
                if logger:
                    logger.debug("Valid Splice Joint detected", **record.diagnostic_dict())

    valid_splice_count = len(joints)

    # --- Save Diagnostic Log ---
    try:
        # We need the real Family / Type name for the diagnostic report.
        try:
            target_elem_type = doc.GetElement(target_type_id)
            target_type_name = target_elem_type.Name if target_elem_type else "Unknown Type"
            # Get Family Name if possible
            target_family_name = getattr(target_elem_type, 'FamilyName', "Unknown Family")
        except Exception:
            target_type_name = "Unknown"
            target_family_name = "Unknown"

        diag_data = {
            "reference_beam_id": primary_id.IntegerValue,
            "target_type_id": target_type_id.IntegerValue,
            "target_family_name": target_family_name,
            "target_type_name": target_type_name,
            "matching_instance_count": matching_count,
            "pairs": diagnostic_logs
        }
        dump_path = os.path.join(os.path.dirname(__file__), "..", "scratch", "beam_beam_diagnostic.json")
        # Ensure scratch dir exists
        scratch_dir = os.path.dirname(dump_path)
        if not os.path.exists(scratch_dir):
            os.makedirs(scratch_dir)
            
        with open(dump_path, 'w') as f:
            json.dump(diag_data, f, indent=4)
    except Exception as ex:
        if logger:
            logger.warning("Failed to write diagnostic log", exc=ex)

    if logger:
        logger.info(
            "Beam-Beam detection complete",
            matching_count=matching_count,
            valid_splices=valid_splice_count,
        )

    # Sort joints conceptually (by arbitrary ID order to keep it stable)
    joints.sort(key=lambda r: (r.primary_id.IntegerValue, r.secondary_id.IntegerValue))

    return joints, matching_count, valid_splice_count


# ---------------------------------------------------------------------------
# Public entry point — Bearer-to-Beam detection
# ---------------------------------------------------------------------------

def detect_bearer_beam_joints(doc,
                               ref_beam_element,
                               ref_bearer_element,
                               bridge_type,
                               logger=None,
                               tolerance=DEFAULT_PROXIMITY_TOLERANCE):
    """Detect valid Bearer-to-Beam joints project-wide.

    Algorithm:
        1. Extract beam TypeId from ref_beam_element.
        2. Extract bearer TypeId from ref_bearer_element.
        3. Validate they are distinct (role ambiguity guard).
        4. Collect all Structural Framing instances matching beam TypeId.
        5. Collect all Structural Framing instances matching bearer TypeId.
        6. For every (beam, bearer) pair run _bearer_beam_rule().
           Each bearer end is tested independently — one bearer can yield
           joints at both endpoints.
        7. Deduplicate by (beam_id, bearer_id) pair key.
        8. Log results to diagnostic JSON.
        9. Return (joints, beam_count, bearer_count, valid_joint_count).

    Args:
        doc:                Autodesk.Revit.DB.Document
        ref_beam_element:   Element — reference beam (TypeId seed)
        ref_bearer_element: Element — reference bearer (TypeId seed)
        bridge_type:        str — "concrete" or "timber"
        logger:             ApplyConnectionsLogger or None
        tolerance:          float — proximity tolerance in Revit internal units

    Returns:
        (list[JointRecord], int, int, int)
        = (joints, beam_count, bearer_count, valid_joint_count)
    """
    # --- Extract TypeIds ---
    try:
        beam_type_id = ref_beam_element.GetTypeId()
    except Exception as ex:
        if logger:
            logger.error("Failed to get TypeId from reference beam", exc=ex)
        return [], 0, 0, 0

    try:
        bearer_type_id = ref_bearer_element.GetTypeId()
    except Exception as ex:
        if logger:
            logger.error("Failed to get TypeId from reference bearer", exc=ex)
        return [], 0, 0, 0

    # --- Role-ambiguity guard ---
    if beam_type_id == bearer_type_id:
        if logger:
            logger.error(
                "Bearer-Beam detection aborted: both references share the same TypeId. "
                "Cannot distinguish beam from bearer roles.",
                beam_type_id=str(beam_type_id),
            )
        return [], 0, 0, 0

    if logger:
        logger.debug(
            "Starting Bearer-Beam detection",
            beam_type_id=str(beam_type_id),
            bearer_type_id=str(bearer_type_id),
            tolerance=tolerance,
            bridge_type=bridge_type,
        )

    # --- Collect populations ---
    beams   = collect_structural_framing(doc, exclude_ids=None, target_type_id=beam_type_id)
    bearers = collect_structural_framing(doc, exclude_ids=None, target_type_id=bearer_type_id)

    beam_count   = len(beams)
    bearer_count = len(bearers)

    if logger:
        logger.debug(
            "Populations collected",
            beam_count=beam_count,
            bearer_count=bearer_count,
        )

    joints = []
    diagnostic_logs = []
    # Deduplicate by (beam_id, bearer_id) — each unique physical joint only once
    seen_pairs = set()

    for beam in beams:
        beam_id_int = beam.Id.IntegerValue

        loc_beam = get_location_curve(beam)
        if loc_beam is None:
            continue
        beam_curve = loc_beam.Curve
        beam_start, beam_end = get_start_end(beam_curve)
        beam_dir = curve_direction(beam_start, beam_end)

        for bearer in bearers:
            bearer_id_int = bearer.Id.IntegerValue

            # Stable pair key — (beam, bearer) are inherently different roles,
            # so no need to sort; (beam_id, bearer_id) is the canonical key.
            pair_key = (beam_id_int, bearer_id_int)
            if pair_key in seen_pairs:
                continue

            loc_bearer = get_location_curve(bearer)
            if loc_bearer is None:
                log = {
                    "pair_name": "Beam {0} / Bearer {1}".format(beam_id_int, bearer_id_int),
                    "beam_id": beam_id_int,
                    "bearer_id": bearer_id_int,
                    "valid_joint": "NO",
                    "reason": "Bearer missing LocationCurve.",
                }
                diagnostic_logs.append(log)
                continue

            bearer_curve = loc_bearer.Curve
            bearer_start, bearer_end = get_start_end(bearer_curve)
            bearer_dir = curve_direction(bearer_start, bearer_end)

            # Run the bearer-beam rule — may produce 0, 1 or 2 joints
            records, log_entries = _bearer_beam_rule(
                beam, bearer,
                beam_start, beam_end, beam_dir,
                bearer_start, bearer_end, bearer_dir,
                bridge_type, tolerance,
            )

            diagnostic_logs.extend(log_entries)

            if records:
                seen_pairs.add(pair_key)
                joints.extend(records)
                for rec in records:
                    if logger:
                        logger.debug(
                            "Valid Bearer-Beam joint detected",
                            **rec.diagnostic_dict()
                        )

    valid_joint_count = len(joints)

    # --- Diagnostic JSON ---
    try:
        try:
            beam_type_elem   = doc.GetElement(beam_type_id)
            bearer_type_elem = doc.GetElement(bearer_type_id)
            beam_type_name   = beam_type_elem.Name   if beam_type_elem   else "Unknown"
            bearer_type_name = bearer_type_elem.Name if bearer_type_elem else "Unknown"
        except Exception:
            beam_type_name   = "Unknown"
            bearer_type_name = "Unknown"

        diag_data = {
            "beam_type_id":       beam_type_id.IntegerValue,
            "bearer_type_id":     bearer_type_id.IntegerValue,
            "beam_type_name":     beam_type_name,
            "bearer_type_name":   bearer_type_name,
            "beam_instance_count":   beam_count,
            "bearer_instance_count": bearer_count,
            "valid_joint_count":     valid_joint_count,
            "pairs": diagnostic_logs,
        }

        dump_path = os.path.join(
            os.path.dirname(__file__), "..", "scratch", "bearer_beam_diagnostic.json"
        )
        scratch_dir = os.path.dirname(dump_path)
        if not os.path.exists(scratch_dir):
            os.makedirs(scratch_dir)

        with open(dump_path, "w") as f:
            json.dump(diag_data, f, indent=4)
    except Exception as ex:
        if logger:
            logger.warning("Failed to write bearer-beam diagnostic log", exc=ex)

    if logger:
        logger.info(
            "Bearer-Beam detection complete",
            beam_count=beam_count,
            bearer_count=bearer_count,
            valid_joint_count=valid_joint_count,
        )

    # Sort for stable ordering: primary (beam) first, then secondary (bearer)
    joints.sort(key=lambda r: (r.primary_id.IntegerValue, r.secondary_id.IntegerValue))

    return joints, beam_count, bearer_count, valid_joint_count


# ---------------------------------------------------------------------------
# Bearer-to-Beam rule
# ---------------------------------------------------------------------------

def _bearer_beam_rule(beam, bearer,
                       beam_start, beam_end, beam_dir,
                       bearer_start, bearer_end, bearer_dir,
                       bridge_type, tolerance):
    """Determine whether a (beam, bearer) pair has valid Bearer-Beam joints.

    Strategy:
        1. Reject if bearer direction is approximately parallel to beam direction
           (would mean the bearer runs alongside the beam, not framing into it).
        2. Test both bearer endpoints against the full beam centerline segment
           using distance_point_to_segment().
        3. Each bearer endpoint that is within tolerance of the beam segment
           is recorded as an independent joint.

    Why distance_point_to_segment rather than point-to-point:
        A bearer can sit at any point along the beam, not just at the beam
        endpoints. The segment test correctly captures a bearer framing into
        the interior of a beam.

    Why NOT require the bearer end to be near the BEAM END:
        Main beams are typically continuous; bearers frame into their span at
        various positions — not necessarily at the beam ends.

    Args:
        beam, bearer:           Elements
        beam_start, beam_end:   XYZ — beam segment endpoints
        beam_dir:               XYZ — normalised beam direction
        bearer_start, bearer_end: XYZ — bearer segment endpoints
        bearer_dir:             XYZ — normalised bearer direction
        bridge_type:            str
        tolerance:              float — Revit internal units

    Returns:
        (list[JointRecord], list[dict])
        — may return 0, 1, or 2 JointRecords (one per valid bearer end)
    """
    beam_id_int   = beam.Id.IntegerValue
    bearer_id_int = bearer.Id.IntegerValue

    records     = []
    log_entries = []

    # --- 1. Anti-parallel guard ---
    # If bearer runs parallel/anti-parallel to beam, it is NOT framing into it.
    if is_parallel(bearer_dir, beam_dir):
        log_entries.append({
            "pair_name":  "Beam {0} / Bearer {1}".format(beam_id_int, bearer_id_int),
            "beam_id":    beam_id_int,
            "bearer_id":  bearer_id_int,
            "valid_joint": "NO",
            "reason":     "Bearer runs parallel to beam (not a framing connection).",
        })
        return records, log_entries

    # --- 2. Test bearer Start endpoint ---
    dist_start = distance_point_to_segment(bearer_start, beam_start, beam_end)
    dist_start_mm = dist_start * 304.8

    if dist_start <= tolerance:
        # Valid joint at bearer Start end
        log_entries.append({
            "pair_name":       "Beam {0} / Bearer {1}".format(beam_id_int, bearer_id_int),
            "beam_id":         beam_id_int,
            "bearer_id":       bearer_id_int,
            "bearer_end_tested": "Start",
            "dist_mm":         round(dist_start_mm, 2),
            "valid_joint":     "YES",
        })
        record = JointRecord(
            primary_id    = beam.Id,      # Beam = primary (Input 1 in Default)
            secondary_id  = bearer.Id,    # Bearer = secondary (Input 2 in Default)
            joint_xyz     = bearer_start,
            primary_dir   = beam_dir,
            secondary_dir = bearer_dir,
            angle_deg     = _angle_between(beam_dir, bearer_dir),
            connection_end = CONNECTION_END_START,
            primary_t     = None,
            bridge_type   = bridge_type,
            proximity_dist = dist_start,
        )
        records.append(record)
    else:
        log_entries.append({
            "pair_name":       "Beam {0} / Bearer {1}".format(beam_id_int, bearer_id_int),
            "beam_id":         beam_id_int,
            "bearer_id":       bearer_id_int,
            "bearer_end_tested": "Start",
            "dist_mm":         round(dist_start_mm, 2),
            "valid_joint":     "NO",
            "reason":          "Bearer Start too far from beam ({0:.1f} mm).".format(dist_start_mm),
        })

    # --- 3. Test bearer End endpoint ---
    dist_end = distance_point_to_segment(bearer_end, beam_start, beam_end)
    dist_end_mm = dist_end * 304.8

    if dist_end <= tolerance:
        # Valid joint at bearer End end
        log_entries.append({
            "pair_name":       "Beam {0} / Bearer {1}".format(beam_id_int, bearer_id_int),
            "beam_id":         beam_id_int,
            "bearer_id":       bearer_id_int,
            "bearer_end_tested": "End",
            "dist_mm":         round(dist_end_mm, 2),
            "valid_joint":     "YES",
        })
        record = JointRecord(
            primary_id    = beam.Id,
            secondary_id  = bearer.Id,
            joint_xyz     = bearer_end,
            primary_dir   = beam_dir,
            secondary_dir = bearer_dir,
            angle_deg     = _angle_between(beam_dir, bearer_dir),
            connection_end = CONNECTION_END_END,
            primary_t     = None,
            bridge_type   = bridge_type,
            proximity_dist = dist_end,
        )
        records.append(record)
    else:
        log_entries.append({
            "pair_name":       "Beam {0} / Bearer {1}".format(beam_id_int, bearer_id_int),
            "beam_id":         beam_id_int,
            "bearer_id":       bearer_id_int,
            "bearer_end_tested": "End",
            "dist_mm":         round(dist_end_mm, 2),
            "valid_joint":     "NO",
            "reason":          "Bearer End too far from beam ({0:.1f} mm).".format(dist_end_mm),
        })

    return records, log_entries


def _angle_between(dir_a, dir_b):
    """Return the angle in degrees between two direction vectors [0..90]."""
    try:
        from core.joint_geometry import angle_between_deg
        return angle_between_deg(dir_a, dir_b)
    except Exception:
        return 0.0


# ---------------------------------------------------------------------------
# Beam-to-Beam rule — End-to-End Splice Only
# ---------------------------------------------------------------------------

def _beam_beam_rule(beam_a, beam_b, bridge_type, tolerance):
    """Determine whether a candidate pair forms a valid end-to-end Beam-to-Beam splice.

    Strategy:
        1. Get endpoints for A and B.
        2. Ensure they are approximately parallel/collinear.
        3. Test 4 endpoint combinations (A.start-B.start, A.start-B.end, etc.).
        4. If min distance is within tolerance, it's a valid end-to-end splice.
        5. Returns (JointRecord or None, diagnostic_dict).
    """
    id_a = beam_a.Id.IntegerValue
    id_b = beam_b.Id.IntegerValue
    pair_name = "Pair {0} / {1}".format(id_a, id_b)
    
    log = {
        "pair_name": pair_name,
        "beam_a": id_a,
        "beam_b": id_b,
        "same_type_id": "YES", # Pre-filtered
    }
    
    loc_a = get_location_curve(beam_a)
    loc_b = get_location_curve(beam_b)
    
    if loc_a is None or loc_b is None:
        log["valid_splice"] = "NO"
        log["reason"] = "Missing LocationCurve geometry."
        return None, log

    curve_a = loc_a.Curve
    curve_b = loc_b.Curve
    
    start_a, end_a = get_start_end(curve_a)
    start_b, end_b = get_start_end(curve_b)
    
    dir_a = curve_direction(start_a, end_a)
    dir_b = curve_direction(start_b, end_b)
    
    # 1. Parallel / Collinear check
    # We use angular tolerance (default 15 deg) to verify they run in same line.
    is_par = is_parallel(dir_a, dir_b)
    log["collinear"] = "YES" if is_par else "NO"
    
    if not is_par:
        log["valid_splice"] = "NO"
        log["reason"] = "Beams are not parallel/collinear (likely crossing)."
        return None, log
        
    # 2. Endpoint proximity check
    # Check all 4 combinations
    combinations = [
        (distance_point_to_point(start_a, start_b), start_a, start_b, "A.Start / B.Start"),
        (distance_point_to_point(start_a, end_b),   start_a, end_b,   "A.Start / B.End"),
        (distance_point_to_point(end_a, start_b),   end_a,   start_b, "A.End / B.Start"),
        (distance_point_to_point(end_a, end_b),     end_a,   end_b,   "A.End / B.End"),
    ]
    
    # Find minimum distance
    min_dist, pt_a, pt_b, combo_name = min(combinations, key=lambda x: x[0])
    
    log["closest_endpoints"] = combo_name
    
    # Convert internal units (feet) to mm for human-readable diagnostic (1 ft = 304.8 mm)
    dist_mm = min_dist * 304.8
    log["endpoint_distance_mm"] = round(dist_mm, 2)
    
    if min_dist <= tolerance:
        # Valid end-to-end splice!
        # Joint is precisely at the midpoint between the two closest ends
        joint_xyz = scale(add(pt_a, pt_b), 0.5)
        
        log["valid_splice"] = "YES"
        
        record = JointRecord(
            primary_id     = beam_a.Id,
            secondary_id   = beam_b.Id,
            joint_xyz      = joint_xyz,
            primary_dir    = dir_a,
            secondary_dir  = dir_b,
            angle_deg      = 0.0, # Parallel
            connection_end = CONNECTION_END_END, # Semantic marker for splice
            primary_t      = 1.0, 
            bridge_type    = bridge_type,
            proximity_dist = min_dist,
        )
        return record, log
    else:
        log["valid_splice"] = "NO"
        log["reason"] = "Endpoint distance ({0:.1f} mm) exceeds tolerance. (Midspan crossing or simply far apart)".format(dist_mm)
        return None, log
