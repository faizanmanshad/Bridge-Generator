# -*- coding: utf-8 -*-
"""
connection_orientation.py — Orientation helpers for structural connection placement.

Apply Connections.pushbutton / core/
Urbana Bridge Generator — Revit 2022 / 2024.3  |  pyRevit 6.4.0  |  IronPython 2.7

Computes orientation hints from a JointRecord.
The output is a dict of geometric hints used by connection_placer.

First iteration: derives flip / rotation hints from dot/cross products.
Individual connection types can later specialise orientation by inspecting
the hint dict and overriding as needed.

Python compatibility: IronPython 2.7 — no f-strings, no dataclasses.
"""

import math

from core.joint_geometry import dot, cross, normalize, length
from core.joint_model    import CONNECTION_END_START, CONNECTION_END_END


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def compute_orientation(joint_record):
    """Compute orientation hints for a structural connection from a JointRecord.

    Returns a dict with the following keys:

        'flip_x':   bool  — primary beam direction is reversed relative to world X
        'flip_y':   bool  — secondary beam approaches from the 'right' side
        'rotation': float — suggested rotation angle in degrees [0, 360)
        'side':     str   — 'left' | 'right' | 'unknown'
        'at_end':   bool  — True if secondary connects at its endpoint
        'angle_deg': float — angle between members (0–90°)

    These hints are advisory. The Revit structural connection API may override
    or ignore them depending on the connection type's own parameter set.

    Args:
        joint_record: JointRecord

    Returns:
        dict
    """
    pdir = joint_record.primary_dir
    sdir = joint_record.secondary_dir

    # Determine which side of the primary beam the secondary approaches from.
    # Cross product of primary direction with secondary direction gives the
    # normal to the plane containing both members. The Z component tells us
    # left vs right (in plan view).
    c = cross(pdir, sdir)
    c_z = c.Z if hasattr(c, 'Z') else 0.0

    if c_z > 0.01:
        side = "left"
    elif c_z < -0.01:
        side = "right"
    else:
        side = "unknown"

    # Flip X — primary direction versus global +X
    try:
        from Autodesk.Revit.DB import XYZ  # type: ignore
        world_x = XYZ(1.0, 0.0, 0.0)
        flip_x  = dot(pdir, world_x) < 0.0
    except Exception:
        flip_x = False

    # Flip Y — secondary approaches from right side
    flip_y = (side == "right")

    # Rotation: angle of secondary direction from primary direction (in plan)
    angle_deg = joint_record.angle_deg
    rotation  = _compute_rotation_deg(pdir, sdir)

    # At-end check — is the secondary connecting at its own endpoint?
    at_end = joint_record.connection_end in (CONNECTION_END_START, CONNECTION_END_END)

    return {
        "flip_x":    flip_x,
        "flip_y":    flip_y,
        "rotation":  rotation,
        "side":      side,
        "at_end":    at_end,
        "angle_deg": angle_deg,
    }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _compute_rotation_deg(primary_dir, secondary_dir):
    """Compute the in-plane rotation of the secondary relative to the primary.

    Projects both vectors onto the XY plane and computes the signed angle.
    Returns a value in [0, 360).

    Args:
        primary_dir:   XYZ (unit)
        secondary_dir: XYZ (unit)

    Returns:
        float — degrees
    """
    # Project onto XY plane
    px, py = primary_dir.X, primary_dir.Y
    sx, sy = secondary_dir.X, secondary_dir.Y

    # Angle of primary from +X
    angle_p = math.atan2(py, px)
    # Angle of secondary from +X
    angle_s = math.atan2(sy, sx)

    # Relative angle secondary vs primary
    diff = math.degrees(angle_s - angle_p)

    # Normalise to [0, 360)
    diff = diff % 360.0
    return diff
