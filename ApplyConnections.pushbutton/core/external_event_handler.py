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
            diag_symbol_class = symbol.GetType().Name
            diag_is_family_symbol = hasattr(symbol, "Family")
            
            # Inspect manually placed instance for ground truth safely
            manual_instances = FilteredElementCollector(doc).OfClass(FamilyInstance).ToElements()
            manual_inst = None
            
            for inst in manual_instances:
                inst_name = ""
                sym_name = ""
                fam_name = ""
                
                try:
                    from Autodesk.Revit.DB import Element
                    inst_name = Element.Name.GetValue(inst)
                except Exception:
                    try:
                        inst_name = inst.Name
                    except Exception:
                        pass
                        
                try:
                    if hasattr(inst, "Symbol") and inst.Symbol:
                        try:
                            sym_name = Element.Name.GetValue(inst.Symbol)
                        except Exception:
                            sym_name = inst.Symbol.Name
                            
                        if hasattr(inst.Symbol, "Family") and inst.Symbol.Family:
                            try:
                                fam_name = Element.Name.GetValue(inst.Symbol.Family)
                            except Exception:
                                fam_name = inst.Symbol.Family.Name
                except Exception:
                    pass
                    
                target = "Single Sided Bracing Connection Plate"
                if (inst_name and target in inst_name) or \
                   (sym_name and target in sym_name) or \
                   (fam_name and target in fam_name):
                    manual_inst = inst
                    break
                    
            if manual_inst:
                actual_symbol_id = manual_inst.GetTypeId()
                actual_symbol = doc.GetElement(actual_symbol_id)
                diag_manual_type_id = actual_symbol_id.IntegerValue
                
                # CASE A FIX: Use the actual symbol if the dropdown ID was incorrect
                if actual_symbol_id != symbol.Id:
                    symbol = actual_symbol
                    diag_symbol_class = symbol.GetType().Name
                    diag_is_family_symbol = hasattr(symbol, "Family")
            else:
                diag_manual_type_id = "Not Found"
                
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
                    insertion_point,
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
                        insertion_point, 
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
                        
                        plane = Plane.CreateByNormalAndOrigin(global_normal, insertion_point)
                        sk_plane = SketchPlane.Create(doc, plane)
                        diag_exact_overload = "NewFamilyInstance(XYZ, FamilySymbol, Element(MathPlane), StructuralType)"
                        instance = doc.Create.NewFamilyInstance(
                            insertion_point, 
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
                            "Face Reference ElementId: {1}\n"
                            "Expected Bearer ElementId: {2}\n"
                            "Resolved GeometryObject Class: {3}\n"
                            "Actual Face Normal: {4}\n"
                            "Original Click Point: {5}\n"
                            "Projected Face Point: {6}\n"
                            "Point-to-Face Distance: {7}\n"
                            "Ref Dir: {8}\n"
                            "abs(ref_dir dot actual face normal): {9}\n"
                            "FamilyPlacementType: {10}\n"
                            "Exceptions: {11}\n\n"
                            "MANUAL INSTANCE INSPECTION:\n{12}"
                        ).format(
                            self.stable_face_ref,
                            diag_face_owner_id,
                            diag_expected_bearer_id,
                            diag_face_class,
                            diag_face_normal,
                            str(original_loc_point),
                            str(insertion_point),
                            diag_pt_to_face_dist,
                            diag_ref_dir_original,
                            diag_dot_ref_normal,
                            diag_placement_type,
                            diag_exception,
                            diag_manual_info
                        )
                        raise Exception(err_msg)
                        
            # If a SketchPlane method succeeded, rotate to align with ref_dir
            if instance and "SketchPlane" in diag_exact_overload:
                try:
                    from Autodesk.Revit.DB import ElementTransformUtils, Line
                    transform = instance.GetTransform()
                    current_dir = transform.BasisX
                    angle = current_dir.AngleOnPlaneTo(ref_dir, global_normal)
                    if abs(angle) > 1e-5:
                        axis = Line.CreateBound(insertion_point, insertion_point + global_normal)
                        ElementTransformUtils.RotateElement(doc, instance.Id, axis, angle)
                except Exception:
                    pass

            
            t.Commit()
            
            success_msg = (
                "SUCCESSFUL PLACEMENT\n"
                "Symbol Class: {0}\n"
                "Manual Instance TypeId: {1}\n"
                "FamilyPlacementType: {2}\n"
                "Face Class: {3}\n"
                "Face Owner matched Bearer: {4}\n"
                "Point-to-Face Distance: {5}\n"
                "Face Normal: {6}\n"
                "Ref_Dir: {7}\n"
                "abs(Ref_Dir dot Normal): {8}\n"
                "Exact Overload Used: {9}\n"
                "Insertion Point: {10}"
            ).format(
                diag_symbol_class, diag_manual_type_id, diag_placement_type,
                diag_face_class, diag_face_owner_id == diag_expected_bearer_id,
                diag_pt_to_face_dist, diag_face_normal, diag_ref_dir_original,
                diag_dot_ref_normal, diag_exact_overload, str(insertion_point)
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

