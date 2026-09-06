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
from core.connection_catalog   import get_connection_types, is_available as catalog_available
from core.validation           import (
    validate_connection_selection,
    validate_main_beam,
    validate_joints,
)
from core.selection            import pick_structural_framing, describe_element
from core.selection            import pick_structural_framing, describe_element
from core.external_event_handler import REQUEST_APPLY_BEAM_BEAM


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
        # Selected main beam ElementId for each bridge type
        self._selected_beam_id = {
            BRIDGE_CONCRETE: None,
            BRIDGE_TIMBER:   None,
        }

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

        # --- Wire events ---
        self._btn_select_concrete.Click += lambda s, e: self._on_select_beam(BRIDGE_CONCRETE)
        self._btn_select_timber.Click   += lambda s, e: self._on_select_beam(BRIDGE_TIMBER)

        self._btn_apply_concrete.Click += lambda s, e: self._on_apply_beam_beam(BRIDGE_CONCRETE)
        self._btn_apply_timber.Click   += lambda s, e: self._on_apply_beam_beam(BRIDGE_TIMBER)

        w.FindName("BtnClose").Click += lambda s, e: self._on_close(s, e)

    # ------------------------------------------------------------------
    # Populate connection type dropdowns
    # ------------------------------------------------------------------

    def _populate_connection_dropdowns(self):
        """Load structural connection types from the document into both dropdowns."""
        self._connection_types = get_connection_types(self._doc)

        if not catalog_available():
            hint = "(StructuralConnectionHandlerType not available in this Revit build)"
            self._conn_combo_concrete.Items.Add(hint)
            self._conn_combo_concrete.SelectedIndex = 0
            self._conn_combo_concrete.IsEnabled = False
            self._conn_combo_timber.Items.Add(hint)
            self._conn_combo_timber.SelectedIndex = 0
            self._conn_combo_timber.IsEnabled = False
            self._set_footer("Steel Connection API not available. Connection dropdown disabled.")
            self._logger.warning("StructuralConnectionHandlerType unavailable")
            return

        if not self._connection_types:
            hint = "(No structural connection types found in this document)"
            self._conn_combo_concrete.Items.Add(hint)
            self._conn_combo_concrete.SelectedIndex = 0
            self._conn_combo_concrete.IsEnabled = False
            self._conn_combo_timber.Items.Add(hint)
            self._conn_combo_timber.SelectedIndex = 0
            self._conn_combo_timber.IsEnabled = False
            self._set_footer(
                "No structural connection types found. "
                "Load connection definitions into the project first (Steel > Connections)."
            )
            self._logger.info("No connection types found in document")
            return

        # Populate both dropdowns with the same list
        for ct in self._connection_types:
            self._conn_combo_concrete.Items.Add(ct.name)
            self._conn_combo_timber.Items.Add(ct.name)

        self._conn_combo_concrete.SelectedIndex = 0
        self._conn_combo_timber.SelectedIndex   = 0

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
        """Return the UI control references for a given bridge type.

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
