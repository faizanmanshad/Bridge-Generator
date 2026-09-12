# -*- coding: utf-8 -*-
"""
external_event_handler.py — Revit External Event handler for Apply Connections.

ApplyConnections.pushbutton / core/
Urbana Bridge Generator — Revit 2022 / 2024.3  |  pyRevit 6.4.0  |  IronPython 2.7

Decouples Revit document modification from the WPF modeless window thread.
"""

import clr  # type: ignore
import os
import json
clr.AddReference("RevitAPI")
clr.AddReference("RevitAPIUI")

from Autodesk.Revit.UI import IExternalEventHandler  # type: ignore
from Autodesk.Revit.DB import Transaction, TransactionStatus  # type: ignore

from core.logging_utils import get_logger
from core.validation import validate_main_beam, validate_joints
from core.joint_detector import detect_beam_beam_joints, detect_bearer_beam_joints
from core.connection_placer import (
    apply_beam_beam_connections_batch,
    apply_bearer_beam_connections_batch,
    RESULT_CREATED,
    RESULT_SKIPPED,
    RESULT_FAILED,
    RESULT_UNAVAIL,
)

REQUEST_NONE             = "REQUEST_NONE"
REQUEST_APPLY_BEAM_BEAM  = "REQUEST_APPLY_BEAM_BEAM"
REQUEST_APPLY_BEARER_BEAM = "REQUEST_APPLY_BEARER_BEAM"


class ApplyConnectionsExternalEventHandler(IExternalEventHandler):
    """Executes requests inside a valid Revit API context."""

    def __init__(self):
        self.request_type = REQUEST_NONE
        self.bridge_type = None
        self.connection_type_id = None
        self.reverse_direction = False  # False = Default, True = Reversed

        # Beam-Beam: reference main beam ElementId
        self.main_beam_id = None

        # Bearer-Beam: reference beam ElementId + reference bearer ElementId
        self.ref_beam_id   = None
        self.ref_bearer_id = None

        self.logger = get_logger()
        # Callback to update the WPF window UI thread
        self.result_callback = None
        self.last_error_data = None

    def Execute(self, uiapp):
        """Called by Revit when the ExternalEvent is raised."""
        doc = uiapp.ActiveUIDocument.Document
        
        try:
            if self.request_type == REQUEST_APPLY_BEAM_BEAM:
                self._execute_apply_beam_beam(doc)
            elif self.request_type == REQUEST_APPLY_BEARER_BEAM:
                self._execute_apply_bearer_beam(doc)
            else:
                self.logger.warning(
                    "ExternalEvent raised with unhandled request_type: {0}".format(
                        self.request_type
                    )
                )

        except Exception as ex:
            self.logger.error("Unhandled exception in ExternalEvent", exc=ex)
            self._report_error(ex, "Unexpected error in external event handler")

        finally:
            self.request_type = REQUEST_NONE

    def GetName(self):
        return "Urbana ApplyConnections External Event Handler"

    def _report_error(self, ex, custom_message="Connection placement failed"):
        """Save error state and notify UI callback."""
        import traceback as tb_module
        
        msg = "{0}:\n{1}".format(custom_message, str(ex))
        
        self.last_error_data = {
            "operation": self.request_type,
            "exception_type": type(ex).__name__,
            "exception_message": str(ex),
            "traceback": tb_module.format_exc(),
            "connection_type_id": getattr(self.connection_type_id, "IntegerValue", None) if self.connection_type_id else None,
            "main_beam_id": getattr(self.main_beam_id, "IntegerValue", None) if self.main_beam_id else None,
        }
        
        try:
            dump_path = os.path.join(os.path.dirname(__file__), "..", "last_error.json")
            with open(dump_path, 'w') as f:
                json.dump(self.last_error_data, f, indent=4)
        except Exception:
            pass

        if self.result_callback:
            self.result_callback(self.bridge_type, {"status": "error", "message": msg})

    def _execute_apply_beam_beam(self, doc):
        """Execute the Beam-Beam connection pipeline."""
        self.last_error_data = None
        
        # 1. Validate Main Beam
        ok, msg, primary_elem = validate_main_beam(doc, self.main_beam_id)
        if not ok:
            if self.result_callback:
                self.result_callback(self.bridge_type, {"status": "error", "message": msg})
            return

        # 2. Detect Joints
        joints = []
        matching_count = 0
        valid_splice_count = 0
        try:
            joints, matching_count, valid_splice_count = detect_beam_beam_joints(
                doc,
                primary_elem,
                self.bridge_type,
                logger=self.logger,
            )
        except Exception as ex:
            self._report_error(ex, "Joint detection failed")
            return

        # 3. Validate Joints
        ok, msg = validate_joints(joints)
        if not ok:
            if self.result_callback:
                self.result_callback(self.bridge_type, {"status": "warning", "message": "No joints detected."})
            return

        # 4. Apply Connections in Transaction
        results = []
        t = None
        try:
            t = Transaction(doc, "Urbana — Apply Beam-Beam Connections")
            t.Start()

            results = apply_beam_beam_connections_batch(
                doc,
                joints,
                self.connection_type_id,
                reverse_direction=self.reverse_direction,
                logger=self.logger,
            )

            t.Commit()
            
        except Exception as ex:
            if t is not None:
                try:
                    if t.GetStatus() == TransactionStatus.Started:
                        t.RollBack()
                except Exception:
                    pass
            self._report_error(ex)
            return

        # 5. Report Results
        n_created  = sum(1 for r in results if r["status"] == RESULT_CREATED)
        n_skipped  = sum(1 for r in results if r["status"] == RESULT_SKIPPED)
        n_failed   = sum(1 for r in results if r["status"] == RESULT_FAILED)
        n_unavail  = sum(1 for r in results if r["status"] == RESULT_UNAVAIL)

        direction_label = "Reversed" if self.reverse_direction else "Default"
        summary_lines = [
            "Matching beam instances: {0}".format(matching_count),
            "Valid same-type splice joints: {0}".format(valid_splice_count),
            "Direction: {0}".format(direction_label),
            "Connections created: {0}".format(n_created),
            "Skipped (existing):  {0}".format(n_skipped),
            "Failed:              {0}".format(n_failed),
        ]
        if n_unavail:
            summary_lines.append("API unavailable:     {0}".format(n_unavail))

        summary = "\n".join(summary_lines)
        
        status_code = "success"
        if n_failed > 0 or n_unavail > 0:
            status_code = "warning"
        elif n_created == 0:
            status_code = "neutral"

        footer_msg = "Done — {0} created, {1} skipped, {2} failed.".format(
            n_created, n_skipped, n_failed
        )

        if self.result_callback:
            self.result_callback(self.bridge_type, {
                "status": status_code,
                "message": summary,
                "footer": footer_msg
            })

    def _execute_apply_bearer_beam(self, doc):
        """Execute the Bearer-Beam connection pipeline."""
        self.last_error_data = None

        # 1. Validate Reference Beam
        ok, msg, beam_elem = validate_main_beam(doc, self.ref_beam_id)
        if not ok:
            err_msg = "Reference Beam invalid: {0}".format(msg)
            if self.result_callback:
                self.result_callback(self.bridge_type, {"status": "error", "message": err_msg})
            return

        # 2. Validate Reference Bearer
        ok, msg, bearer_elem = validate_main_beam(doc, self.ref_bearer_id)
        if not ok:
            err_msg = "Reference Bearer invalid: {0}".format(msg)
            if self.result_callback:
                self.result_callback(self.bridge_type, {"status": "error", "message": err_msg})
            return

        # 3. Detect joints
        joints = []
        beam_count = bearer_count = valid_joint_count = 0
        try:
            joints, beam_count, bearer_count, valid_joint_count = detect_bearer_beam_joints(
                doc,
                beam_elem,
                bearer_elem,
                self.bridge_type,
                logger=self.logger,
            )
        except Exception as ex:
            self._report_error(ex, "Bearer-Beam joint detection failed")
            return

        # 4. Guard: same TypeId → ambiguous roles (detector already returns [] in this case)
        if not joints and beam_count == 0 and bearer_count == 0:
            msg = (
                "Both references share the same Revit TypeId.\n"
                "Cannot distinguish Beam from Bearer roles.\n"
                "Please select references of different types."
            )
            if self.result_callback:
                self.result_callback(self.bridge_type, {"status": "error", "message": msg})
            return

        # 5. Guard: populations found but no joints
        if not joints:
            summary = (
                "Matching beam instances:   {0}\n"
                "Matching bearer instances: {1}\n"
                "No valid Bearer-Beam joints detected.\n"
                "Check that bearer endpoints reach the beam centerline "
                "within the search tolerance."
            ).format(beam_count, bearer_count)
            if self.result_callback:
                self.result_callback(
                    self.bridge_type, {"status": "warning", "message": summary}
                )
            return

        # 6. Apply connections in a Transaction
        results = []
        t = None
        try:
            t = Transaction(doc, "Urbana — Apply Bearer-Beam Connections")
            t.Start()

            results = apply_bearer_beam_connections_batch(
                doc,
                joints,
                self.connection_type_id,
                reverse_direction=self.reverse_direction,
                logger=self.logger,
            )

            t.Commit()

        except Exception as ex:
            if t is not None:
                try:
                    if t.GetStatus() == TransactionStatus.Started:
                        t.RollBack()
                except Exception:
                    pass
            self._report_error(ex, "Bearer-Beam connection placement failed")
            return

        # 7. Report results
        n_created = sum(1 for r in results if r["status"] == RESULT_CREATED)
        n_skipped = sum(1 for r in results if r["status"] == RESULT_SKIPPED)
        n_failed  = sum(1 for r in results if r["status"] == RESULT_FAILED)
        n_unavail = sum(1 for r in results if r["status"] == RESULT_UNAVAIL)

        direction_label = "Reversed" if self.reverse_direction else "Default"
        summary_lines = [
            "Matching beam instances:   {0}".format(beam_count),
            "Matching bearer instances: {0}".format(bearer_count),
            "Valid Bearer-Beam joints:  {0}".format(valid_joint_count),
            "Direction: {0}".format(direction_label),
            "Connections created: {0}".format(n_created),
            "Skipped (existing):  {0}".format(n_skipped),
            "Failed:              {0}".format(n_failed),
        ]
        if n_unavail:
            summary_lines.append("API unavailable:     {0}".format(n_unavail))

        summary = "\n".join(summary_lines)

        status_code = "success"
        if n_failed > 0 or n_unavail > 0:
            status_code = "warning"
        elif n_created == 0:
            status_code = "neutral"

        footer_msg = "Done — {0} created, {1} skipped, {2} failed.".format(
            n_created, n_skipped, n_failed
        )

        if self.result_callback:
            self.result_callback(self.bridge_type, {
                "status":  status_code,
                "message": summary,
                "footer":  footer_msg,
            })
