# -*- coding: utf-8 -*-
"""
connection_placer.py — Structural connection creation via the Revit API.

ApplyConnections.pushbutton / core/
Urbana Bridge Generator — Revit 2022 / 2024.3  |  pyRevit 6.4.0  |  IronPython 2.7

Responsibilities:
    1. Duplicate detection — check if the same structural connection already
       exists between the participating members.
    2. Connection creation — call StructuralConnectionHandler.Create() with
       the correct member IDs and connection type.
    3. Result reporting — return a structured result dict per joint.

IMPORTANT: This module does NOT manage transactions. The caller (window code-behind)
is responsible for wrapping calls in a Transaction with correct lifecycle management.

Revit API:
    Autodesk.Revit.DB.Structure.StructuralConnectionHandler
    Autodesk.Revit.DB.Structure.StructuralConnectionHandlerType

Both classes are present in Revit 2022 and Revit 2024.3.

Python compatibility: IronPython 2.7 — no f-strings, no dataclasses.
"""

import clr
clr.AddReference("RevitAPI")

from Autodesk.Revit.DB import FilteredElementCollector  # type: ignore

from core.revit_compat        import element_id_value
from core.connection_orientation import compute_orientation


# ---------------------------------------------------------------------------
# API availability
# ---------------------------------------------------------------------------

_HANDLER_CLASS = None

try:
    from Autodesk.Revit.DB.Structure import StructuralConnectionHandler  # type: ignore
    _HANDLER_CLASS = StructuralConnectionHandler
except (ImportError, Exception):
    _HANDLER_CLASS = None


def is_placement_available():
    """Return True if StructuralConnectionHandler is importable."""
    return _HANDLER_CLASS is not None


# ---------------------------------------------------------------------------
# Result constants
# ---------------------------------------------------------------------------

RESULT_CREATED  = "created"
RESULT_SKIPPED  = "skipped_existing"
RESULT_FAILED   = "failed"
RESULT_UNAVAIL  = "api_unavailable"


# ---------------------------------------------------------------------------
# Duplicate detection
# ---------------------------------------------------------------------------

def check_existing_connection(doc, primary_id, secondary_id):
    """Return True if a StructuralConnectionHandler already links both members.

    Scans all existing StructuralConnectionHandler elements and checks
    whether both primary_id and secondary_id are in each handler's
    connected member list.

    Args:
        doc:          Autodesk.Revit.DB.Document
        primary_id:   ElementId
        secondary_id: ElementId

    Returns:
        bool
    """
    if not is_placement_available():
        return False

    primary_int   = element_id_value(primary_id)
    secondary_int = element_id_value(secondary_id)

    try:
        collector = (
            FilteredElementCollector(doc)
            .OfClass(_HANDLER_CLASS)
        )
        for conn in collector:
            try:
                connected = conn.GetConnectedElements()
                connected_ints = set()
                for cid in connected:
                    connected_ints.add(element_id_value(cid))

                if primary_int in connected_ints and secondary_int in connected_ints:
                    return True
            except Exception:
                continue
    except Exception:
        pass

    return False


# ---------------------------------------------------------------------------
# Connection placement
# ---------------------------------------------------------------------------

def apply_beam_beam_connection(doc, joint_record, connection_type_id, logger=None):
    """Create a structural connection for one detected joint.

    Pre-conditions (must be checked by caller before this function):
      - A valid Transaction is already started.
      - connection_type_id is a valid ElementId of a StructuralConnectionHandlerType.

    This function:
      1. Checks for an existing duplicate connection.
      2. Builds the member ID list [primary, secondary].
      3. Calls StructuralConnectionHandler.Create().
      4. Returns a result dict.

    Args:
        doc:                Autodesk.Revit.DB.Document
        joint_record:       JointRecord
        connection_type_id: ElementId of StructuralConnectionHandlerType
        logger:             ApplyConnectionsLogger or None

    Returns:
        dict with keys:
            'status':        str  — RESULT_* constant
            'connection_id': int or None  — created connection's ElementId int value
            'message':       str  — human-readable description
            'joint':         JointRecord
    """
    result = {
        "status":        RESULT_FAILED,
        "connection_id": None,
        "message":       "",
        "joint":         joint_record,
    }

    # --- API availability ---
    if not is_placement_available():
        result["status"]  = RESULT_UNAVAIL
        result["message"] = (
            "StructuralConnectionHandler is not available in this Revit installation. "
            "Ensure the structural connections module is licensed and loaded."
        )
        if logger:
            logger.error("StructuralConnectionHandler unavailable")
        return result

    primary_id   = joint_record.primary_id
    secondary_id = joint_record.secondary_id

    # --- Duplicate check ---
    try:
        already_exists = check_existing_connection(doc, primary_id, secondary_id)
    except Exception as ex:
        already_exists = False
        if logger:
            logger.warning("Duplicate check failed; proceeding", error=str(ex))

    if already_exists:
        result["status"]  = RESULT_SKIPPED
        result["message"] = (
            "Connection already exists between members {0} and {1}. Skipped.".format(
                element_id_value(primary_id),
                element_id_value(secondary_id),
            )
        )
        if logger:
            logger.info("Duplicate connection detected — skipped",
                        **joint_record.diagnostic_dict())
        return result

    # --- Compute orientation hints (available for future use / logging) ---
    try:
        orientation = compute_orientation(joint_record)
    except Exception:
        orientation = {}

    if logger:
        logger.debug(
            "Orientation computed",
            orientation=orientation,
            **joint_record.diagnostic_dict()
        )

    # --- Build member ID list ---
    # Convention: primary first, secondary second
    try:
        from System.Collections.Generic import List as NetList  # type: ignore
        from Autodesk.Revit.DB import ElementId               # type: ignore

        member_ids = NetList[ElementId]()
        member_ids.Add(primary_id)
        member_ids.Add(secondary_id)
    except Exception as ex:
        result["message"] = "Failed to build member ID list: {0}".format(ex)
        if logger:
            logger.error(result["message"], exc=ex)
        return result

    # --- Create the connection ---
    try:
        new_conn = _HANDLER_CLASS.Create(doc, member_ids, connection_type_id)

        conn_int = element_id_value(new_conn.Id) if new_conn is not None else None

        result["status"]        = RESULT_CREATED
        result["connection_id"] = conn_int
        result["message"] = (
            "Connection created between {0} (primary) and {1} (secondary). "
            "Connection ID: {2}.".format(
                element_id_value(primary_id),
                element_id_value(secondary_id),
                conn_int,
            )
        )
        if logger:
            logger.info(
                "Connection created",
                connection_id=conn_int,
                **joint_record.diagnostic_dict()
            )

    except Exception as ex:
        result["message"] = (
            "Failed to create connection between {0} and {1}: {2}".format(
                element_id_value(primary_id),
                element_id_value(secondary_id),
                str(ex),
            )
        )
        if logger:
            logger.error(result["message"], exc=ex)

    return result


# ---------------------------------------------------------------------------
# Batch placement
# ---------------------------------------------------------------------------

def apply_beam_beam_connections_batch(doc, joints, connection_type_id, logger=None):
    """Apply connections for a list of JointRecords within a single transaction.

    IMPORTANT: The caller is responsible for the Transaction lifecycle.
    This function must be called inside a started Transaction.

    Args:
        doc:                Autodesk.Revit.DB.Document
        joints:             list[JointRecord]
        connection_type_id: ElementId of StructuralConnectionHandlerType
        logger:             ApplyConnectionsLogger or None

    Returns:
        list[dict]  — one result dict per joint (see apply_beam_beam_connection)
    """
    results = []
    for joint in joints:
        result = apply_beam_beam_connection(doc, joint, connection_type_id, logger)
        results.append(result)
    return results
