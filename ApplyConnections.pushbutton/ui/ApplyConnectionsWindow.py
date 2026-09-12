# -*- coding: utf-8 -*-
# pyright: reportMissingImports=false
"""
ApplyConnectionsWindow.py — Code-behind for ApplyConnectionsWindow.xaml.

ApplyConnections.pushbutton / ui/
Urbana Bridge Generator — Revit 2022 / 2024.3  |  pyRevit 6.4.0  |  IronPython 2.7

Window architecture:
  - XamlReader.Load() pattern (no x:Class code-behind — same as BridgeSetupWindow)
  - FindName() for all control binding
  - window.Hide() / window.Show() for Revit selection without closing the WPF window
  - bridge_type ("concrete" | "timber") is derived from which tab is active
    and passed through to all detection/placement logic

Tab structure:
  BridgeTypeTabs (top-level)
    Concrete Deck
      ConcreteConnectionTabs (secondary)
        Beam-Beam   ← IMPLEMENTED
        Bearer-Beam ← PLACEHOLDER
        Post-Beam   ← PLACEHOLDER
        Bracing Connection  ← PLACEHOLDER
        Bearing + Chemset   ← PLACEHOLDER
        Bracing Rods        ← PLACEHOLDER
    Timber Deck
      TimberConnectionTabs (secondary)
        Beam-Beam   ← IMPLEMENTED
        Bearer-Beam ← PLACEHOLDER
        Post-Beam   ← PLACEHOLDER
        Joist-Bearer ← PLACEHOLDER
        Bracing Connection  ← PLACEHOLDER
        Bearing + Chemset   ← PLACEHOLDER
        Bracing Rods        ← PLACEHOLDER

Python compatibility: IronPython 2.7 — no f-strings, no dataclasses.
"""

import os
import clr  # type: ignore
clr.AddReference("PresentationFramework")
clr.AddReference("PresentationCore")
clr.AddReference("WindowsBase")
clr.AddReference("RevitAPI")
clr.AddReference("RevitAPIUI")

from System.Windows.Markup import XamlReader   # type: ignore
from System.IO             import StreamReader  # type: ignore
from System.Windows.Media  import SolidColorBrush, Color  # type: ignore

from Autodesk.Revit.DB import Transaction, TransactionStatus  # type: ignore

from core.logging_utils        import get_logger
from core.revit_compat         import element_id_value
from core.connection_catalog   import get_connection_types, get_bracing_plate_symbols, is_available as catalog_available
from core.validation           import (
    validate_connection_selection,
    validate_main_beam,
    validate_joints,
)
from core.selection            import pick_structural_framing, describe_element, pick_face
from core.external_event_handler import (
    REQUEST_APPLY_BEAM_BEAM,
    REQUEST_APPLY_BEARER_BEAM,
    REQUEST_APPLY_BRACING_CONNECTION,
)


# ---------------------------------------------------------------------------
# Brush helpers
# ---------------------------------------------------------------------------

def _brush(hex_color):
    """Return a WPF SolidColorBrush from a #RRGGBB hex string."""
    c = Color.FromRgb(
        int(hex_color[1:3], 16),
        int(hex_color[3:5], 16),
        int(hex_color[5:7], 16),
    )
    return SolidColorBrush(c)


_BRUSH_SUCCESS  = _brush("#2E7D32")
_BRUSH_INFO     = _brush("#1B3A6B")
_BRUSH_WARNING  = _brush("#E65100")
_BRUSH_ERROR    = _brush("#C62828")
_BRUSH_MUTED    = _brush("#8A97AE")

_BG_SUCCESS = _brush("#E8F5E9")
_BG_WARNING = _brush("#FFF3E0")
_BG_ERROR   = _brush("#FFEBEE")
_BG_INFO    = _brush("#E3F2FD")
_BG_NEUTRAL = _brush("#FFFFFF")


# ---------------------------------------------------------------------------
# Bridge type constants
# ---------------------------------------------------------------------------

BRIDGE_CONCRETE = "concrete"
BRIDGE_TIMBER   = "timber"


# ---------------------------------------------------------------------------
# Window class
# ---------------------------------------------------------------------------

class ApplyConnectionsWindow(object):
    """Wrapper around the WPF ApplyConnectionsWindow XAML.

    Usage:
        win = ApplyConnectionsWindow(uidoc, bundle_dir)
        win.show_dialog()
    """

    def __init__(self, uidoc, bundle_dir, handler=None, ext_event=None):
        self._uidoc      = uidoc
        self._doc        = uidoc.Document
        self._bundle_dir = bundle_dir
        self._logger     = get_logger()
        self._window     = None
        self._handler    = handler
        self._ext_event  = ext_event
        
        if self._handler:
            self._handler.result_callback = self._on_event_completed

        # --- Per-bridge-type state ---
        # Selected main beam ElementId for Beam-Beam
        self._selected_beam_id = {
            BRIDGE_CONCRETE: None,
            BRIDGE_TIMBER:   None,
        }

        # Bearer-Beam: separate beam and bearer references per bridge type
        # Keys use bridge_type string ("concrete" / "timber")
        self._bb_beam_id = {
            BRIDGE_CONCRETE: None,
            BRIDGE_TIMBER:   None,
        }
        self._bb_bearer_id = {
            BRIDGE_CONCRETE: None,
            BRIDGE_TIMBER:   None,
        }

        # Bracing Connection: beam and bearer references + loaded custom plate family symbols
        self._bracing_beam_id = {
            BRIDGE_CONCRETE: None,
            BRIDGE_TIMBER:   None,
        }
        self._bracing_bearer_id = {
            BRIDGE_CONCRETE: None,
            BRIDGE_TIMBER:   None,
        }
        self._bracing_face_stable_ref = {
            BRIDGE_CONCRETE: None,
            BRIDGE_TIMBER:   None,
        }
        self._bracing_face_point = {
            BRIDGE_CONCRETE: None,
            BRIDGE_TIMBER:   None,
        }
        self._bracing_plate_symbols = []

        # Available structural connection types (shared across both bridge types)
        self._connection_types = []

        self._load_xaml()
        self._bind_controls()
        self._populate_connection_dropdowns()

    # ------------------------------------------------------------------
    # XAML loading
    # ------------------------------------------------------------------

    def _load_xaml(self):
        xaml_path = os.path.join(self._bundle_dir, "ui", "ApplyConnectionsWindow.xaml")
        reader = StreamReader(xaml_path)
        try:
            self._window = XamlReader.Load(reader.BaseStream)
        finally:
            reader.Close()

    # ------------------------------------------------------------------
    # Control binding
    # ------------------------------------------------------------------

    def _bind_controls(self):
        w = self._window

        # --- Footer ---
        self._footer = w.FindName("FooterStatus")

        # --- Concrete Beam-Beam controls ---
        self._conn_combo_concrete    = w.FindName("ConnectionTypeCombo_Concrete")
        self._btn_select_concrete    = w.FindName("BtnSelectBeam_Concrete")
        self._beam_label_concrete    = w.FindName("SelectedBeamLabel_Concrete")
        self._beam_card_concrete     = w.FindName("SelectedBeamCard_Concrete")
        self._result_text_concrete   = w.FindName("ResultText_Concrete")
        self._result_card_concrete   = w.FindName("ResultCard_Concrete")
        self._btn_apply_concrete     = w.FindName("BtnApplyBeamBeam_Concrete")

        # --- Timber Beam-Beam controls ---
        self._conn_combo_timber      = w.FindName("ConnectionTypeCombo_Timber")
        self._btn_select_timber      = w.FindName("BtnSelectBeam_Timber")
        self._beam_label_timber      = w.FindName("SelectedBeamLabel_Timber")
        self._beam_card_timber       = w.FindName("SelectedBeamCard_Timber")
        self._result_text_timber     = w.FindName("ResultText_Timber")
        self._result_card_timber     = w.FindName("ResultCard_Timber")
        self._btn_apply_timber       = w.FindName("BtnApplyBeamBeam_Timber")

        # --- Connection Direction toggles (ToggleButton: unchecked=Default, checked=Reversed) ---
        self._dir_toggle_concrete    = w.FindName("DirectionToggle_Concrete")
        self._dir_toggle_timber      = w.FindName("DirectionToggle_Timber")

        # --- Bearer-Beam controls (Concrete) ---
        self._bb_conn_combo_concrete   = w.FindName("ConnectionTypeCombo_BB_Concrete")
        self._bb_btn_beam_concrete     = w.FindName("BtnSelectBBBeam_Concrete")
        self._bb_beam_label_concrete   = w.FindName("SelectedBBBeamLabel_Concrete")
        self._bb_btn_bearer_concrete   = w.FindName("BtnSelectBBBearer_Concrete")
        self._bb_bearer_label_concrete = w.FindName("SelectedBBBearerLabel_Concrete")
        self._bb_result_text_concrete  = w.FindName("ResultText_BB_Concrete")
        self._bb_result_card_concrete  = w.FindName("ResultCard_BB_Concrete")
        self._bb_btn_apply_concrete    = w.FindName("BtnApplyBearerBeam_Concrete")
        self._bb_dir_toggle_concrete   = w.FindName("DirectionToggle_BB_Concrete")

        # --- Bearer-Beam controls (Timber) ---
        self._bb_conn_combo_timber     = w.FindName("ConnectionTypeCombo_BB_Timber")
        self._bb_btn_beam_timber       = w.FindName("BtnSelectBBBeam_Timber")
        self._bb_beam_label_timber     = w.FindName("SelectedBBBeamLabel_Timber")
        self._bb_btn_bearer_timber     = w.FindName("BtnSelectBBBearer_Timber")
        self._bb_bearer_label_timber   = w.FindName("SelectedBBBearerLabel_Timber")
        self._bb_result_text_timber    = w.FindName("ResultText_BB_Timber")
        self._bb_result_card_timber    = w.FindName("ResultCard_BB_Timber")
        self._bb_btn_apply_timber      = w.FindName("BtnApplyBearerBeam_Timber")
        self._bb_dir_toggle_timber     = w.FindName("DirectionToggle_BB_Timber")

        # --- Bracing Connection controls (Concrete) ---
        self._bracing_conn_combo_concrete   = w.FindName("ConnectionTypeCombo_Bracing_Concrete")
        self._bracing_btn_beam_concrete     = w.FindName("BtnSelectBracingBeam_Concrete")
        self._bracing_beam_label_concrete   = w.FindName("SelectedBracingBeamLabel_Concrete")
        self._bracing_btn_bearer_concrete   = w.FindName("BtnSelectBracingBearer_Concrete")
        self._bracing_bearer_label_concrete = w.FindName("SelectedBracingBearerLabel_Concrete")
        self._bracing_result_text_concrete  = w.FindName("ResultText_Bracing_Concrete")
        self._bracing_result_card_concrete  = w.FindName("ResultCard_Bracing_Concrete")
        self._bracing_btn_apply_concrete    = w.FindName("BtnApplyBracing_Concrete")
        self._bracing_dir_toggle_concrete   = w.FindName("DirectionToggle_Bracing_Concrete")
        self._bracing_btn_face_concrete     = w.FindName("BtnSelectBracingFace_Concrete")
        self._bracing_face_label_concrete   = w.FindName("SelectedBracingFaceLabel_Concrete")

        # --- Bracing Connection controls (Timber) ---
        self._bracing_conn_combo_timber     = w.FindName("ConnectionTypeCombo_Bracing_Timber")
        self._bracing_btn_beam_timber       = w.FindName("BtnSelectBracingBeam_Timber")
        self._bracing_beam_label_timber     = w.FindName("SelectedBracingBeamLabel_Timber")
        self._bracing_btn_bearer_timber     = w.FindName("BtnSelectBracingBearer_Timber")
        self._bracing_bearer_label_timber   = w.FindName("SelectedBracingBearerLabel_Timber")
        self._bracing_result_text_timber    = w.FindName("ResultText_Bracing_Timber")
        self._bracing_result_card_timber    = w.FindName("ResultCard_Bracing_Timber")
        self._bracing_btn_apply_timber      = w.FindName("BtnApplyBracing_Timber")
        self._bracing_dir_toggle_timber     = w.FindName("DirectionToggle_Bracing_Timber")
        self._bracing_btn_face_timber       = w.FindName("BtnSelectBracingFace_Timber")
        self._bracing_face_label_timber     = w.FindName("SelectedBracingFaceLabel_Timber")

        # --- Wire events ---
        self._btn_select_concrete.Click += lambda s, e: self._on_select_beam(BRIDGE_CONCRETE)
        self._btn_select_timber.Click   += lambda s, e: self._on_select_beam(BRIDGE_TIMBER)

        self._btn_apply_concrete.Click += lambda s, e: self._on_apply_beam_beam(BRIDGE_CONCRETE)
        self._btn_apply_timber.Click   += lambda s, e: self._on_apply_beam_beam(BRIDGE_TIMBER)

        # Bearer-Beam events
        self._bb_btn_beam_concrete.Click   += lambda s, e: self._on_select_bb_beam(BRIDGE_CONCRETE)
        self._bb_btn_bearer_concrete.Click += lambda s, e: self._on_select_bb_bearer(BRIDGE_CONCRETE)
        self._bb_btn_apply_concrete.Click  += lambda s, e: self._on_apply_bearer_beam(BRIDGE_CONCRETE)

        self._bb_btn_beam_timber.Click   += lambda s, e: self._on_select_bb_beam(BRIDGE_TIMBER)
        self._bb_btn_bearer_timber.Click += lambda s, e: self._on_select_bb_bearer(BRIDGE_TIMBER)
        self._bb_btn_apply_timber.Click  += lambda s, e: self._on_apply_bearer_beam(BRIDGE_TIMBER)

        # Bracing Connection events
        if self._bracing_btn_beam_concrete:
            self._bracing_btn_beam_concrete.Click += lambda s, e: self._on_select_bracing_beam(BRIDGE_CONCRETE)
        if self._bracing_btn_bearer_concrete:
            self._bracing_btn_bearer_concrete.Click += lambda s, e: self._on_select_bracing_bearer(BRIDGE_CONCRETE)
        if self._bracing_btn_face_concrete:
            self._bracing_btn_face_concrete.Click += lambda s, e: self._on_select_bracing_face(BRIDGE_CONCRETE)
        if self._bracing_btn_apply_concrete:
            self._bracing_btn_apply_concrete.Click += lambda s, e: self._on_apply_bracing_connection(BRIDGE_CONCRETE)

        if self._bracing_btn_beam_timber:
            self._bracing_btn_beam_timber.Click += lambda s, e: self._on_select_bracing_beam(BRIDGE_TIMBER)
        if self._bracing_btn_bearer_timber:
            self._bracing_btn_bearer_timber.Click += lambda s, e: self._on_select_bracing_bearer(BRIDGE_TIMBER)
        if self._bracing_btn_face_timber:
            self._bracing_btn_face_timber.Click += lambda s, e: self._on_select_bracing_face(BRIDGE_TIMBER)
        if self._bracing_btn_apply_timber:
            self._bracing_btn_apply_timber.Click += lambda s, e: self._on_apply_bracing_connection(BRIDGE_TIMBER)

        w.FindName("BtnClose").Click += lambda s, e: self._on_close(s, e)

    # ------------------------------------------------------------------
    # Populate connection type dropdowns
    # ------------------------------------------------------------------

    def _populate_connection_dropdowns(self):
        """Load structural connection types from the document into both dropdowns."""
        self._connection_types = get_connection_types(self._doc)

        if not catalog_available():
            hint = "(StructuralConnectionHandlerType not available in this Revit build)"
            for combo in [
                self._conn_combo_concrete, self._conn_combo_timber,
                self._bb_conn_combo_concrete, self._bb_conn_combo_timber,
                self._bracing_conn_combo_concrete, self._bracing_conn_combo_timber,
            ]:
                if combo is not None:
                    combo.Items.Add(hint)
                    combo.SelectedIndex = 0
                    combo.IsEnabled = False
            self._set_footer("Steel Connection API not available. Connection dropdown disabled.")
            self._logger.warning("StructuralConnectionHandlerType unavailable")
            return

        if not self._connection_types:
            hint = "(No structural connection types found in this document)"
            for combo in [
                self._conn_combo_concrete, self._conn_combo_timber,
                self._bb_conn_combo_concrete, self._bb_conn_combo_timber,
                self._bracing_conn_combo_concrete, self._bracing_conn_combo_timber,
            ]:
                if combo is not None:
                    combo.Items.Add(hint)
                    combo.SelectedIndex = 0
                    combo.IsEnabled = False
            self._set_footer(
                "No structural connection types found. "
                "Load connection definitions into the project first (Steel > Connections)."
            )
            self._logger.info("No connection types found in document")
            return

        # Populate all dropdowns (Beam-Beam, Bearer-Beam, and Bracing for each bridge type)
        for ct in self._connection_types:
            self._conn_combo_concrete.Items.Add(ct.name)
            self._conn_combo_timber.Items.Add(ct.name)
            self._bb_conn_combo_concrete.Items.Add(ct.name)
            self._bb_conn_combo_timber.Items.Add(ct.name)
            if self._bracing_conn_combo_concrete is not None:
                self._bracing_conn_combo_concrete.Items.Add(ct.name)
            if self._bracing_conn_combo_timber is not None:
                self._bracing_conn_combo_timber.Items.Add(ct.name)

        self._conn_combo_concrete.SelectedIndex    = 0
        self._conn_combo_timber.SelectedIndex      = 0
        self._bb_conn_combo_concrete.SelectedIndex = 0
        self._bb_conn_combo_timber.SelectedIndex   = 0
        if self._bracing_conn_combo_concrete is not None:
            self._bracing_conn_combo_concrete.SelectedIndex = 0
        if self._bracing_conn_combo_timber is not None:
            self._bracing_conn_combo_timber.SelectedIndex = 0

        self._set_footer(
            "{0} structural connection type(s) available.".format(len(self._connection_types))
        )
        self._logger.info(
            "Connection types loaded",
            count=len(self._connection_types),
        )

    # ------------------------------------------------------------------
    # Get currently selected connection type ElementId
    # ------------------------------------------------------------------

    def _get_selected_connection_type_id(self, bridge_type):
        """Return the ElementId of the selected connection type, or None.

        Args:
            bridge_type: BRIDGE_CONCRETE or BRIDGE_TIMBER

        Returns:
            Autodesk.Revit.DB.ElementId or None
        """
        if bridge_type == BRIDGE_CONCRETE:
            combo = self._conn_combo_concrete
        else:
            combo = self._conn_combo_timber

        idx = combo.SelectedIndex
        if idx < 0 or idx >= len(self._connection_types):
            return None
        return self._connection_types[idx].element_id

    # ------------------------------------------------------------------
    # Controls for a given bridge type
    # ------------------------------------------------------------------

    def _controls(self, bridge_type):
        """Return the UI control references for a given bridge type (Beam-Beam).

        Returns:
            (beam_label, result_text, result_card)
        """
        if bridge_type == BRIDGE_CONCRETE:
            return (
                self._beam_label_concrete,
                self._result_text_concrete,
                self._result_card_concrete,
            )
        return (
            self._beam_label_timber,
            self._result_text_timber,
            self._result_card_timber,
        )

    def _bb_controls(self, bridge_type):
        """Return the UI control references for Bearer-Beam for a given bridge type.

        Returns:
            (beam_label, bearer_label, result_text, result_card)
        """
        if bridge_type == BRIDGE_CONCRETE:
            return (
                self._bb_beam_label_concrete,
                self._bb_bearer_label_concrete,
                self._bb_result_text_concrete,
                self._bb_result_card_concrete,
            )
        return (
            self._bb_beam_label_timber,
            self._bb_bearer_label_timber,
            self._bb_result_text_timber,
            self._bb_result_card_timber,
        )

    def _bracing_controls(self, bridge_type):
        """Return the UI control references for Bracing Connection for a given bridge type.

        Returns:
            (beam_label, bearer_label, face_label, result_text, result_card)
        """
        if bridge_type == BRIDGE_CONCRETE:
            return (
                self._bracing_beam_label_concrete,
                self._bracing_bearer_label_concrete,
                self._bracing_face_label_concrete,
                self._bracing_result_text_concrete,
                self._bracing_result_card_concrete,
            )
        return (
            self._bracing_beam_label_timber,
            self._bracing_bearer_label_timber,
            self._bracing_face_label_timber,
            self._bracing_result_text_timber,
            self._bracing_result_card_timber,
        )

    def _get_bracing_plate_symbol_id(self, bridge_type):
        """Return the ElementId of the selected structural connection type for bracing, or None."""
        combo = self._bracing_conn_combo_concrete if bridge_type == BRIDGE_CONCRETE else self._bracing_conn_combo_timber
        if combo is None:
            return None
        idx = combo.SelectedIndex
        if idx < 0 or idx >= len(self._connection_types):
            return None
        return self._connection_types[idx].element_id

    def _on_select_bracing_beam(self, bridge_type):
        """Allow user to pick a Reference Beam for Bracing Connection."""
        self._window.Hide()
        element = None
        cancelled = False
        try:
            element, cancelled = pick_structural_framing(self._uidoc)
        except Exception as ex:
            self._logger.error("Error during bracing reference beam selection", exc=ex)
        finally:
            self._window.Show()

        if cancelled or element is None:
            self._set_footer("Reference Beam selection cancelled.")
            return

        self._bracing_beam_id[bridge_type] = element.Id
        beam_label, _, _, _, _ = self._bracing_controls(bridge_type)
        display = describe_element(element)
        try:
            type_id_val = element.GetTypeId().IntegerValue
        except Exception:
            type_id_val = "Unknown"

        beam_label.Text = "{0}\nTypeId: {1}".format(display, type_id_val)
        self._set_bracing_result(bridge_type, "Reference Beam selected: " + display, neutral=True)
        self._set_footer("Reference Beam selected. Now select a Reference Bearer.")

    def _on_select_bracing_bearer(self, bridge_type):
        """Allow user to pick a Reference Bearer for Bracing Connection."""
        self._window.Hide()
        element = None
        cancelled = False
        try:
            element, cancelled = pick_structural_framing(self._uidoc)
        except Exception as ex:
            self._logger.error("Error during bracing reference bearer selection", exc=ex)
        finally:
            self._window.Show()

        if cancelled or element is None:
            self._set_footer("Reference Bearer selection cancelled.")
            return

        self._bracing_bearer_id[bridge_type] = element.Id
        _, bearer_label, _, _, _ = self._bracing_controls(bridge_type)
        display = describe_element(element)
        try:
            type_id_val = element.GetTypeId().IntegerValue
        except Exception:
            type_id_val = "Unknown"

        bearer_label.Text = "{0}\nTypeId: {1}".format(display, type_id_val)
        self._set_bracing_result(bridge_type, "Reference Bearer selected: " + display, neutral=True)
        self._set_footer("Reference Bearer selected. Now select the Bearer's bottom face.")

    def _on_select_bracing_face(self, bridge_type):
        """Allow user to pick a face on the selected Reference Bearer."""
        bearer_id = self._bracing_bearer_id.get(bridge_type)
        if bearer_id is None:
            msg = "Select a Reference Bearer first."
            self._set_bracing_result(bridge_type, msg, error=True)
            self._set_footer(msg)
            return

        self._window.Hide()
        reference = None
        point = None
        cancelled = False
        try:
            reference, point, cancelled = pick_face(self._uidoc, bearer_id)
        except Exception as ex:
            self._logger.error("Error during bracing face selection", exc=ex)
        finally:
            self._window.Show()

        if cancelled or reference is None:
            self._set_footer("Face selection cancelled.")
            return

        stable_ref = reference.ConvertToStableRepresentation(self._doc)
        self._bracing_face_stable_ref[bridge_type] = stable_ref
        self._bracing_face_point[bridge_type] = point

        _, _, face_label, _, _ = self._bracing_controls(bridge_type)
        
        face_label.Text = "Face selected\nPt: ({0:.2f}, {1:.2f}, {2:.2f})".format(point.X, point.Y, point.Z)
        self._set_bracing_result(bridge_type, "Face selected on Bearer.", neutral=True)
        self._set_footer("Face selected. Click Apply to place the custom Bracing Connection plate.")

    def _on_apply_bracing_connection(self, bridge_type):
        """Run the Bracing Connection plate placement test workflow."""
        symbol_id = self._get_bracing_plate_symbol_id(bridge_type)
        if symbol_id is None:
            msg = "No custom bracing connection plate family selected."
            self._set_bracing_result(bridge_type, msg, error=True)
            self._set_footer(msg)
            return

        # BYPASS BEAM CHECK
        # beam_id = self._bracing_beam_id.get(bridge_type)
        # if beam_id is None: ...

        bearer_id = self._bracing_bearer_id.get(bridge_type)
        if bearer_id is None:
            msg = "No Reference Bearer selected. Click 'Select Reference Bearer' first."
            self._set_bracing_result(bridge_type, msg, error=True)
            self._set_footer(msg)
            return

        stable_ref = self._bracing_face_stable_ref.get(bridge_type)
        point = self._bracing_face_point.get(bridge_type)
        if stable_ref is None or point is None:
            msg = "No Bearer Face selected. Click 'Select Bearer Bottom Face' first."
            self._set_bracing_result(bridge_type, msg, error=True)
            self._set_footer(msg)
            return

        self._set_bracing_result(bridge_type, "Placing custom Bracing Connection plate...", neutral=True)
        self._set_footer("Executing single face-based placement...")
        self._update_ui()

        if self._handler and self._ext_event:
            self._handler.request_type        = REQUEST_APPLY_BRACING_CONNECTION
            self._handler.bridge_type         = bridge_type
            self._handler.connection_type_id  = symbol_id
            self._handler.ref_bearer_id       = bearer_id
            
            # Send the stable face reference and point as well
            self._handler.stable_face_ref     = stable_ref
            self._handler.face_point          = point

            self._ext_event.Raise()
        else:
            self._set_bracing_result(bridge_type, "Internal error: ExternalEvent not initialized.", error=True)


    def _get_bb_connection_type_id(self, bridge_type):
        """Return the ElementId of the selected Bearer-Beam connection type, or None."""
        if bridge_type == BRIDGE_CONCRETE:
            combo = self._bb_conn_combo_concrete
        else:
            combo = self._bb_conn_combo_timber

        idx = combo.SelectedIndex
        if idx < 0 or idx >= len(self._connection_types):
            return None
        return self._connection_types[idx].element_id

    # ------------------------------------------------------------------
    # Event: Select Reference Beam (Bearer–Beam)
    # ------------------------------------------------------------------

    def _on_select_bb_beam(self, bridge_type):
        """Handle 'Select Reference Beam' click for Bearer-Beam."""
        beam_label, bearer_label, result_text, result_card = self._bb_controls(bridge_type)

        self._window.Hide()
        element = None
        cancelled = False
        try:
            element, cancelled = pick_structural_framing(self._uidoc)
        except Exception as ex:
            self._logger.error("Error during BB reference beam selection", exc=ex)
        finally:
            self._window.Show()

        if cancelled or element is None:
            self._set_bb_result(bridge_type, "Beam selection cancelled.", neutral=True)
            self._set_footer("Beam selection cancelled.")
            return

        ok, msg, validated = validate_main_beam(self._doc, element.Id)
        if not ok:
            self._set_bb_result(bridge_type, msg, error=True)
            self._set_footer("Invalid beam selection: " + msg)
            return

        self._bb_beam_id[bridge_type] = element.Id
        display = describe_element(element)

        try:
            type_id_val = element.GetTypeId().IntegerValue
        except Exception:
            type_id_val = "Unknown"

        beam_label.Text = "{0}\nTypeId: {1}".format(display, type_id_val)
        self._set_bb_result(bridge_type, "Reference Beam selected: " + display, neutral=True)
        self._set_footer("Reference Beam selected. Now select a Reference Bearer.")
        self._logger.info(
            "BB Reference Beam selected",
            bridge_type=bridge_type,
            element=display,
            element_id=element_id_value(element.Id),
        )

    # ------------------------------------------------------------------
    # Event: Select Reference Bearer (Bearer–Beam)
    # ------------------------------------------------------------------

    def _on_select_bb_bearer(self, bridge_type):
        """Handle 'Select Reference Bearer' click for Bearer-Beam."""
        beam_label, bearer_label, result_text, result_card = self._bb_controls(bridge_type)

        self._window.Hide()
        element = None
        cancelled = False
        try:
            element, cancelled = pick_structural_framing(self._uidoc)
        except Exception as ex:
            self._logger.error("Error during BB reference bearer selection", exc=ex)
        finally:
            self._window.Show()

        if cancelled or element is None:
            self._set_bb_result(bridge_type, "Bearer selection cancelled.", neutral=True)
            self._set_footer("Bearer selection cancelled.")
            return

        ok, msg, validated = validate_main_beam(self._doc, element.Id)
        if not ok:
            self._set_bb_result(bridge_type, msg, error=True)
            self._set_footer("Invalid bearer selection: " + msg)
            return

        self._bb_bearer_id[bridge_type] = element.Id
        display = describe_element(element)

        try:
            type_id_val = element.GetTypeId().IntegerValue
        except Exception:
            type_id_val = "Unknown"

        bearer_label.Text = "{0}\nTypeId: {1}".format(display, type_id_val)
        self._set_bb_result(bridge_type, "Reference Bearer selected: " + display, neutral=True)
        self._set_footer("Reference Bearer selected. Click Apply to run the Bearer-Beam workflow.")
        self._logger.info(
            "BB Reference Bearer selected",
            bridge_type=bridge_type,
            element=display,
            element_id=element_id_value(element.Id),
        )

    # ------------------------------------------------------------------
    # Event: Apply Bearer–Beam Connections
    # ------------------------------------------------------------------

    def _on_apply_bearer_beam(self, bridge_type):
        """Run the full Bearer-Beam connection workflow for the given bridge type."""
        # --- Step 1: Connection type ---
        conn_type_id = self._get_bb_connection_type_id(bridge_type)
        ok, msg = validate_connection_selection(conn_type_id)
        if not ok:
            self._set_bb_result(bridge_type, msg, error=True)
            self._set_footer(msg)
            return

        # --- Step 2: Validate Reference Beam ---
        beam_id = self._bb_beam_id.get(bridge_type)
        if beam_id is None:
            msg = "No Reference Beam selected. Click 'Select Reference Beam' first."
            self._set_bb_result(bridge_type, msg, error=True)
            self._set_footer(msg)
            return

        ok, msg, _ = validate_main_beam(self._doc, beam_id)
        if not ok:
            self._set_bb_result(bridge_type, "Reference Beam invalid: " + msg, error=True)
            self._set_footer("Reference Beam invalid.")
            return

        # --- Step 3: Validate Reference Bearer ---
        bearer_id = self._bb_bearer_id.get(bridge_type)
        if bearer_id is None:
            msg = "No Reference Bearer selected. Click 'Select Reference Bearer' first."
            self._set_bb_result(bridge_type, msg, error=True)
            self._set_footer(msg)
            return

        ok, msg, _ = validate_main_beam(self._doc, bearer_id)
        if not ok:
            self._set_bb_result(bridge_type, "Reference Bearer invalid: " + msg, error=True)
            self._set_footer("Reference Bearer invalid.")
            return

        # --- Step 4: Guard — same TypeId means ambiguous roles ---
        try:
            beam_elem   = self._doc.GetElement(beam_id)
            bearer_elem = self._doc.GetElement(bearer_id)
            if beam_elem.GetTypeId() == bearer_elem.GetTypeId():
                msg = (
                    "The Reference Beam and Reference Bearer share the same Revit TypeId.\n"
                    "Cannot distinguish Beam from Bearer roles.\n"
                    "Please select references of DIFFERENT element types."
                )
                self._set_bb_result(bridge_type, msg, error=True)
                self._set_footer("Same TypeId — select a different Reference Bearer.")
                return
        except Exception as ex:
            self._logger.warning("TypeId pre-check failed; will rely on handler guard", exc=ex)

        # --- Step 5: Populate handler and raise ExternalEvent ---
        self._set_bb_result(bridge_type, "Detecting Bearer-to-Beam joints...", neutral=True)
        self._set_footer("Scanning for Bearer-Beam joints and applying connections...")
        self._update_ui()

        if self._handler and self._ext_event:
            if bridge_type == BRIDGE_CONCRETE:
                dir_toggle = self._bb_dir_toggle_concrete
            else:
                dir_toggle = self._bb_dir_toggle_timber

            is_reversed = False
            try:
                if dir_toggle is not None and dir_toggle.IsChecked:
                    is_reversed = True
            except Exception:
                pass

            self._handler.request_type      = REQUEST_APPLY_BEARER_BEAM
            # Use a bridge_type key that the callback can route to BB result cards
            self._handler.bridge_type       = bridge_type + "-bb"
            self._handler.connection_type_id = conn_type_id
            self._handler.ref_beam_id       = beam_id
            self._handler.ref_bearer_id     = bearer_id
            self._handler.reverse_direction = is_reversed
            self._ext_event.Raise()
        else:
            self._set_bb_result(
                bridge_type, "Internal error: ExternalEvent not initialized.", error=True
            )

    # ------------------------------------------------------------------
    # Event: Select Main Beam
    # ------------------------------------------------------------------

    def _on_select_beam(self, bridge_type):
        """Handle 'Select Main Beam' button click.

        Workflow:
            1. Hide window (returns control to Revit).
            2. PickObject with StructuralFramingFilter.
            3. Show window.
            4. Validate selected element.
            5. Update UI.
        """
        beam_label, result_text, result_card = self._controls(bridge_type)

        # Hide window to allow Revit viewport interaction
        self._window.Hide()
        element = None
        cancelled = False
        try:
            element, cancelled = pick_structural_framing(self._uidoc)
        except Exception as ex:
            self._logger.error("Unexpected error during element selection", exc=ex)
        finally:
            self._window.Show()

        if cancelled or element is None:
            self._set_result(bridge_type, "Selection cancelled.", neutral=True)
            self._set_footer("Selection cancelled.")
            return

        # --- Validate selected element ---
        ok, msg, validated = validate_main_beam(self._doc, element.Id)
        if not ok:
            self._set_result(bridge_type, msg, error=True)
            self._set_footer("Invalid selection: " + msg)
            self._logger.warning("Invalid beam selection", reason=msg)
            return

        # --- Store and display ---
        self._selected_beam_id[bridge_type] = element.Id
        display = describe_element(element)
        
        try:
            type_id_val = element.GetTypeId().IntegerValue
        except Exception:
            type_id_val = "Unknown"

        display_text = "{0}\nType ID: {1}".format(display, type_id_val)
        beam_label.Text = display_text

        self._set_result(bridge_type, "Reference beam selected: " + display, neutral=True)
        self._set_footer("Beam selected. Click 'Apply Beam-Beam Connections' to proceed.")
        self._logger.info(
            "Main beam selected",
            bridge_type=bridge_type,
            element=display,
            element_id=element_id_value(element.Id),
        )

    # ------------------------------------------------------------------
    # Event: Apply Beam–Beam Connections
    # ------------------------------------------------------------------

    def _on_apply_beam_beam(self, bridge_type):
        """Run the full Beam-Beam connection workflow for the given bridge type.

        Steps:
            1. Validate connection type selected.
            2. Validate main beam selected and still valid.
            3. Detect joints via joint_detector.
            4. Validate joints found.
            5. Apply connections in a Transaction.
            6. Report results.
        """
        beam_label, result_text, result_card = self._controls(bridge_type)

        # --- Step 1: Validate connection type ---
        conn_type_id = self._get_selected_connection_type_id(bridge_type)
        ok, msg = validate_connection_selection(conn_type_id)
        if not ok:
            self._set_result(bridge_type, msg, error=True)
            self._set_footer(msg)
            return

        # --- Step 2: Validate main beam ---
        beam_id = self._selected_beam_id.get(bridge_type)
        ok, msg, primary_elem = validate_main_beam(self._doc, beam_id)
        if not ok:
            self._set_result(bridge_type, msg, error=True)
            self._set_footer(msg)
            return

        # --- Step 3: Populate event request and raise ---
        self._set_result(bridge_type, "Detecting beam-to-beam joints...", neutral=True)
        self._set_footer("Scanning for joints and applying connections...")
        self._update_ui()

        if self._handler and self._ext_event:
            # Read Connection Direction toggle state
            if bridge_type == BRIDGE_CONCRETE:
                dir_toggle = self._dir_toggle_concrete
            else:
                dir_toggle = self._dir_toggle_timber

            is_reversed = False
            try:
                if dir_toggle is not None and dir_toggle.IsChecked:
                    is_reversed = True
            except Exception:
                pass

            self._handler.request_type      = REQUEST_APPLY_BEAM_BEAM
            self._handler.bridge_type       = bridge_type
            self._handler.connection_type_id = conn_type_id
            self._handler.main_beam_id      = beam_id
            self._handler.reverse_direction = is_reversed
            self._ext_event.Raise()
        else:
            self._set_result(bridge_type, "Internal error: ExternalEvent not initialized.", error=True)

    def _on_event_completed(self, bridge_type, result_dict):
        """Callback from ExternalEvent when it completes processing."""
        # This callback comes from the Revit API thread but we must update WPF UI safely
        try:
            import System                                               # type: ignore
            from System.Windows.Threading import Dispatcher             # type: ignore
            
            def update_action():
                status = result_dict.get("status")
                msg = result_dict.get("message", "")
                footer = result_dict.get("footer", None)

                # Bearer-Beam results are delivered with a "-bb" suffix
                if isinstance(bridge_type, str) and bridge_type.endswith("-bb"):
                    actual_bt = bridge_type[:-3]  # strip "-bb"
                    if status == "error":
                        self._set_bb_result(actual_bt, msg, error=True)
                    elif status == "warning":
                        self._set_bb_result(actual_bt, msg, warning=True)
                    elif status == "success":
                        self._set_bb_result(actual_bt, msg, success=True)
                    else:
                        self._set_bb_result(actual_bt, msg, neutral=True)
                elif isinstance(bridge_type, str) and bridge_type.endswith("-bracing"):
                    actual_bt = bridge_type[:-8]  # strip "-bracing"
                    if status == "error":
                        self._set_bracing_result(actual_bt, msg, error=True)
                    elif status == "warning":
                        self._set_bracing_result(actual_bt, msg, warning=True)
                    elif status == "success":
                        self._set_bracing_result(actual_bt, msg, success=True)
                    else:
                        self._set_bracing_result(actual_bt, msg, neutral=True)
                else:
                    # Beam-Beam path
                    if status == "error":
                        self._set_result(bridge_type, msg, error=True)
                    elif status == "warning":
                        self._set_result(bridge_type, msg, warning=True)
                    elif status == "success":
                        self._set_result(bridge_type, msg, success=True)
                    else:
                        self._set_result(bridge_type, msg, neutral=True)


                if footer:
                    self._set_footer(footer)
                else:
                    if status == "error":
                        self._set_footer("Operation failed.")

                self._update_ui()

            Dispatcher.CurrentDispatcher.Invoke(System.Action(update_action))
        except Exception as ex:
            self._logger.error("Failed to update UI from callback", exc=ex)

    # ------------------------------------------------------------------
    # UI helpers
    # ------------------------------------------------------------------

    def _set_result(self, bridge_type, message,
                    success=False, warning=False, error=False, neutral=False):
        """Update the result card text and background colour for a bridge type.

        Args:
            bridge_type: BRIDGE_CONCRETE or BRIDGE_TIMBER
            message:     str to display.
            success / warning / error / neutral: mutually exclusive colour flags.
        """
        _, result_text, result_card = self._controls(bridge_type)

        result_text.Text = message

        if success:
            result_card.Background = _BG_SUCCESS
            result_text.Foreground = _BRUSH_SUCCESS
        elif warning:
            result_card.Background = _BG_WARNING
            result_text.Foreground = _BRUSH_WARNING
        elif error:
            result_card.Background = _BG_ERROR
            result_text.Foreground = _BRUSH_ERROR
        else:
            # neutral / info
            result_card.Background = _BG_INFO
            result_text.Foreground = _BRUSH_INFO

    def _set_bb_result(self, bridge_type, message,
                       success=False, warning=False, error=False, neutral=False):
        """Update the Bearer-Beam result card text and background colour.

        Args:
            bridge_type: BRIDGE_CONCRETE or BRIDGE_TIMBER (without "-bb" suffix)
            message:     str to display.
            success / warning / error / neutral: mutually exclusive colour flags.
        """
        _, _, result_text, result_card = self._bb_controls(bridge_type)

        result_text.Text = message

        if success:
            result_card.Background = _BG_SUCCESS
            result_text.Foreground = _BRUSH_SUCCESS
        elif warning:
            result_card.Background = _BG_WARNING
            result_text.Foreground = _BRUSH_WARNING
        elif error:
            result_card.Background = _BG_ERROR
            result_text.Foreground = _BRUSH_ERROR
        else:
            # neutral / info
            result_card.Background = _BG_INFO
            result_text.Foreground = _BRUSH_INFO

    def _set_bracing_result(self, bridge_type, message,
                            success=False, warning=False, error=False, neutral=False):
        """Update the Bracing Connection result card text and background colour.

        Args:
            bridge_type: BRIDGE_CONCRETE or BRIDGE_TIMBER (without "-bracing" suffix)
            message:     str to display.
            success / warning / error / neutral: mutually exclusive colour flags.
        """
        _, _, _, result_text, result_card = self._bracing_controls(bridge_type)

        if result_text and result_card:
            result_text.Text = message

            if success:
                result_card.Background = _BG_SUCCESS
                result_text.Foreground = _BRUSH_SUCCESS
            elif warning:
                result_card.Background = _BG_WARNING
                result_text.Foreground = _BRUSH_WARNING
            elif error:
                result_card.Background = _BG_ERROR
                result_text.Foreground = _BRUSH_ERROR
            else:
                # neutral / info
                result_card.Background = _BG_INFO
                result_text.Foreground = _BRUSH_INFO


    def _set_footer(self, text):
        """Update the footer status bar text."""
        if self._footer:
            self._footer.Text = text

    def _update_ui(self):
        """Attempt to flush pending WPF dispatcher updates.

        Non-critical UI refresh. The window will update naturally after
        each operation. This is a best-effort call only.
        """
        # Best-effort: DoEvents equivalent via empty dispatcher callback.
        # Wrapped entirely in try/except so any IronPython/WPF edge case
        # cannot affect the main workflow.
        try:
            import System                                               # type: ignore
            from System.Windows.Threading import (                      # type: ignore
                Dispatcher,
                DispatcherPriority,
            )
            Dispatcher.CurrentDispatcher.Invoke(
                DispatcherPriority.Background,
                System.Action(lambda: None),
            )
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Window close
    # ------------------------------------------------------------------

    def _on_close(self, sender, args):
        self._window.Close()

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def show_dialog(self):
        """Show the window as a modal dialog."""
        self._window.ShowDialog()
