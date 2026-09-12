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
            
            # Initialize these diagnostic variables to prevent UnboundLocalError
            diag_beam_name = "N/A"
            diag_beam_storage = "N/A"
            diag_beam_mm = 0.0
            diag_p_center = XYZ.Zero
            diag_side_dir = XYZ.Zero
            diag_p_flange = XYZ.Zero
            diag_p_target_ref = insertion_point
            
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
                    
                    # --- NEW PARAMETER-DRIVEN LATERAL OFFSET ---
                    try:
                        # 1. Fetch the Beam Width parameter value
                        # First try the Beam's Type parameters
                        beam_type = doc.GetElement(ref_beam.GetTypeId())
                        param = beam_type.LookupParameter(self.beam_width_param_name) if beam_type else None
                        
                        if param is None:
                            # Try the beam instance itself (fallback)
                            param = ref_beam.LookupParameter(self.beam_width_param_name)
                        
                        if param is None or not param.HasValue:
                            raise Exception("Could not find parameter '{}' on the selected Beam.".format(self.beam_width_param_name))
                            
                        beam_width_internal = param.AsDouble()
                        
                        # 2. Convert user offset to internal units
                        user_offset_internal = self.user_flange_offset_mm / 304.8
                        
                        # 3. Determine Beam Centerline reference point
                        proj_result = beam_curve.Project(insertion_point)
                        if proj_result is None:
                            raise Exception("Could not project plate point onto Beam LocationCurve.")
                        p_center = proj_result.XYZPoint
                        
                        # 4. Determine which side of the beam contains the bearer
                        bearer_curve = ref_bearer.Location.Curve
                        ep0 = bearer_curve.GetEndPoint(0)
                        ep1 = bearer_curve.GetEndPoint(1)
                        
                        # Pick the endpoint that is furthest from the beam centerline
                        if ep0.DistanceTo(p_center) > ep1.DistanceTo(p_center):
                            bearer_side_point = ep0
                        else:
                            bearer_side_point = ep1
                            
                        raw_side_vector = bearer_side_point - p_center
                        
                        # Remove longitudinal component
                        lateral_vector = raw_side_vector - beam_dir * raw_side_vector.DotProduct(beam_dir)
                        if lateral_vector.IsAlmostEqualTo(XYZ.Zero):
                            raise Exception("Bearer aligns perfectly with Beam. Cannot determine lateral side.")
                            
                        direction_toward_bearer = lateral_vector.Normalize()
                        
                        # 5. Apply Mathematical Correction to insertion point
                        # This places the *FamilyOrigin* exactly at the target reference edge.
                        current_lateral = (insertion_point - p_center).DotProduct(direction_toward_bearer)
                        desired_lateral = (beam_width_internal / 2.0) + user_offset_internal
                        lateral_delta = desired_lateral - current_lateral
                        
                        final_insertion_point = insertion_point + direction_toward_bearer * lateral_delta
                        
                        # Store these for diagnostics later
                        diag_beam_name = param.Definition.Name
                        diag_beam_storage = str(param.StorageType)
                        diag_beam_internal = beam_width_internal
                        diag_beam_mm = beam_width_internal * 304.8
                        
                        diag_p_center = p_center
                        diag_side_dir = direction_toward_bearer
                        diag_p_flange = p_center + direction_toward_bearer * (beam_width_internal / 2.0)
                        diag_p_target_ref = final_insertion_point
                        
                        diag_calc_origin = (
                            "Width: {} mm, Offset: {} mm | "
                            "Lat Delta: {} mm"
                        ).format(
                            round(diag_beam_mm, 1),
                            round(self.user_flange_offset_mm, 1),
                            round(lateral_delta * 304.8, 1)
                        )
                    except Exception as lat_ex:
                        raise Exception("Parameter-driven lateral offset failed: " + str(lat_ex))

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
                # DO NOT mask the error. If calculating the offset or getting beam info fails, we MUST abort.
                raise Exception("Failed to calculate bracing placement geometry: " + str(ex))

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
            # BASIC ORIENTATION FIX & PHYSICAL OFFSET CORRECTION
            # ---------------------------------------------------------------
            diag_rotation = "None"
            diag_offset_calc = "None"
            final_physical_dist = "N/A"
            
            if instance:
                try:
                    from Autodesk.Revit.DB import ElementTransformUtils, Line, Options, Solid, GeometryInstance, PlanarFace
                    
                    transform = instance.GetTransform()
                    current_x = transform.BasisX
                    
                    # We want the plate's local X to align with bearer_dir (forward_dir)
                    angle_x = current_x.AngleOnPlaneTo(forward_dir, global_normal)
                    if abs(angle_x) > 1e-4:
                        axis = Line.CreateBound(final_insertion_point, final_insertion_point + global_normal)
                        ElementTransformUtils.RotateElement(doc, instance.Id, axis, angle_x)
                        diag_rotation = "Rotated {} rads to align X with Bearer".format(round(angle_x, 4))
                    
                    doc.Regenerate()
                    
                    # Geometry Inspector Helper
                    def get_local_face_distance(element, origin_pt, search_dir, expected_normal=None, max_distance=3.0):
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
                                                continue
                                                
                                            f_origin = face.Origin
                                            if tf: f_origin = tf.OfPoint(f_origin)
                                            
                                            vec = f_origin - origin_pt
                                            dist_to_plane = vec.DotProduct(normal)
                                            abs_dist = abs(dist_to_plane)
                                            
                                            if abs_dist < max_distance and abs_dist < best_dist[0]:
                                                proj = face.Project(origin_pt)
                                                if proj and proj.Distance < max_distance:
                                                    best_dist[0] = abs_dist
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
                    
                    # 1. Measure physical offset: Family Origin to Plate Edge pointing towards Beam
                    # search_dir = -diag_side_dir because we want the face looking towards the beam
                    p_family_origin = instance.GetTransform().Origin
                    
                    # We look for the face in the negative side_dir direction (pointing to beam).
                    # Actually, `search_dir` is the direction we search from the origin. We want to search towards the beam.
                    # Wait, if we search in -side_dir, `get_local_face_distance` looks for a face whose normal is opposite (-(-side_dir)) = +side_dir.
                    # Yes, the face of the plate pointing at the beam has normal = -side_dir (if it's to the right of the beam).
                    # But wait, if we are on the right side of the beam, `side_dir` is positive X. The left face of the plate has normal = -X (-side_dir).
                    # So `get_local_face_distance` with search_dir = -side_dir expects normal = +side_dir.
                    # This is correct. The plate's left face normal points AWAY from the plate, so it points towards the beam (-side_dir).
                    # Wait, if normal points towards beam (-side_dir), then normal . (-side_dir) = 1.0. 
                    # `get_local_face_distance` says: `if normal.DotProduct(search_dir) < -0.5:`
                    # So if search_dir = side_dir, it looks for normal ~ -side_dir. Yes!
                    
                    plate_origin_to_reference = get_local_face_distance(
                        instance, 
                        p_family_origin, 
                        diag_side_dir, 
                        expected_normal=diag_side_dir.Negate()
                    )
                    
                    if plate_origin_to_reference is not None:
                        # 2. Shift the family to align physical reference with target reference
                        # P_target_reference = P_origin_new + side_dir * plate_origin_to_reference
                        # P_origin_new = P_target_reference - side_dir * plate_origin_to_reference
                        # Currently, P_origin_old = P_target_reference
                        # Move vec = P_origin_new - P_origin_old = - side_dir * plate_origin_to_reference
                        shift_mag = plate_origin_to_reference
                        shift_vec = diag_side_dir.Negate() * shift_mag
                        
                        ElementTransformUtils.MoveElement(doc, instance.Id, shift_vec)
                        doc.Regenerate()
                        
                        p_origin_final = instance.GetTransform().Origin
                        p_phys_ref_final = p_origin_final + diag_side_dir * plate_origin_to_reference
                        
                        dist_flange_to_phys = p_phys_ref_final.DistanceTo(diag_p_flange)
                        
                        diag_offset_calc = (
                            "Origin-to-Edge: {} mm | "
                            "Shifted origin by: {} mm"
                        ).format(round(plate_origin_to_reference * 304.8, 1), round(shift_mag * -304.8, 1))
                        
                        final_physical_dist = "{} mm".format(round(dist_flange_to_phys * 304.8, 1))
                    else:
                        diag_offset_calc = "Failed to find physical face for offset correction."
                        p_origin_final = p_family_origin
                        
                except Exception as geom_ex:
                    diag_rotation = "Geometry Error: " + str(geom_ex)
                    p_origin_final = diag_p_target_ref

            t.Commit()
            
            success_msg = (
                "SUCCESSFUL PLACEMENT\n\n"
                "[BEAM DATA]\n"
                "BeamId: {0} | BearerId: {1}\n"
                "Width Param: '{2}' ({3})\n"
                "Beam Width: {4} mm | Half Width: {5} mm\n"
                "User Offset: {6} mm\n\n"
                "[VECTORS]\n"
                "Centerline XYZ: {7}\n"
                "Side Dir XYZ: {8}\n"
                "Flange Edge XYZ: {9}\n"
                "Target Ref XYZ: {10}\n\n"
                "[ORIGIN COMPENSATION]\n"
                "Exact Overload: {11}\n"
                "Rotation: {12}\n"
                "Origin offset calculation: {13}\n"
                "Final Family Origin XYZ: {14}\n\n"
                "FINAL PHYSICAL FLANGE-TO-PLATE-REF DISTANCE: {15}"
            ).format(
                self.ref_beam_id.IntegerValue, self.ref_bearer_id.IntegerValue,
                diag_beam_name, diag_beam_storage,
                round(diag_beam_mm, 1), round(diag_beam_mm / 2.0, 1),
                round(self.user_flange_offset_mm, 1),
                "({}, {}, {})".format(round(diag_p_center.X, 2), round(diag_p_center.Y, 2), round(diag_p_center.Z, 2)),
                "({}, {}, {})".format(round(diag_side_dir.X, 2), round(diag_side_dir.Y, 2), round(diag_side_dir.Z, 2)),
                "({}, {}, {})".format(round(diag_p_flange.X, 2), round(diag_p_flange.Y, 2), round(diag_p_flange.Z, 2)),
                "({}, {}, {})".format(round(diag_p_target_ref.X, 2), round(diag_p_target_ref.Y, 2), round(diag_p_target_ref.Z, 2)),
                diag_exact_overload,
                diag_rotation,
                diag_offset_calc,
                "({}, {}, {})".format(round(p_origin_final.X, 2), round(p_origin_final.Y, 2), round(p_origin_final.Z, 2)),
                final_physical_dist
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

