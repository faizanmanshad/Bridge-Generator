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
        """Execute single Bracing Connection placement within a Revit transaction for debugging."""
        ref_bearer = doc.GetElement(self.ref_bearer_id) if self.ref_bearer_id else None

        if not ref_bearer:
            if self.result_callback:
                self.result_callback(
                    self.bridge_type,
                    {"status": "error", "message": "Reference Bearer element not found."},
                )
            return

        if not self.connection_type_id:
            if self.result_callback:
                self.result_callback(
                    self.bridge_type,
                    {"status": "warning", "message": "No custom bracing connection plate family selected."},
                )
            return

        if not self.stable_face_ref or not self.face_point:
            if self.result_callback:
                self.result_callback(
                    self.bridge_type,
                    {"status": "error", "message": "No stable face reference or point provided."},
                )
            return

        t = None
        try:
            # Resolve face reference
            face_ref = Reference.ParseFromStableRepresentation(doc, self.stable_face_ref)
            
            # Resolve family symbol
            symbol = doc.GetElement(self.connection_type_id)
            if not symbol:
                raise Exception("Could not resolve FamilySymbol from the selected type.")

            t = Transaction(doc, "Urbana — Apply Single Bracing Connection Plate")
            t.Start()

            if not symbol.IsActive:
                symbol.Activate()
                doc.Regenerate()

            # =====================================================================
            # STRICT DIAGNOSTIC PLACEMENT
            # =====================================================================
            import traceback
            from Autodesk.Revit.DB import FilteredElementCollector, FamilyInstance
            
            # 1. VERIFY symbol
            symbol_id = self.connection_type_id
            symbol = doc.GetElement(symbol_id)
            if symbol is None:
                raise Exception("Failed to resolve FamilySymbol for ElementId: {}".format(symbol_id))
                
            diag_symbol_class = symbol.GetType().Name
            diag_is_family_symbol = hasattr(symbol, "Family")
            
            if not diag_is_family_symbol:
                raise Exception("Selected item (Id: {}) is of class {}, not a FamilySymbol.".format(symbol_id, diag_symbol_class))
            
            diag_manual_type_id = symbol_id.IntegerValue
                
            diag_placement_type = str(symbol.Family.FamilyPlacementType) if diag_is_family_symbol else "N/A"
            diag_is_active = symbol.IsActive if diag_is_family_symbol else False
            
            if diag_is_family_symbol and not symbol.IsActive:
                symbol.Activate()
                doc.Regenerate()

            # ---------------------------------------------------------------
            # DIRECTLY RESOLVE THE SELECTED FACE
            # ---------------------------------------------------------------
            face_ref = Reference.ParseFromStableRepresentation(doc, self.stable_face_ref)
            
            diag_expected_bearer_id = self.ref_bearer_id.IntegerValue
            diag_face_owner_id = face_ref.ElementId.IntegerValue
            if diag_face_owner_id != diag_expected_bearer_id:
                raise Exception("Mismatch: face owner {} != expected bearer {}".format(diag_face_owner_id, diag_expected_bearer_id))
            
            face_obj = ref_bearer.GetGeometryObjectFromReference(face_ref)
            
            if face_obj is None:
                diag_face_class = "None"
                diag_is_planar = False
                insertion_point = self.face_point
                global_normal = XYZ.BasisZ.Negate()
                diag_face_origin = "N/A"
                diag_face_normal = "N/A (Assumed Down)"
                diag_pt_to_face_dist = "Face Object Not Found (No projection)"
            else:
                diag_face_class = face_obj.GetType().Name
                diag_is_planar = hasattr(face_obj, "FaceNormal")
                
                # ---------------------------------------------------------------
                # VALIDATE / CORRECT THE INSERTION POINT
                # ---------------------------------------------------------------
                original_loc_point = self.face_point
                transform = ref_bearer.GetTransform()
                
                # Convert global clicked point to local to project it against the local GeometryObject
                local_point = transform.Inverse.OfPoint(original_loc_point)
                
                if diag_is_planar:
                    local_normal = face_obj.FaceNormal.Normalize()
                    diag_face_origin = str(transform.OfPoint(face_obj.Origin))
                else:
                    local_normal = face_obj.ComputeNormal(UV(0.5, 0.5)).Normalize()
                    diag_face_origin = "N/A"
                    
                global_normal = transform.OfVector(local_normal).Normalize()
                diag_face_normal = str(global_normal)
                
                projection = face_obj.Project(local_point)
                if projection:
                    # Convert the local intersection point back to global
                    insertion_point = transform.OfPoint(projection.XYZPoint)
                    diag_pt_to_face_dist = original_loc_point.DistanceTo(insertion_point)
                else:
                    insertion_point = original_loc_point
                    diag_pt_to_face_dist = "Project Failed"
                
            # ---------------------------------------------------------------
            # REFERENCE DIRECTION
            # ---------------------------------------------------------------
            try:
                bearer_dir = ref_bearer.Location.Curve.Direction.Normalize()
            except Exception:
                bearer_dir = XYZ.BasisX
                
            dot_bearer_normal = bearer_dir.DotProduct(global_normal)
            projected_dir = bearer_dir - global_normal * dot_bearer_normal
            
            if projected_dir.GetLength() > 1e-6:
                ref_dir = projected_dir.Normalize()
            else:
                if diag_is_planar:
                    ref_dir = transform.OfVector(face_obj.XVector).Normalize()
                else:
                    if abs(global_normal.DotProduct(XYZ.BasisX)) < 0.99:
                        ref_dir = global_normal.CrossProduct(XYZ.BasisX).Normalize()
                    else:
                        ref_dir = global_normal.CrossProduct(XYZ.BasisY).Normalize()
                        
            diag_ref_dir_original = str(ref_dir)
            diag_ref_dir_length = ref_dir.GetLength()
            diag_dot_ref_normal = abs(ref_dir.DotProduct(global_normal))
            
            # ---------------------------------------------------------------
            # GEOMETRY CALCULATION: BEAM & BEARER
            # ---------------------------------------------------------------
            diag_beam_info = "None"
            diag_calc_origin = "None"
            diag_offset_30 = "None"
            diag_offset_5 = "None"
            final_insertion_point = insertion_point
            
            try:
                # MM to Feet conversion factor
                MM_TO_FT = 1.0 / 304.8
                
                # Fetch Beam
                ref_beam = doc.GetElement(self.ref_beam_id)
                if ref_beam:
                    beam_curve = ref_beam.Location.Curve
                    beam_dir = beam_curve.Direction.Normalize()
                    
                    # Project both to the face plane
                    # Face normal is global_normal
                    dot_beam = beam_dir.DotProduct(global_normal)
                    proj_beam_dir = (beam_dir - global_normal * dot_beam).Normalize()
                    
                    # Determine forward and side directions
                    forward_dir = ref_dir # Bearer direction
                    side_dir = forward_dir.CrossProduct(global_normal).Normalize()
                    
                    # --- COMPUTE MATHEMATICAL INTERSECTION ---
                    try:
                        p1 = ref_bearer.Location.Curve.GetEndPoint(0)
                        p2 = ref_bearer.Location.Curve.GetEndPoint(1)
                        p3 = beam_curve.GetEndPoint(0)
                        p4 = beam_curve.GetEndPoint(1)
                        
                        x1, y1 = p1.X, p1.Y
                        x2, y2 = p2.X, p2.Y
                        x3, y3 = p3.X, p3.Y
                        x4, y4 = p4.X, p4.Y
                        
                        denom = (x1 - x2) * (y3 - y4) - (y1 - y2) * (x3 - x4)
                        if abs(denom) > 1e-5:
                            ix = ((x1*y2 - y1*x2)*(x3 - x4) - (x1 - x2)*(x3*y4 - y3*x4)) / denom
                            iy = ((x1*y2 - y1*x2)*(y3 - y4) - (y1 - y2)*(x3*y4 - y3*x4)) / denom
                            
                            # Validating finite intersection (check if intersection is within bounding segments, allow 5% tolerance)
                            def is_within(v, bounds, tol=0.1):
                                min_b = min(bounds) - tol
                                max_b = max(bounds) + tol
                                return min_b <= v <= max_b
                                
                            if not (is_within(ix, (x1, x2)) and is_within(iy, (y1, y2)) and 
                                    is_within(ix, (x3, x4)) and is_within(iy, (y3, y4))):
                                raise Exception("Calculated intersection ({},{}) lies far outside the finite member segments.".format(round(ix,2), round(iy,2)))

                            final_insertion_point = XYZ(ix, iy, insertion_point.Z)
                            diag_calc_origin = "Finite Intersection: ({}, {}, {})".format(round(ix,2), round(iy,2), round(insertion_point.Z,2))
                        else:
                            raise Exception("Bearer and Beam lines are parallel.")
                    except Exception as int_ex:
                        raise Exception("Finite joint intersection failed: " + str(int_ex))
                    
                    # Dump the family bounding box to understand where its origin is
                    bbox = symbol.get_BoundingBox(None)
                    if bbox:
                        min_pt = bbox.Min
                        max_pt = bbox.Max
                        diag_beam_info = "Family BBox: Min({},{},{}) Max({},{},{})".format(
                            round(min_pt.X / MM_TO_FT, 1), round(min_pt.Y / MM_TO_FT, 1), round(min_pt.Z / MM_TO_FT, 1),
                            round(max_pt.X / MM_TO_FT, 1), round(max_pt.Y / MM_TO_FT, 1), round(max_pt.Z / MM_TO_FT, 1)
                        )
                    else:
                        diag_beam_info = "Family BBox: None"
            except Exception as ex:
                diag_beam_info = "Error: " + str(ex)

            # ---------------------------------------------------------------
            # PLACEMENT ATTEMPTS
            # ---------------------------------------------------------------
            diag_exact_overload = "None"
            instance = None
            exceptions = []
            
            try:
                # ATTEMPT 1: Face-based overload
                diag_exact_overload = "NewFamilyInstance(Reference, XYZ, XYZ, FamilySymbol)"
                instance = doc.Create.NewFamilyInstance(
                    face_ref,
                    final_insertion_point,
                    ref_dir,
                    symbol
                )
            except Exception as e1:
                exceptions.append("FaceBased Failed: " + str(e1))
                
                # ATTEMPT 2: SketchPlane from Face Reference
                try:
                    from Autodesk.Revit.DB import SketchPlane
                    from Autodesk.Revit.DB.Structure import StructuralType
                    sk_plane = SketchPlane.Create(doc, face_ref)
                    diag_exact_overload = "NewFamilyInstance(XYZ, FamilySymbol, Element(SketchPlane), StructuralType)"
                    instance = doc.Create.NewFamilyInstance(
                        final_insertion_point, 
                        symbol, 
                        sk_plane, 
                        StructuralType.NonStructural
                    )
                except Exception as e2:
                    exceptions.append("SketchPlane(FaceRef) Failed: " + str(e2))
                    
                    # ATTEMPT 3: SketchPlane from Mathematical Plane
                    try:
                        from Autodesk.Revit.DB import Plane, SketchPlane
                        from Autodesk.Revit.DB.Structure import StructuralType
                        
                        plane = Plane.CreateByNormalAndOrigin(global_normal, final_insertion_point)
                        sk_plane = SketchPlane.Create(doc, plane)
                        diag_exact_overload = "NewFamilyInstance(XYZ, FamilySymbol, Element(MathPlane), StructuralType)"
                        instance = doc.Create.NewFamilyInstance(
                            final_insertion_point, 
                            symbol, 
                            sk_plane, 
                            StructuralType.NonStructural
                        )
                    except Exception as e3:
                        exceptions.append("SketchPlane(MathPlane) Failed: " + str(e3))
                        
                        # Generate diagnostic payload if ALL fail
                        diag_exception = " | ".join(exceptions)
                        diag_tb = traceback.format_exc()
                        
                        diag_manual_info = "Not Found"
                        if manual_inst:
                            try:
                                m_eid = manual_inst.Id.IntegerValue
                                m_type = manual_inst.GetTypeId().IntegerValue
                                m_host = manual_inst.Host.Id.IntegerValue if manual_inst.Host else "None"
                                m_host_face = "Yes" if (hasattr(manual_inst, "HostFace") and manual_inst.HostFace) else "No"
                                m_wp = "N/A"
                                try:
                                    wp_param = manual_inst.get_Parameter(Autodesk.Revit.DB.BuiltInParameter.SKETCH_PLANE_PARAM)
                                    if wp_param and wp_param.HasValue:
                                        m_wp = wp_param.AsValueString()
                                except: pass
                                m_origin = str(manual_inst.GetTransform().Origin)
                                diag_manual_info = (
                                    "ElementId: {0}\n"
                                    "Host: {1}\n"
                                    "HostFace Exposed: {2}\n"
                                    "WorkPlane Param: {3}\n"
                                ).format(m_eid, m_host, m_host_face, m_wp)
                            except Exception as me:
                                diag_manual_info = "Error retrieving manual instance data: " + str(me)
                        
                        err_msg = (
                            "PLACEMENT FAILED ON ALL ATTEMPTS\n"
                            "Stable Face Reference: {0}\n"
                            "Exceptions: {1}\n\n"
                            "MANUAL INSTANCE INSPECTION:\n{2}"
                        ).format(
                            self.stable_face_ref,
                            diag_exception,
                            diag_manual_info
                        )
                        raise Exception(err_msg)
                        
            # ---------------------------------------------------------------
            # GEOMETRIC ALIGNMENT & TRANSLATION (30mm / 5mm RULES)
            # ---------------------------------------------------------------
            diag_rotation = "None"
            diag_translation = "None"
            
            if instance:
                try:
                    from Autodesk.Revit.DB import ElementTransformUtils, Line, Options, Solid, GeometryInstance
                    
                    # --- 1. ORIENTATION FIX ---
                    transform = instance.GetTransform()
                    current_x = transform.BasisX
                    current_y = transform.BasisY
                    
                    # We want the plate's local X to align with bearer_dir
                    # and local Y to point roughly towards the beam (side_dir)
                    angle_x = current_x.AngleOnPlaneTo(forward_dir, global_normal)
                    if abs(angle_x) > 1e-4:
                        axis = Line.CreateBound(final_insertion_point, final_insertion_point + global_normal)
                        ElementTransformUtils.RotateElement(doc, instance.Id, axis, angle_x)
                        diag_rotation = "Rotated {} rads to align X with Bearer".format(round(angle_x, 4))
                    
                    # After rotation, check if Y points AWAY from beam, if so flip 180
                    transform = instance.GetTransform()
                    if transform.BasisY.DotProduct(side_dir) < 0:
                        axis = Line.CreateBound(final_insertion_point, final_insertion_point + global_normal)
                        ElementTransformUtils.RotateElement(doc, instance.Id, axis, 3.14159265359)
                        diag_rotation += " | Flipped 180 to face beam"
                        
                    doc.Regenerate()
                    
                    # --- 2. LOCAL EDGE MEASUREMENT & TRANSLATION ---
                    def get_local_face_distance(element, origin_pt, search_dir, expected_normal=None, max_distance=3.0):
                        """Find the planar face of an element closest to origin_pt looking in search_dir.
                           If expected_normal is provided, only faces roughly aligning with that normal are considered.
                           Returns the signed distance to project origin_pt onto that face.
                        """
                        from Autodesk.Revit.DB import PlanarFace
                        opt = Options()
                        opt.ComputeReferences = True
                        geom = element.get_Geometry(opt)
                        best_dist = [1e9]
                        best_signed_dist = [None]
                        
                        def process_solid(solid, tf):
                            if solid and solid.Faces.Size > 0:
                                for face in solid.Faces:
                                    if isinstance(face, PlanarFace):
                                        normal = face.FaceNormal
                                        if tf: normal = tf.OfVector(normal).Normalize()
                                        
                                        # Only consider faces facing opposite to our search_dir
                                        if normal.DotProduct(search_dir) < -0.5:
                                            if expected_normal and abs(abs(normal.DotProduct(expected_normal)) - 1.0) > 0.1:
                                                continue # Normal is not parallel to expected_normal
                                                
                                            # Project origin_pt to the plane of this face
                                            f_origin = face.Origin
                                            if tf: f_origin = tf.OfPoint(f_origin)
                                            
                                            vec = f_origin - origin_pt
                                            dist_to_plane = vec.DotProduct(normal)
                                            
                                            # If this face is close to the joint
                                            abs_dist = abs(dist_to_plane)
                                            if abs_dist < max_distance and abs_dist < best_dist[0]:
                                                # Ensure the projection point is actually within the bounding box of the face
                                                # to filter out distant planar extensions of other faces.
                                                proj = face.Project(origin_pt)
                                                if proj and proj.Distance < max_distance:
                                                    best_dist[0] = abs_dist
                                                    # distance along search_dir:
                                                    # search_dir points towards the face, normal points away.
                                                    best_signed_dist[0] = vec.DotProduct(search_dir)
                                                    
                        def traverse(geom_elem, tf):
                            for obj in geom_elem:
                                if isinstance(obj, Solid): process_solid(obj, tf)
                                elif isinstance(obj, GeometryInstance):
                                    new_tf = obj.Transform
                                    if tf: new_tf = tf.Multiply(new_tf)
                                    traverse(obj.GetInstanceGeometry(), new_tf)
                        if geom: traverse(geom, None)
                        return best_signed_dist[0]

                    # Define the axes
                    # 30mm rule is relative to the beam flange along `side_dir`
                    # 5mm rule is relative to the bearer edge along `forward_dir`
                    shift_vec = XYZ.Zero
                    
                    # --- 30 MM RULE (BEAM) ---
                    # Find distance from insertion point to local beam face along side_dir
                    dist_to_beam = get_local_face_distance(ref_beam, final_insertion_point, side_dir, expected_normal=side_dir)
                    # Find distance from insertion point to plate's front face along side_dir
                    dist_to_plate_front = get_local_face_distance(instance, final_insertion_point, side_dir)
                    
                    if dist_to_beam is not None and dist_to_plate_front is not None:
                        current_gap = dist_to_beam - dist_to_plate_front
                        target_gap = 30.0 / 304.8
                        shift_mag = current_gap - target_gap
                        if abs(shift_mag) < 3.0: # Sanity check: do not translate more than 3 feet (900mm)
                            shift_vec += side_dir * shift_mag
                            diag_offset_30 = "Local Gap {} mm -> Target 30 mm | Shifted {} mm".format(round(current_gap*304.8, 1), round(shift_mag * 304.8, 1))
                        else:
                            diag_offset_30 = "Sanity Check Failed: Shift magnitude {} mm is too large".format(round(shift_mag*304.8, 1))
                    else:
                        diag_offset_30 = "Failed to find local faces for 30mm rule"
                        
                    # --- 5 MM RULE (BEARER REAR) ---
                    # "Rear side" means opposite to forward_dir (away from the joint).
                    # We look for the bearer's face along forward_dir?
                    # The plate bolts under the bearer. It is set back 5mm from the bearer's edge.
                    # Wait, if the plate is at the joint, the bearer ends at the beam. 
                    # The bearer end face is along forward_dir (if forward_dir points towards the beam intersection).
                    dist_to_bearer_end = get_local_face_distance(ref_bearer, final_insertion_point, forward_dir, expected_normal=forward_dir)
                    dist_to_plate_end = get_local_face_distance(instance, final_insertion_point, forward_dir)
                    
                    if dist_to_bearer_end is not None and dist_to_plate_end is not None:
                        current_gap = dist_to_bearer_end - dist_to_plate_end
                        target_gap = 5.0 / 304.8
                        shift_mag = current_gap - target_gap
                        if abs(shift_mag) < 3.0:
                            shift_vec += forward_dir * shift_mag
                            diag_offset_5 = "Local Setback {} mm -> Target 5 mm | Shifted {} mm".format(round(current_gap*304.8, 1), round(shift_mag * 304.8, 1))
                        else:
                            diag_offset_5 = "Sanity Check Failed: Shift magnitude {} mm is too large".format(round(shift_mag*304.8, 1))
                    else:
                        diag_offset_5 = "Failed to find local faces for 5mm rule"
                        
                    if not shift_vec.IsAlmostEqualTo(XYZ.Zero):
                        ElementTransformUtils.MoveElement(doc, instance.Id, shift_vec)
                        diag_translation = "Vector: " + str(shift_vec)
                        
                except Exception as geom_ex:
                    diag_translation = "Geometry Error: " + str(geom_ex)

            t.Commit()
            
            success_msg = (
                "SUCCESSFUL PLACEMENT\n"
                "Exact Overload Used: {0}\n"
                "Rotation Applied: {1}\n"
                "Translation Applied: {2}\n"
                "30mm rule info: {3}\n"
                "5mm rule info: {4}"
            ).format(
                diag_exact_overload, diag_rotation, diag_translation, diag_offset_30, diag_offset_5
            )
            
            if self.result_callback:
                self.result_callback(self.bridge_type + "-bracing", {
                    "status": "success",
                    "message": success_msg,
                    "footer": "Diagnostic checks passed."
                })

        except Exception as ex:
            if t is not None:
                try:
                    if t.GetStatus() == TransactionStatus.Started:
                        t.RollBack()
                except Exception:
                    pass
            self._report_error(ex, "Single bracing connection placement failed")

