# -*- coding: utf-8 -*-
"""
joint_geometry.py — Pure geometry utilities for structural joint detection.

ApplyConnections.pushbutton / core/
Urbana Bridge Generator — Revit 2022 / 2024.3  |  pyRevit 6.4.0  |  IronPython 2.7

All functions operate on Autodesk.Revit.DB.XYZ objects and plain Python numbers.
No Revit document access is performed here — this module is purely mathematical.

Revit internal units:
    1 Revit internal unit ≈ 1 foot (304.8 mm)

Proximity tolerance is defined as a module constant in internal units.
It can be overridden by callers as needed.

Python compatibility: IronPython 2.7 — no f-strings, no dataclasses.
"""

import math
import clr
clr.AddReference("RevitAPI")

# ---------------------------------------------------------------------------
# Tolerance constants — in Revit internal units (feet)
# ---------------------------------------------------------------------------

# Default proximity tolerance: ~150 mm ≈ 0.492 ft
# A secondary beam endpoint within this distance of the primary curve qualifies.
DEFAULT_PROXIMITY_TOLERANCE = 0.5   # Revit internal units (~152 mm)

# Angular tolerance for perpendicularity / parallel checks
DEFAULT_ANGLE_TOLERANCE_DEG = 15.0


# ---------------------------------------------------------------------------
# XYZ arithmetic helpers
# ---------------------------------------------------------------------------

def subtract(a, b):
    """Return (a - b) as a new XYZ."""
    return a.__class__(a.X - b.X, a.Y - b.Y, a.Z - b.Z)


def add(a, b):
    """Return (a + b) as a new XYZ."""
    return a.__class__(a.X + b.X, a.Y + b.Y, a.Z + b.Z)


def scale(v, factor):
    """Return v * factor as a new XYZ."""
    return v.__class__(v.X * factor, v.Y * factor, v.Z * factor)


def length(v):
    """Return the magnitude of an XYZ vector."""
    return math.sqrt(v.X * v.X + v.Y * v.Y + v.Z * v.Z)


def normalize(v):
    """Return a unit-length version of v. Returns v unchanged if near-zero."""
    mag = length(v)
    if mag < 1e-10:
        return v
    return v.__class__(v.X / mag, v.Y / mag, v.Z / mag)


def dot(a, b):
    """Return the dot product of two XYZ vectors."""
    return a.X * b.X + a.Y * b.Y + a.Z * b.Z


def cross(a, b):
    """Return the cross product of two XYZ vectors."""
    return a.__class__(
        a.Y * b.Z - a.Z * b.Y,
        a.Z * b.X - a.X * b.Z,
        a.X * b.Y - a.Y * b.X,
    )


def distance_point_to_point(a, b):
    """Return the Euclidean distance between two XYZ points."""
    dx = a.X - b.X
    dy = a.Y - b.Y
    dz = a.Z - b.Z
    return math.sqrt(dx * dx + dy * dy + dz * dz)


# ---------------------------------------------------------------------------
# Curve / segment helpers
# ---------------------------------------------------------------------------

def get_start_end(curve):
    """Return (start_XYZ, end_XYZ) for a Revit Curve.

    Args:
        curve: Autodesk.Revit.DB.Curve (typically a Line).

    Returns:
        (XYZ, XYZ) tuple.
    """
    return curve.GetEndPoint(0), curve.GetEndPoint(1)


def curve_direction(start, end):
    """Return the normalised direction vector from start to end.

    Args:
        start: XYZ
        end:   XYZ

    Returns:
        XYZ (unit vector) or XYZ(0,0,0) if degenerate.
    """
    return normalize(subtract(end, start))


def closest_point_on_segment(point, seg_start, seg_end):
    """Return the point on the line segment [seg_start, seg_end] closest to point.

    Uses the standard parametric projection formula.
    The result is clamped to [0, 1] so it stays on the segment.

    Args:
        point:     XYZ — the query point.
        seg_start: XYZ — segment start.
        seg_end:   XYZ — segment end.

    Returns:
        XYZ — closest point on the segment.
    """
    seg_vec = subtract(seg_end, seg_start)
    seg_len_sq = dot(seg_vec, seg_vec)

    if seg_len_sq < 1e-14:
        # Degenerate segment — return start
        return seg_start

    to_point = subtract(point, seg_start)
    t = dot(to_point, seg_vec) / seg_len_sq

    # Clamp to segment
    if t < 0.0:
        t = 0.0
    elif t > 1.0:
        t = 1.0

    return add(seg_start, scale(seg_vec, t))


def distance_point_to_segment(point, seg_start, seg_end):
    """Return the shortest distance from point to the segment [seg_start, seg_end].

    Args:
        point:     XYZ
        seg_start: XYZ
        seg_end:   XYZ

    Returns:
        float — distance in Revit internal units.
    """
    closest = closest_point_on_segment(point, seg_start, seg_end)
    return distance_point_to_point(point, closest)


def parametric_position(point, seg_start, seg_end):
    """Return the t-parameter [0..1] of the projection of point onto the segment.

    0.0 = at seg_start, 1.0 = at seg_end, 0.5 = midpoint.
    Values outside [0,1] mean the projection falls beyond the segment ends.

    Args:
        point:     XYZ
        seg_start: XYZ
        seg_end:   XYZ

    Returns:
        float — unclamped parametric position.
    """
    seg_vec    = subtract(seg_end, seg_start)
    seg_len_sq = dot(seg_vec, seg_vec)
    if seg_len_sq < 1e-14:
        return 0.0
    to_point = subtract(point, seg_start)
    return dot(to_point, seg_vec) / seg_len_sq


# ---------------------------------------------------------------------------
# Angular helpers
# ---------------------------------------------------------------------------

def angle_between_rad(v1, v2):
    """Return the angle between two vectors in radians.

    Uses the absolute dot product so the result is always [0, π/2].
    (We treat anti-parallel beams as parallel for connection purposes.)

    Args:
        v1, v2: XYZ — need not be unit vectors.

    Returns:
        float — angle in radians [0, π/2].
    """
    n1 = normalize(v1)
    n2 = normalize(v2)
    d  = abs(dot(n1, n2))
    # Clamp to [-1, 1] to guard against floating-point overshoot
    d = min(1.0, max(-1.0, d))
    return math.acos(d)


def angle_between_deg(v1, v2):
    """Return the angle between two vectors in degrees [0, 90]."""
    return math.degrees(angle_between_rad(v1, v2))


def is_perpendicular(v1, v2, tol_deg=DEFAULT_ANGLE_TOLERANCE_DEG):
    """Return True if v1 and v2 are approximately perpendicular.

    Args:
        v1, v2:  XYZ vectors.
        tol_deg: angular tolerance in degrees (default 15°).

    Returns:
        bool
    """
    angle = angle_between_deg(v1, v2)
    return abs(angle - 90.0) <= tol_deg


def is_parallel(v1, v2, tol_deg=DEFAULT_ANGLE_TOLERANCE_DEG):
    """Return True if v1 and v2 are approximately parallel (or anti-parallel).

    Args:
        v1, v2:  XYZ vectors.
        tol_deg: angular tolerance in degrees (default 15°).

    Returns:
        bool
    """
    angle = angle_between_deg(v1, v2)
    return angle <= tol_deg
