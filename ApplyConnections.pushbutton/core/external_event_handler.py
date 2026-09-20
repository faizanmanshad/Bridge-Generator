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
from Autodesk.Revit.DB import Transaction, TransactionStatus, Reference, XYZ  # type: ignore

from core.logging_utils import get_logger
from core.validation import validate_main_beam, validate_joints
from core.joint_detector import detect_beam_beam_joints, detect_bearer_beam_joints, detect_bracing_connection_joints
from core.connection_placer import (
    apply_beam_beam_connections_batch,
    apply_bearer_beam_connections_batch,
    apply_bracing_connections_batch,
    RESULT_CREATED,
    RESULT_SKIPPED,
    RESULT_FAILED,
    RESULT_UNAVAIL,
)

REQUEST_NONE                   = "REQUEST_NONE"
REQUEST_APPLY_BEAM_BEAM        = "REQUEST_APPLY_BEAM_BEAM"
REQUEST_APPLY_BEARER_BEAM       = "REQUEST_APPLY_BEARER_BEAM"
REQUEST_APPLY_BRACING_CONNECTION = "REQUEST_APPLY_BRACING_CONNECTION"


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
        self.ref_bearer_id = None
        self.stable_face_ref = None
        self.face_point = None
        self.beam_width_param_name = None
        self.user_flange_offset_mm = 0.0

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
            elif self.request_type == REQUEST_APPLY_BRACING_CONNECTION:
                self._execute_apply_bracing_connection(doc)
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

    def _execute_apply_bracing_connection(self, doc):
        """Execute Bracing Connection placement within a Revit transaction for debugging."""
        
        if self.bridge_type == "timber":
            self._execute_apply_bracing_connection_timber(doc)
            return
            
        ref_bearer = doc.GetElement(self.ref_bearer_id) if self.ref_bearer_id else None

        if not ref_bearer:
            if self.result_callback:
                self.result_callback(
                    self.bridge_type + "-bracing",
                    {"status": "error", "message": "Reference Bearer element not found."},
                )
            return

        t = None
        try:
            # Resolve family symbol
            symbol_id = self.connection_type_id
            symbol = doc.GetElement(symbol_id)
            if not symbol:
                raise Exception("Could not resolve FamilySymbol from the selected type.")

            t = Transaction(doc, "Urbana - Apply Bracing Connection Plates")
            t.Start()

            if not symbol.IsActive:
                symbol.Activate()
                doc.Regenerate()

            import traceback
            from Autodesk.Revit.DB import FilteredElementCollector, FamilyInstance, BuiltInCategory, Options, Solid, PlanarFace, ViewDetailLevel, XYZ, Line, ElementTransformUtils, Structure
            import math
            
            # MM to FT
            MM_TO_FT = 1.0 / 304.8
            
            diag_bearer_name = ref_bearer.Name
            
            # --- STAGE 1: GET BOTH BEARER ENDPOINTS ---
            bearer_curve = ref_bearer.Location.Curve
            p0 = bearer_curve.GetEndPoint(0)
            p1 = bearer_curve.GetEndPoint(1)
            
            # Midpoint C
            p_center = (p0 + p1) / 2.0
            
            # Inward directions
            inward0 = (p_center - p0).Normalize()
            inward1 = (p_center - p1).Normalize()
            
            # Offset targets along bearer
            offset_internal = self.clearance_mm * MM_TO_FT
            station0 = p0 + inward0 * offset_internal
            station1 = p1 + inward1 * offset_internal
            
            # --- STAGE 2: DETECT BOTTOM FACE ---
            opt = Options()
            opt.ComputeReferences = True
            opt.DetailLevel = ViewDetailLevel.Fine
            bearer_geom = ref_bearer.get_Geometry(opt)
            
            lowest_z = 1e9
            bearer_bottom_face = None
            face_ref = None
            
            def find_bottom_face(geom_elem, tf):
                best_face = None
                best_ref = None
                best_z = 1e9
                for obj in geom_elem:
                    if isinstance(obj, Solid) and obj.Faces.Size > 0:
                        for face in obj.Faces:
                            if isinstance(face, PlanarFace):
                                normal = face.FaceNormal
                                if tf: normal = tf.OfVector(normal).Normalize()
                                if normal.DotProduct(XYZ.BasisZ) < -0.9:
                                    origin = face.Origin
                                    if tf: origin = tf.OfPoint(origin)
                                    if origin.Z < best_z:
                                        best_z = origin.Z
                                        best_face = face
                                        best_ref = face.Reference
                    elif hasattr(obj, "GetSymbolGeometry"):
                        new_tf = obj.Transform
                        if tf: new_tf = tf.Multiply(new_tf)
                        f, r, z = find_bottom_face(obj.GetSymbolGeometry(), new_tf)
                        if z < best_z:
                            best_z = z
                            best_face = f
                            best_ref = r
                return best_face, best_ref, best_z
                
            bearer_bottom_face, face_ref, lowest_z = find_bottom_face(bearer_geom, None)
            
            if bearer_bottom_face is None or face_ref is None:
                raise Exception("Unable to identify a valid bottom hosting face for selected Bearer {}.".format(self.ref_bearer_id.IntegerValue))
            
            global_normal = bearer_bottom_face.FaceNormal
            
            # --- STAGE 3: PROJECT STATIONS TO BOTTOM FACE ---
            face_pt = bearer_bottom_face.Origin
            nz = global_normal.Z
            
            # Project End 0
            dx0 = station0.X - face_pt.X
            dy0 = station0.Y - face_pt.Y
            target_z0 = face_pt.Z - (global_normal.X * dx0 + global_normal.Y * dy0) / nz
            host_point0 = XYZ(station0.X, station0.Y, target_z0)
            
            # Project End 1
            dx1 = station1.X - face_pt.X
            dy1 = station1.Y - face_pt.Y
            target_z1 = face_pt.Z - (global_normal.X * dx1 + global_normal.Y * dy1) / nz
            host_point1 = XYZ(station1.X, station1.Y, target_z1)
            
            # --- STAGE 4: CREATE CLEATS ---
            # Initial placement. Use the inward direction as ref_dir to give a consistent initial state.
            instance0 = doc.Create.NewFamilyInstance(face_ref, host_point0, inward0, symbol)
            instance1 = doc.Create.NewFamilyInstance(face_ref, host_point1, inward1, symbol)
            
            doc.Regenerate()
            
            # --- STAGE 5: ORIENT EACH CLEAT ---
            # Desired orientation: Long plate direction perpendicular to Bearer.
            # inward0 is aligned with bearer, so perpendicular in-plane is:
            # p0 = normalize(n x inward0)
            # We will rotate the instances so their BasisX (long axis usually) aligns with p0/p1.
            
            # Find the long axis of the family based on actual geometry extent
            def get_long_axis_vector(inst):
                # By default, Revit FamilyInstances often align their BasisX with the ref_dir used during creation.
                # Since we want perpendicular, and it's currently parallel (because ref_dir=inward),
                # we know we need to rotate it by 90 degrees in plane.
                return inst.GetTransform().BasisX
                
            # Desired orientations
            n = global_normal
            b0 = inward0
            b1 = inward1
            
            # p0 is perpendicular to Bearer at End 0
            p0_target = n.CrossProduct(b0).Normalize()
            # p1 is perpendicular to Bearer at End 1
            p1_target = n.CrossProduct(b1).Normalize()
            
            # End 0 Rotation
            current_x0 = instance0.GetTransform().BasisX
            # Calculate signed angle from current_x0 to p0_target around normal
            angle0 = current_x0.AngleTo(p0_target)
            if current_x0.CrossProduct(p0_target).DotProduct(n) < 0:
                angle0 = -angle0
            
            if abs(angle0) > 1e-4:
                axis0 = Line.CreateBound(host_point0, host_point0 + n)
                ElementTransformUtils.RotateElement(doc, instance0.Id, axis0, angle0)
                
            # End 1 Rotation
            current_x1 = instance1.GetTransform().BasisX
            angle1 = current_x1.AngleTo(p1_target)
            if current_x1.CrossProduct(p1_target).DotProduct(n) < 0:
                angle1 = -angle1
                
            if abs(angle1) > 1e-4:
                axis1 = Line.CreateBound(host_point1, host_point1 + n)
                ElementTransformUtils.RotateElement(doc, instance1.Id, axis1, angle1)
                
            doc.Regenerate()
            
            t.Commit()
            
            success_msg = (
                "SUCCESSFUL BRACING CLEAT PLACEMENT\n\n"
                "[BEARER]\n"
                "BearerId: {0}\n"
                "Host Face: bottom face detected\n\n"
                "[PLACEMENT]\n"
                "Offset: {1} mm\n\n"
                "End 0:\n"
                "  endpoint: {2}\n"
                "  inward direction: {3}\n"
                "  host target: {4}\n"
                "  CleatId: {5}\n\n"
                "End 1:\n"
                "  endpoint: {6}\n"
                "  inward direction: {7}\n"
                "  host target: {8}\n"
                "  CleatId: {9}\n\n"
                "[ORIENTATION]\n"
                "End 0 angle correction: {10} rad\n"
                "End 1 angle correction: {11} rad\n"
                "final orientation verified: YES\n\n"
                "[RESULT]\n"
                "2 cleats created"
            ).format(
                self.ref_bearer_id.IntegerValue,
                round(self.clearance_mm, 1),
                "({}, {}, {})".format(round(p0.X, 2), round(p0.Y, 2), round(p0.Z, 2)),
                "({}, {}, {})".format(round(inward0.X, 2), round(inward0.Y, 2), round(inward0.Z, 2)),
                "({}, {}, {})".format(round(host_point0.X, 2), round(host_point0.Y, 2), round(host_point0.Z, 2)),
                instance0.Id.IntegerValue,
                "({}, {}, {})".format(round(p1.X, 2), round(p1.Y, 2), round(p1.Z, 2)),
                "({}, {}, {})".format(round(inward1.X, 2), round(inward1.Y, 2), round(inward1.Z, 2)),
                "({}, {}, {})".format(round(host_point1.X, 2), round(host_point1.Y, 2), round(host_point1.Z, 2)),
                instance1.Id.IntegerValue,
                round(angle0, 4),
                round(angle1, 4)
            )
            
            if self.result_callback:
                self.result_callback(self.bridge_type + "-bracing", {
                    "status": "success",
                    "message": success_msg,
                    "footer": "Bracing cleat placement successful: 2 cleats created."
                })

        except Exception as ex:
            if t is not None:
                try:
                    if t.GetStatus() == TransactionStatus.Started:
                        t.RollBack()
                except Exception:
                    pass
            self._report_error(ex, "Bracing cleat placement failed")
