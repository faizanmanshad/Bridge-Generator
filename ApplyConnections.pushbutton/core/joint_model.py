# -*- coding: utf-8 -*-
"""
joint_model.py — JointRecord data model.

ApplyConnections.pushbutton / core/
Urbana Bridge Generator — Revit 2022 / 2024.3  |  pyRevit 6.4.0  |  IronPython 2.7

JointRecord is the shared handoff object between:

    joint_detector   (produces JointRecords)
         ↓
    connection_placer (consumes JointRecords)

It is intentionally lightweight — a plain Python object with named attributes.
No ORM, no schema, no heavy framework.

Python compatibility: IronPython 2.7 — no f-strings, no dataclasses.
"""


# ---------------------------------------------------------------------------
# Connection end constants
# ---------------------------------------------------------------------------

CONNECTION_END_START  = "start"   # Secondary member's start endpoint
CONNECTION_END_END    = "end"     # Secondary member's end endpoint
CONNECTION_END_MID    = "mid"     # Somewhere along the member (not at an end)
CONNECTION_END_UNKNOWN = "unknown"


# ---------------------------------------------------------------------------
# JointRecord
# ---------------------------------------------------------------------------

class JointRecord(object):
    """Immutable record describing one detected structural joint.

    Attributes:
        primary_id:      ElementId  — the primary main beam.
        secondary_id:    ElementId  — the connected secondary member.
        joint_xyz:       XYZ        — best location for the joint.
        primary_dir:     XYZ        — normalised direction vector of primary beam.
        secondary_dir:   XYZ        — normalised direction vector of secondary beam.
        angle_deg:       float      — angle between primary and secondary (0–90°).
        connection_end:  str        — which end of the secondary is connected
                                     (CONNECTION_END_START / _END / _MID / _UNKNOWN).
        primary_t:       float      — parametric position [0,1] on primary curve
                                     where the joint falls.
        bridge_type:     str        — "concrete" or "timber".
        proximity_dist:  float      — distance used for detection (for diagnostics).
    """

    def __init__(
        self,
        primary_id,
        secondary_id,
        joint_xyz,
        primary_dir,
        secondary_dir,
        angle_deg,
        connection_end,
        primary_t=None,
        bridge_type="concrete",
        proximity_dist=None,
    ):
        self.primary_id     = primary_id
        self.secondary_id   = secondary_id
        self.joint_xyz      = joint_xyz
        self.primary_dir    = primary_dir
        self.secondary_dir  = secondary_dir
        self.angle_deg      = angle_deg
        self.connection_end = connection_end
        self.primary_t      = primary_t        # may be None
        self.bridge_type    = bridge_type
        self.proximity_dist = proximity_dist   # may be None

    def __repr__(self):
        from core.revit_compat import element_id_value
        try:
            pid = element_id_value(self.primary_id)
            sid = element_id_value(self.secondary_id)
        except Exception:
            pid = str(self.primary_id)
            sid = str(self.secondary_id)

        return (
            "JointRecord("
            "primary={0}, secondary={1}, "
            "end={2}, angle={3:.1f}deg, "
            "bridge={4}"
            ")".format(pid, sid, self.connection_end, self.angle_deg, self.bridge_type)
        )

    def diagnostic_dict(self):
        """Return a dictionary suitable for structured logging."""
        from core.revit_compat import element_id_value
        try:
            pid = element_id_value(self.primary_id)
            sid = element_id_value(self.secondary_id)
        except Exception:
            pid = str(self.primary_id)
            sid = str(self.secondary_id)

        jxyz = self.joint_xyz
        pdir = self.primary_dir
        sdir = self.secondary_dir

        return {
            "primary_id":     pid,
            "secondary_id":   sid,
            "joint_x":        round(jxyz.X, 4) if jxyz else None,
            "joint_y":        round(jxyz.Y, 4) if jxyz else None,
            "joint_z":        round(jxyz.Z, 4) if jxyz else None,
            "primary_dir_x":  round(pdir.X, 4) if pdir else None,
            "primary_dir_y":  round(pdir.Y, 4) if pdir else None,
            "secondary_dir_x": round(sdir.X, 4) if sdir else None,
            "secondary_dir_y": round(sdir.Y, 4) if sdir else None,
            "angle_deg":      round(self.angle_deg, 2),
            "connection_end": self.connection_end,
            "primary_t":      round(self.primary_t, 4) if self.primary_t is not None else None,
            "proximity_dist": round(self.proximity_dist, 6) if self.proximity_dist is not None else None,
            "bridge_type":    self.bridge_type,
        }
