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
from core.connection_catalog   import (
    get_connection_types,
    get_bracing_plate_symbols,
    is_available as catalog_available,
    classify_connection_catalog,
    classify_bracing_catalog,
)
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

def _enumerate_symbol_parameters(symbol):
    """Extract length/double parameters from a FamilySymbol.
    
    Returns:
        list of dicts: [{"name": str, "value": float}, ...] sorted by name.
    """
    params = []
    if not symbol:
        return params
    try:
        from Autodesk.Revit.DB import StorageType
        for p in symbol.Parameters:
            if p.HasValue and p.StorageType == StorageType.Double:
                params.append({
                    "name": p.Definition.Name,
                    "value": p.AsDouble()
                })
        params.sort(key=lambda x: x["name"])
    except Exception:
        pass
    return params


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

        # Flat parallel index lists — one entry per ComboBox item.
        # None at header positions; ConnectionTypeItem at selectable positions.
        # Index i in this list corresponds exactly to combo.Items[i].
        self._flat_beam_beam_items_concrete   = []  # ConnectionTypeCombo_Concrete
        self._flat_beam_beam_items_timber     = []  # ConnectionTypeCombo_Timber
        self._flat_bearer_beam_items_concrete = []  # ConnectionTypeCombo_BB_Concrete
        self._flat_bearer_beam_items_timber   = []  # ConnectionTypeCombo_BB_Timber
        self._flat_bracing_items_concrete     = []  # ConnectionTypeCombo_Bracing_Concrete
        self._flat_bracing_items_timber       = []  # ConnectionTypeCombo_Bracing_Timber

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
        self._bracing_btn_bearer_concrete   = w.FindName("BtnSelectBracingBearer_Concrete")
        self._bracing_bearer_label_concrete = w.FindName("SelectedBracingBearerLabel_Concrete")
        self._bracing_result_text_concrete  = w.FindName("ResultText_Bracing_Concrete")
        self._bracing_result_card_concrete  = w.FindName("ResultCard_Bracing_Concrete")
        self._bracing_btn_apply_concrete    = w.FindName("BtnApplyBracing_Concrete")
        self._bracing_flange_offset_concrete = w.FindName("BracingFlangeOffset_Concrete")

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
        self._bracing_beam_width_combo_timber = w.FindName("BracingBeamWidthCombo_Timber")
        self._bracing_flange_offset_timber    = w.FindName("BracingFlangeOffset_Timber")

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
        if self._bracing_btn_bearer_concrete:
            self._bracing_btn_bearer_concrete.Click += lambda s, e: self._on_select_bracing_bearer(BRIDGE_CONCRETE)
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
    # ComboBox item builders (hierarchy presentation layer)
    # ------------------------------------------------------------------

    def _make_header_item(self, family_name):
        """Return a non-selectable WPF ComboBoxItem styled as a Family header.

        IsEnabled=False prevents keyboard navigation and selection.
        The style comes from CatalogHeaderStyle in the XAML resources.
        """
        from System.Windows.Controls import ComboBoxItem  # type: ignore
        ci = ComboBoxItem()
        ci.Content = family_name
        try:
            ci.Style = self._window.FindResource("CatalogHeaderStyle")
        except Exception:
            # If resource lookup fails, apply minimal styling in Python
            from System.Windows.Media import SolidColorBrush, Color  # type: ignore
            ci.Background = SolidColorBrush(Color.FromRgb(0xEE, 0xF2, 0xF9))
            ci.FontWeight = getattr(
                __import__("System.Windows", fromlist=["FontWeights"]).FontWeights,
                "SemiBold",
                None,
            ) or ci.FontWeight
        ci.IsEnabled = False
        return ci

    def _make_child_item(self, type_name, original_item):
        """Return an indented, selectable WPF ComboBoxItem for a Type child row.

        The style comes from CatalogChildStyle in the XAML resources.
        .Tag holds the original ConnectionTypeItem for safe unwrapping.
        """
        from System.Windows.Controls import ComboBoxItem  # type: ignore
        ci = ComboBoxItem()
        ci.Content = type_name
        ci.Tag = original_item
        try:
            ci.Style = self._window.FindResource("CatalogChildStyle")
        except Exception:
            pass
        return ci

    def _make_flat_item(self, name, original_item):
        """Return a normal, selectable WPF ComboBoxItem for a flat (non-grouped) row.

        No indent. .Tag holds the original ConnectionTypeItem.
        """
        from System.Windows.Controls import ComboBoxItem  # type: ignore
        ci = ComboBoxItem()
        ci.Content = name
        ci.Tag = original_item
        return ci

    def _build_catalog_combo_items(self, entries):
        """Convert a list of CatalogEntry objects into (wpf_items, flat_items).

        wpf_items  : list of ComboBoxItem — to be added to combo.Items in order.
        flat_items : parallel list of ConnectionTypeItem-or-None.
                     None at every header position; ConnectionTypeItem at every
                     selectable position.

        The two lists are always the same length.
        """
        wpf_items  = []
        flat_items = []
        emitted_families = {}  # family_int_id → bool (already emitted a header)

        for entry in entries:
            if entry.kind == "family_type":
                fid = entry.family_int_id
                if fid not in emitted_families:
                    emitted_families[fid] = True
                    header = self._make_header_item(entry.family_name)
                    wpf_items.append(header)
                    flat_items.append(None)  # header — not an applicable item

                child = self._make_child_item(entry.type_name, entry.original_item)
                wpf_items.append(child)
                flat_items.append(entry.original_item)

            else:  # flat
                flat_ci = self._make_flat_item(entry.type_name, entry.original_item)
                wpf_items.append(flat_ci)
                flat_items.append(entry.original_item)

        return wpf_items, flat_items

    # ------------------------------------------------------------------
    # Populate connection type dropdowns
    # ------------------------------------------------------------------

    def _populate_connection_dropdowns(self):
        """Load structural connection types from the document into all dropdowns.

        Classifies each catalog item via the real Revit API to detect genuine
        FamilySymbol->Family relationships, then builds Family-header + child-Type
        rows.  Items with no genuine hierarchy remain as flat selectable rows.
        Family headers are non-selectable (IsEnabled=False) and do not count
        toward the connection-type count shown in the footer.
        """
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

        # ------------------------------------------------------------------
        # Classify catalog into Family/Type hierarchy entries
        # ------------------------------------------------------------------
        try:
            conn_entries = classify_connection_catalog(self._connection_types, self._doc)
        except Exception as ex:
            self._logger.warning("Catalog classification failed; falling back to flat", exc=ex)
            from core.connection_catalog import CatalogEntry
            conn_entries = [
                CatalogEntry("flat", None, None, ct.name, ct)
                for ct in self._connection_types
            ]

        # Build WPF items + parallel flat index lists for Beam-Beam combos
        bb_wpf_items, bb_flat = self._build_catalog_combo_items(conn_entries)

        # Beam-Beam Concrete
        self._flat_beam_beam_items_concrete = list(bb_flat)
        for ci in bb_wpf_items:
            self._conn_combo_concrete.Items.Add(ci)
        self._conn_combo_concrete.SelectedIndex = self._first_selectable(bb_flat)

        # Beam-Beam Timber (independent copy of same items)
        self._flat_beam_beam_items_timber = list(bb_flat)
        for ci in bb_wpf_items:
            # Must create new ComboBoxItem objects — a single object can only
            # be owned by one WPF Items collection at a time.
            from System.Windows.Controls import ComboBoxItem  # type: ignore
            new_ci = ComboBoxItem()
            new_ci.Content  = ci.Content
            new_ci.Tag      = ci.Tag
            new_ci.IsEnabled = ci.IsEnabled
            try:
                if ci.IsEnabled:
                    new_ci.Style = self._window.FindResource("CatalogChildStyle")
                else:
                    new_ci.Style = self._window.FindResource("CatalogHeaderStyle")
            except Exception:
                pass
            self._conn_combo_timber.Items.Add(new_ci)
        self._conn_combo_timber.SelectedIndex = self._first_selectable(bb_flat)

        # Build WPF items + parallel flat index lists for Bearer-Beam combos
        # (same underlying entries as Beam-Beam but each combo owns its own objects)
        def _clone_combo_items(wpf_items, flat_items, target_combo, flat_list_attr):
            """Clone wpf_items into target_combo and store the flat index list."""
            cloned_flat = list(flat_items)
            setattr(self, flat_list_attr, cloned_flat)
            for ci in wpf_items:
                from System.Windows.Controls import ComboBoxItem  # type: ignore
                new_ci = ComboBoxItem()
                new_ci.Content   = ci.Content
                new_ci.Tag       = ci.Tag
                new_ci.IsEnabled = ci.IsEnabled
                try:
                    if ci.IsEnabled:
                        new_ci.Style = self._window.FindResource("CatalogChildStyle")
                    else:
                        new_ci.Style = self._window.FindResource("CatalogHeaderStyle")
                except Exception:
                    pass
                target_combo.Items.Add(new_ci)
            target_combo.SelectedIndex = self._first_selectable(flat_items)

        _clone_combo_items(
            bb_wpf_items, bb_flat,
            self._bb_conn_combo_concrete,
            "_flat_bearer_beam_items_concrete",
        )
        _clone_combo_items(
            bb_wpf_items, bb_flat,
            self._bb_conn_combo_timber,
            "_flat_bearer_beam_items_timber",
        )

        # ------------------------------------------------------------------
        # Bracing dropdown — uses get_bracing_plate_symbols (FamilySymbols)
        # ------------------------------------------------------------------
        self._bracing_plate_symbols = get_bracing_plate_symbols(self._doc)
        if not self._bracing_plate_symbols:
            self._logger.info("No custom Bracing FamilySymbols found in document")
            hint = "(No custom Bracing connection plates found)"
            if self._bracing_conn_combo_concrete is not None:
                self._bracing_conn_combo_concrete.Items.Add(hint)
                self._bracing_conn_combo_concrete.SelectedIndex = 0
            if self._bracing_conn_combo_timber is not None:
                self._bracing_conn_combo_timber.Items.Add(hint)
                self._bracing_conn_combo_timber.SelectedIndex = 0
        else:
            try:
                bracing_entries = classify_bracing_catalog(self._bracing_plate_symbols, self._doc)
            except Exception as ex:
                self._logger.warning("Bracing catalog classification failed; falling back to flat", exc=ex)
                from core.connection_catalog import CatalogEntry
                bracing_entries = [
                    CatalogEntry("flat", None, None, st.name, st)
                    for st in self._bracing_plate_symbols
                ]

            br_wpf_items, br_flat = self._build_catalog_combo_items(bracing_entries)

            # Bracing Concrete
            self._flat_bracing_items_concrete = list(br_flat)
            for ci in br_wpf_items:
                if self._bracing_conn_combo_concrete is not None:
                    self._bracing_conn_combo_concrete.Items.Add(ci)
            if self._bracing_conn_combo_concrete is not None:
                self._bracing_conn_combo_concrete.SelectedIndex = self._first_selectable(br_flat)

            # Bracing Timber (cloned)
            _clone_combo_items(
                br_wpf_items, br_flat,
                self._bracing_conn_combo_timber,
                "_flat_bracing_items_timber",
            ) if self._bracing_conn_combo_timber is not None else None

            # Handle None timber combo gracefully
            if self._bracing_conn_combo_timber is None:
                self._flat_bracing_items_timber = list(br_flat)

        # Footer: count only actual selectable items (exclude header Nones)
        self._set_footer(
            "{0} structural connection type(s) available.".format(len(self._connection_types))
        )
        self._logger.info(
            "Connection types loaded",
            count=len(self._connection_types),
        )

    @staticmethod
    def _first_selectable(flat_items):
        """Return the index of the first non-None entry in flat_items, or 0."""
        for i, item in enumerate(flat_items):
            if item is not None:
                return i
        return 0

    # ------------------------------------------------------------------
    # Get currently selected connection type ElementId
    # ------------------------------------------------------------------

    def _get_selected_connection_type_id(self, bridge_type):
        """Return the ElementId of the selected Beam-Beam connection type, or None.

        Uses the flat parallel index list so that Family header rows (None
        entries) are safely ignored and never forwarded to the backend.

        Args:
            bridge_type: BRIDGE_CONCRETE or BRIDGE_TIMBER

        Returns:
            Autodesk.Revit.DB.ElementId or None
        """
        if bridge_type == BRIDGE_CONCRETE:
            combo      = self._conn_combo_concrete
            flat_items = self._flat_beam_beam_items_concrete
        else:
            combo      = self._conn_combo_timber
            flat_items = self._flat_beam_beam_items_timber

        idx = combo.SelectedIndex
        if idx < 0 or idx >= len(flat_items):
            return None
        item = flat_items[idx]
        if item is None:  # header row — no valid ElementId
            return None
        return item.element_id

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
                None,
                self._bracing_bearer_label_concrete,
                None,
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
        """Return the ElementId of the selected Bracing plate symbol, or None.

        Uses the flat parallel index list so that Family header rows (None
        entries) are safely ignored and never forwarded to the backend.
        """
        if bridge_type == BRIDGE_CONCRETE:
            combo      = self._bracing_conn_combo_concrete
            flat_items = self._flat_bracing_items_concrete
        else:
            combo      = self._bracing_conn_combo_timber
            flat_items = self._flat_bracing_items_timber

        if combo is None or not flat_items:
            return None
        idx = combo.SelectedIndex
        if idx < 0 or idx >= len(flat_items):
            return None
        item = flat_items[idx]
        if item is None:  # header row
            return None
        return item.element_id

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
            symbol = self._doc.GetElement(element.GetTypeId())
            
            # Populate parameter dropdown
            combo = self._bracing_beam_width_combo_concrete if bridge_type == BRIDGE_CONCRETE else self._bracing_beam_width_combo_timber
            if combo is not None:
                combo.Items.Clear()
                params = _enumerate_symbol_parameters(symbol)
                for p in params:
                    combo.Items.Add(p["name"])
                if combo.Items.Count > 0:
                    combo.SelectedIndex = 0
                else:
                    combo.Items.Add("(No numeric parameters)")
                    combo.SelectedIndex = 0
                    
        except Exception as e:
            type_id_val = "Unknown"
            self._logger.error("Failed to populate beam parameters", exc=e)

        beam_label.Text = "{0}\nTypeId: {1}".format(display, type_id_val)
        self._set_bracing_result(bridge_type, "Reference Beam selected: " + display, neutral=True)
        self._set_footer("Reference Beam selected. Choose width parameter, then select a Reference Bearer.")

    def _on_select_bracing_bearer(self, bridge_type):
        """Allow user to pick a Reference Bearer for Bracing Connection."""
        if bridge_type == BRIDGE_CONCRETE:
            self._window.Hide()
            element = None
            point = None
            cancelled = False
            try:
                from core.selection import pick_structural_framing_with_point
                element, point, cancelled = pick_structural_framing_with_point(self._uidoc, "Select Bearer / End")
            except Exception as ex:
                self._logger.error("Error during bracing reference bearer selection", ex)
            finally:
                self._window.Show()

            if cancelled or element is None:
                self._set_footer("Bearer selection cancelled.")
                return

            self._bracing_bearer_id[bridge_type] = element.Id
            self._bracing_face_point[bridge_type] = point

            bearer_label = self._bracing_bearer_label_concrete
            display = describe_element(element)
            
            bearer_label.Text = "Bearer: {0}\nEnd 0: detected\nEnd 1: detected\nHost Face: Bottom face automatically detected".format(display)
            self._set_bracing_result(bridge_type, "Bearer / End selected.", neutral=True)
            self._set_footer("Bearer selected. Click Apply to place the custom Bracing Connection plate.")
            return

        # Timber fallback
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

        if bridge_type == BRIDGE_CONCRETE:
            bearer_id = self._bracing_bearer_id.get(bridge_type)
            point = self._bracing_face_point.get(bridge_type)
            
            if bearer_id is None or point is None:
                msg = "No Bearer selected. Click 'Select Bearer' first."
                self._set_bracing_result(bridge_type, msg, error=True)
                self._set_footer(msg)
                return
                
            offset_tb = self._bracing_flange_offset_concrete
            try:
                user_offset_mm = float(offset_tb.Text)
                if user_offset_mm < 0:
                    raise ValueError("Offset must be positive.")
            except Exception:
                msg = "Invalid Offset. Please enter a valid number (e.g., 30.0)."
                self._set_bracing_result(bridge_type, msg, error=True)
                self._set_footer(msg)
                return

            self._set_bracing_result(bridge_type, "Placing custom Bracing Connection plate...", neutral=True)
            self._set_footer("Executing physical geometry solver...")
            self._update_ui()

            if self._handler and self._ext_event:
                self._handler.request_type        = REQUEST_APPLY_BRACING_CONNECTION
                self._handler.bridge_type         = bridge_type
                self._handler.connection_type_id  = symbol_id
                self._handler.ref_bearer_id       = bearer_id
                self._handler.click_point         = point
                self._handler.clearance_mm        = user_offset_mm
                
                self._handler.ref_beam_id         = None
                self._handler.stable_face_ref     = None
                self._handler.face_point          = None
                self._handler.width_param_name    = None
                
                self._ext_event.Raise()
            return

        # Timber fallback
        # Reintroduce single beam reference for geometry calculation
        beam_id = self._bracing_beam_id.get(bridge_type)
        if beam_id is None:
            msg = "No Reference Beam selected. Click 'Select Reference Beam' first."
            self._set_bracing_result(bridge_type, msg, error=True)
            self._set_footer(msg)
            return

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
            
        # Validate Parameter Selection
        width_combo = self._bracing_beam_width_combo_timber
        if width_combo is None or width_combo.SelectedIndex < 0 or width_combo.SelectedItem == "(No numeric parameters)":
            msg = "Please select a valid Beam Width Parameter."
            self._set_bracing_result(bridge_type, msg, error=True)
            self._set_footer(msg)
            return
            
        param_name = width_combo.SelectedItem
        
        # Validate User Offset
        offset_tb = self._bracing_flange_offset_timber
        try:
            user_offset_mm = float(offset_tb.Text)
            if user_offset_mm < 0:
                raise ValueError("Offset must be positive.")
        except Exception:
            msg = "Invalid Flange Edge Offset. Please enter a valid number (e.g., 30.0)."
            self._set_bracing_result(bridge_type, msg, error=True)
            self._set_footer(msg)
            return

        self._set_bracing_result(bridge_type, "Placing custom Bracing Connection plate...", neutral=True)
        self._set_footer("Executing single face-based placement with lateral offset...")
        self._update_ui()

        if self._handler and self._ext_event:
            self._handler.request_type        = REQUEST_APPLY_BRACING_CONNECTION
            self._handler.bridge_type         = bridge_type
            self._handler.connection_type_id  = symbol_id
            self._handler.ref_bearer_id       = bearer_id
            self._handler.ref_beam_id         = beam_id
            
            # Send the stable face reference and point as well
            self._handler.stable_face_ref     = stable_ref
            self._handler.face_point          = point
            
            # Send width parameter info
            self._handler.beam_width_param_name = param_name
            self._handler.user_flange_offset_mm = user_offset_mm

            self._ext_event.Raise()
        else:
            self._set_bracing_result(bridge_type, "Internal error: ExternalEvent not initialized.", error=True)


    def _get_bb_connection_type_id(self, bridge_type):
        """Return the ElementId of the selected Bearer-Beam connection type, or None.

        Uses the flat parallel index list so that Family header rows (None
        entries) are safely ignored and never forwarded to the backend.
        """
        if bridge_type == BRIDGE_CONCRETE:
            combo      = self._bb_conn_combo_concrete
            flat_items = self._flat_bearer_beam_items_concrete
        else:
            combo      = self._bb_conn_combo_timber
            flat_items = self._flat_bearer_beam_items_timber

        idx = combo.SelectedIndex
        if idx < 0 or idx >= len(flat_items):
            return None
        item = flat_items[idx]
        if item is None:  # header row
            return None
        return item.element_id

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
                        # Just pass the error message to the result card
                        pass
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
