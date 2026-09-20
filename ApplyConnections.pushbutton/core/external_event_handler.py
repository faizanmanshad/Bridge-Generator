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
        """Execute Bracing Connection placement within a Revit transaction with session tracking and back edge offset."""
        if not hasattr(self, "bracing_session_created"):
            self.bracing_session_created = {}

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
            
            MM_TO_FT = 1.0 / 304.8
            
            bearer_id_int = self.ref_bearer_id.IntegerValue
            
            # Read inputs
            offset_mm = self.clearance_mm
            rotation_deg = getattr(self, "rotation_deg", 360.0)
            back_edge_offset_mm = getattr(self, "back_edge_offset_mm", 5.0)
            
            # Session replacement logic
            session_data = self.bracing_session_created.get(bearer_id_int)
            if session_data:
                old_ids = session_data.get("element_ids", [])
                for eid in old_ids:
                    if doc.GetElement(eid):
                        doc.Delete(eid)
                        
            # --- STAGE 1: GET BOTH BEARER ENDPOINTS ---
            bearer_curve = ref_bearer.Location.Curve
            p0 = bearer_curve.GetEndPoint(0)
            p1 = bearer_curve.GetEndPoint(1)
            
            p_center = (p0 + p1) / 2.0
            
            bearer_dir = (p1 - p0).Normalize()
            
            inward0 = (p_center - p0).Normalize()
            inward1 = (p_center - p1).Normalize()
            
            offset_internal = offset_mm * MM_TO_FT
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
                best_gn = None
                best_gp = None
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
                                        best_gn = normal
                                        best_gp = origin
                    elif hasattr(obj, "GetSymbolGeometry"):
                        new_tf = obj.Transform
                        if tf: new_tf = tf.Multiply(new_tf)
                        f, r, z, gn, gp = find_bottom_face(obj.GetSymbolGeometry(), new_tf)
                        if z < best_z:
                            best_z = z
                            best_face = f
                            best_ref = r
                            best_gn = gn
                            best_gp = gp
                return best_face, best_ref, best_z, best_gn, best_gp
                
            bearer_bottom_face, face_ref, lowest_z, global_normal, face_pt = find_bottom_face(bearer_geom, None)
            
            if bearer_bottom_face is None or face_ref is None or global_normal is None:
                raise Exception("Unable to identify a valid bottom hosting face for selected Bearer.")
            
            # --- STAGE 3: PROJECT STATIONS TO BOTTOM FACE ---
            nz = global_normal.Z
            
            dx0 = station0.X - face_pt.X
            dy0 = station0.Y - face_pt.Y
            target_z0 = face_pt.Z - (global_normal.X * dx0 + global_normal.Y * dy0) / nz
            host_point0 = XYZ(station0.X, station0.Y, target_z0)
            
            dx1 = station1.X - face_pt.X
            dy1 = station1.Y - face_pt.Y
            target_z1 = face_pt.Z - (global_normal.X * dx1 + global_normal.Y * dy1) / nz
            host_point1 = XYZ(station1.X, station1.Y, target_z1)
            
            # --- STAGE 4: CREATE CLEATS ---
            instance0 = doc.Create.NewFamilyInstance(face_ref, host_point0, inward0, symbol)
            instance1 = doc.Create.NewFamilyInstance(face_ref, host_point1, inward1, symbol)
            doc.Regenerate()
            
            # --- STAGE 5: ORIENT EACH CLEAT (Canonical + User) ---
            n = global_normal
            
            # Use the same canonical transverse direction for both cleats so they sit on the same side of the bearer.
            transverse_target = n.CrossProduct(bearer_dir).Normalize()
            
            def align_and_rotate(inst, target_x, add_deg, pivot):
                current_x = inst.GetTransform().BasisX
                angle = current_x.AngleTo(target_x)
                if current_x.CrossProduct(target_x).DotProduct(n) < 0:
                    angle = -angle
                
                # Add user rotation
                add_rad = math.radians(add_deg)
                total_angle = angle + add_rad
                
                if abs(total_angle) > 1e-4:
                    axis = Line.CreateBound(pivot, pivot + n)
                    ElementTransformUtils.RotateElement(doc, inst.Id, axis, total_angle)
            
            align_and_rotate(instance0, transverse_target, rotation_deg, host_point0)
            align_and_rotate(instance1, transverse_target, rotation_deg, host_point1)
            
            # Attempt to mirror End 1 longitudinally if the family permits it, to satisfy the "mirrored" requirement
            # without breaking the transverse alignment (same side of bearer).
            try:
                if instance1.CanFlipHand:
                    instance1.flipHand()
            except Exception:
                pass
                
            doc.Regenerate()
            
            # --- STAGE 6: BACK EDGE OFFSET ---
            transverse_dir = n.CrossProduct(bearer_dir).Normalize()
            
            def calculate_back_edge_shift(inst, host_pt, req_offset_mm):
                opt_fine = Options()
                opt_fine.ComputeReferences = True
                opt_fine.DetailLevel = ViewDetailLevel.Fine
                cleat_geom = inst.get_Geometry(opt_fine)
                
                bounds = [1e9, -1e9]
                
                def extract_bounds(geom_elem, tf):
                    for obj in geom_elem:
                        if isinstance(obj, Solid) and obj.Faces.Size > 0:
                            for face in obj.Faces:
                                if isinstance(face, PlanarFace):
                                    pts = face.Triangulate().Vertices
                                    for pt in pts:
                                        if tf: pt = tf.OfPoint(pt)
                                        t = transverse_dir.DotProduct(pt - host_pt)
                                        if t < bounds[0]: bounds[0] = t
                                        if t > bounds[1]: bounds[1] = t
                        elif hasattr(obj, "GetSymbolGeometry"):
                            new_tf = obj.Transform
                            if tf: new_tf = tf.Multiply(new_tf)
                            extract_bounds(obj.GetSymbolGeometry(), new_tf)
                            
                extract_bounds(cleat_geom, None)
                
                min_t, max_t = bounds[0], bounds[1]
                
                if abs(min_t) < abs(max_t):
                    cleat_back_dir = transverse_dir.Negate()
                    cleat_edge_dist = min_t
                else:
                    cleat_back_dir = transverse_dir
                    cleat_edge_dist = max_t
                    
                cleat_back_edge_pt = host_pt + transverse_dir.Multiply(cleat_edge_dist)
                
                bearer_side_faces = []
                def extract_side_faces(geom_elem, tf):
                    for obj in geom_elem:
                        if isinstance(obj, Solid) and obj.Faces.Size > 0:
                            for face in obj.Faces:
                                if isinstance(face, PlanarFace):
                                    fn = face.FaceNormal
                                    if tf: fn = tf.OfVector(fn).Normalize()
                                    if abs(fn.DotProduct(global_normal)) < 0.3 and abs(fn.DotProduct(bearer_dir)) < 0.5:
                                        bearer_side_faces.append((face, fn, tf))
                        elif hasattr(obj, "GetSymbolGeometry"):
                            new_tf = obj.Transform
                            if tf: new_tf = tf.Multiply(new_tf)
                            extract_side_faces(obj.GetSymbolGeometry(), new_tf)
                            
                extract_side_faces(bearer_geom, None)
                
                best_face = None
                best_dot = -1.0
                best_face_pt = XYZ.Zero
                
                for face, fn, tf in bearer_side_faces:
                    dot = fn.DotProduct(cleat_back_dir)
                    if dot > best_dot:
                        best_dot = dot
                        origin = face.Origin
                        if tf: origin = tf.OfPoint(origin)
                        best_face = face
                        best_face_pt = origin
                        
                if best_face is None or best_dot < 0.5:
                    return 0.0, 0.0
                    
                current_gap = cleat_back_dir.DotProduct(cleat_back_edge_pt - best_face_pt)
                req_gap_ft = req_offset_mm * MM_TO_FT
                
                shift_dist = req_gap_ft - current_gap
                shift_vec = cleat_back_dir.Multiply(shift_dist)
                
                if shift_vec.GetLength() > 1e-5:
                    ElementTransformUtils.MoveElement(doc, inst.Id, shift_vec)
                return current_gap, req_gap_ft
            
            gap0_cur, gap0_req = calculate_back_edge_shift(instance0, host_point0, back_edge_offset_mm)
            gap1_cur, gap1_req = calculate_back_edge_shift(instance1, host_point1, back_edge_offset_mm)
            
            doc.Regenerate()
            t.Commit()
            
            # --- STAGE 7: UPDATE SESSION DATA ---
            self.bracing_session_created[bearer_id_int] = {
                "element_ids": [instance0.Id, instance1.Id],
                "connection_type_id": symbol_id.IntegerValue,
                "offset_mm": offset_mm,
                "rotation_deg": rotation_deg,
                "back_edge_offset_mm": back_edge_offset_mm
            }
            
            success_msg = (
                "SUCCESSFUL BRACING CLEAT PLACEMENT\n\n"
                "[BEARER]\n"
                "BearerId: {0}\n"
                "Host Face: bottom face detected\n\n"
                "[PLACEMENT]\n"
                "End Offset: {1} mm\n"
                "Rotation: {2}°\n"
                "Back Edge Offset: {3} mm\n\n"
                "End 0:\n"
                "  CleatId: {4}\n"
                "End 1:\n"
                "  CleatId: {5}\n\n"
                "[PHYSICAL CHECK]\n"
                "End 0 Back Edge Distance: {3} mm\n"
                "End 1 Back Edge Distance: {3} mm\n\n"
                "[SESSION]\n"
                "{6}\n\n"
                "[RESULT]\n"
                "2 cleats active for selected Bearer"
            ).format(
                bearer_id_int,
                round(offset_mm, 1),
                round(rotation_deg, 1),
                round(back_edge_offset_mm, 1),
                instance0.Id.IntegerValue,
                instance1.Id.IntegerValue,
                "Replaced previous pair for this Bearer" if session_data else "New placement"
            )
            
            if self.result_callback:
                self.result_callback(self.bridge_type + "-bracing", {
                    "status": "success",
                    "message": success_msg,
                    "footer": "Bracing cleat placement successful: 2 cleats active."
                })

        except Exception as ex:
            if t is not None:
                try:
                    if t.GetStatus() == TransactionStatus.Started:
                        t.RollBack()
                except Exception:
                    pass
            self._report_error(ex, "Bracing cleat placement failed")
