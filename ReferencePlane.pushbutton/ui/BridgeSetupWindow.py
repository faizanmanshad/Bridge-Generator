# -*- coding: utf-8 -*-
"""
BridgeSetupWindow.py — Code-behind / view-model for BridgeSetupWindow.xaml.

Reference Plane.pushbutton / ui/
Urbana Bridge Generator — Revit 2024.3 / pyRevit 6.4.0 / IronPython 2.7

Three-tab setup tool:
  Tab ③ — Global Parameters: create/validate all 25 GPs and formulas.
  Tab ② — Bridge Configuration: select Span/Width/CRNK/Camber, load families.
  Tab ③ — Reference Planes: create/update the parametric reference skeleton.

Status icon/color mapping:
  Created          → ✓  green   (#2E7D32)
  Existing         → ✓  navy    (#1B3A6B)
  Updated          → ↑  blue    (#1565C0)
  Formula Applied  → ƒ  blue    (#1565C0)
  Skipped          → ○  grey    (#8A97AE)
  Conflict         → ⚠  orange  (#E65100)
  Error            → ✕  red     (#C62828)
  Missing          → ✕  red     (#C62828)
  Already Loaded   → ✓  navy    (#1B3A6B)
  Loaded           → ✓  green   (#2E7D32)
  Path Missing     → —  orange  (#E65100)
  Not Found        → ✕  red     (#C62828)
  Load Failed      → ✕  red     (#C62828)

Python compatibility: IronPython 2.7 — no f-strings, no dataclasses.
"""

import os
import clr
clr.AddReference("PresentationFramework")
clr.AddReference("PresentationCore")
clr.AddReference("WindowsBase")
clr.AddReference("RevitAPI")
clr.AddReference("RevitAPIUI")

from System.Windows import Visibility as System_Windows_Visibility_Visible
from System.Windows.Markup  import XamlReader
from System.IO              import StreamReader
from System.Windows.Media   import SolidColorBrush, Color
from System.Collections.ObjectModel import ObservableCollection

from Autodesk.Revit.DB import (
    Transaction,
    TransactionGroup,
    GlobalParametersManager,
    DoubleParameterValue,
)

from core.revit_compat import element_id_value

from core.logging_utils        import get_logger
from core.param_spec           import DEFINITIONS, PLANE_REQUIRED_GPS
from core.global_param_manager import ensure_all
from core.origin_provider      import get_bridge_center
from core.units                import mm_to_internal
from core.validation           import (
    required_global_parameters_present,
    active_view_is_supported,
)
from core.refplane_manager     import (
    ensure_horizontal_set,
    ensure_vertical_set,
    read_gp_values_mm,
)
from core.dimension_manager    import ensure_all_dimensions
from core.bridge_config        import (
    SUPPORTED_SPANS_M,
    SUPPORTED_WIDTHS_M,
    get_configs_for_span,
    config_display_name,
    crank_length_mm,
    default_camber_mm,
    is_special_22,
    validate_config,
    resolve_config_from_gp,
    nearest_span,
    nearest_width,
    span_display,
    width_display,
)
from core.family_manager import (
    FAMILY_DEFAULTS,
    FAMILY_NAMES,
    STATUS_ALREADY_LOADED, STATUS_LOADED,
    STATUS_ERROR, STATUS_LOAD_FAILED, STATUS_PATH_MISSING, STATUS_NOT_FOUND,
    get_family_library_root,
    inspect_family_types,
    load_selected_families,
)


# ---------------------------------------------------------------------------
# Color helpers
# ---------------------------------------------------------------------------

def _brush(hex_color):
    """Return a WPF SolidColorBrush from a #RRGGBB hex string."""
    c = Color.FromRgb(
        int(hex_color[1:3], 16),
        int(hex_color[3:5], 16),
        int(hex_color[5:7], 16),
    )
    return SolidColorBrush(c)


# Pre-built brushes
_BRUSH_GREEN  = _brush("#2E7D32")
_BRUSH_NAVY   = _brush("#1B3A6B")
_BRUSH_BLUE   = _brush("#1565C0")
_BRUSH_ORANGE = _brush("#E65100")
_BRUSH_RED    = _brush("#C62828")
_BRUSH_GREY   = _brush("#8A97AE")


_STATUS_MAP = {
    # Existing status codes (unchanged)
    "Created":         ("✓", _BRUSH_GREEN),
    "Existing":        ("✓", _BRUSH_NAVY),
    "Updated":         ("↑", _BRUSH_BLUE),
    "Formula Applied": ("ƒ", _BRUSH_BLUE),
    "Conflict":        ("⚠", _BRUSH_ORANGE),
    "Error":           ("✕", _BRUSH_RED),
    "Missing":         ("✕", _BRUSH_RED),
    # New status codes for config / family sections
    "Skipped":         ("○", _BRUSH_GREY),
    # Family-specific
    STATUS_ALREADY_LOADED: ("✓", _BRUSH_NAVY),
    STATUS_LOADED:         ("✓", _BRUSH_GREEN),
    STATUS_PATH_MISSING:   ("—", _BRUSH_ORANGE),
    STATUS_NOT_FOUND:      ("✕", _BRUSH_RED),
    STATUS_LOAD_FAILED:    ("✕", _BRUSH_RED),
    STATUS_ERROR:          ("✕", _BRUSH_RED),
}


def _status_decoration(status):
    """Return (icon_str, brush) for a given status string."""
    return _STATUS_MAP.get(status, ("·", _BRUSH_GREY))


# ---------------------------------------------------------------------------
# Simple view-model item (used by ListView / ItemsControl)
# ---------------------------------------------------------------------------

class StatusItem(object):
    """One row in a status list view."""

    def __init__(self, name, status, detail=""):
        icon, color = _status_decoration(status)
        self.Name        = name
        self.Status      = status
        self.Detail      = detail or ""
        self.Icon        = icon
        self.IconColor   = color
        self.StatusColor = color


# ---------------------------------------------------------------------------
# Window class
# ---------------------------------------------------------------------------

class BridgeSetupWindow(object):
    """Wrapper around the WPF BridgeSetupWindow XAML.

    Usage:
        win = BridgeSetupWindow(uidoc, bundle_dir)
        win.show_dialog()
    """

    def __init__(self, uidoc, bundle_dir):
        self._uidoc      = uidoc
        self._doc        = uidoc.Document
        self._bundle_dir = bundle_dir
        self._logger     = get_logger()
        self._window     = None

        # --- Observable collections ---
        self._param_items  = ObservableCollection[object]()
        self._plane_items  = ObservableCollection[object]()
        self._req_items    = ObservableCollection[object]()
        self._config_items = ObservableCollection[object]()
        self._family_items = ObservableCollection[object]()

        # --- Bridge Configuration state ---
        self._span_values    = list(SUPPORTED_SPANS_M)   # [4.0, 6.0, ...]
        self._width_values   = list(SUPPORTED_WIDTHS_M)  # [1.5, 2.0, ...]
        self._config_objects = []                         # config dicts for selected span
        self._current_config = None                       # selected config dict
        self._current_is_special_22 = False               # derived from current_config
        self._staged_config = None                        # temporarily saved config

        self._load_xaml()
        self._bind_controls()
        self._populate_config_tab()
        self._populate_tab2_prereqs()

    # ------------------------------------------------------------------
    # XAML loading
    # ------------------------------------------------------------------

    def _load_xaml(self):
        xaml_path = os.path.join(self._bundle_dir, "ui", "BridgeSetupWindow.xaml")
        reader = StreamReader(xaml_path)
        try:
            self._window = XamlReader.Load(reader.BaseStream)
        finally:
            reader.Close()

    def _bind_controls(self):
        w = self._window

        # --- Tab ② Global Parameters controls ---
        self._param_list    = w.FindName("ParamStatusList")
        self._param_summary = w.FindName("ParamSummaryLabel")

        # --- Tab ③ Reference Planes controls ---
        self._plane_list    = w.FindName("PlaneStatusList")
        self._plane_summary = w.FindName("PlaneSummaryLabel")
        self._req_list      = w.FindName("RequiredGPList")
        self._origin_text   = w.FindName("OriginInfoText")

        # --- Shared footer ---
        self._footer_status = w.FindName("FooterStatus")
        self._main_tabs     = w.FindName("MainTabs")

        # --- Tab ① Bridge Configuration controls ---
        self._span_combo     = w.FindName("SpanCombo")
        self._width_combo    = w.FindName("WidthCombo")
        self._config_combo   = w.FindName("ConfigCombo")
        self._camber_box     = w.FindName("CamberBox")

        self._derived_length     = w.FindName("DerivedLength")
        self._derived_clear_span = w.FindName("DerivedClearSpan")
        self._derived_crank_len  = w.FindName("DerivedCrankLen")
        self._derived_camber     = w.FindName("DerivedCamber")

        self._config_status_card = w.FindName("ConfigStatusCard")
        self._config_status_list = w.FindName("ConfigStatusList")

        # Family section controls
        self._beam_path_box    = w.FindName("BeamPathBox")
        self._bearer_path_box  = w.FindName("BearerPathBox")
        self._joist_path_box   = w.FindName("JoistPathBox")
        self._packer_path_box  = w.FindName("PackerPathBox")

        self._beam_type_combo   = w.FindName("BeamTypeCombo")
        self._bearer_type_combo = w.FindName("BearerTypeCombo")
        self._joist_type_combo  = w.FindName("JoistTypeCombo")
        self._packer_type_combo = w.FindName("PackerTypeCombo")

        self._cb_beam    = w.FindName("CbBeam")
        self._cb_bearer  = w.FindName("CbBearer")
        self._cb_joist   = w.FindName("CbJoist")
        self._cb_packer  = w.FindName("CbPacker")

        self._family_status_list = w.FindName("FamilyStatusList")

        # --- Bind observable collections to controls ---
        self._param_list.ItemsSource          = self._param_items
        self._plane_list.ItemsSource          = self._plane_items
        self._req_list.ItemsSource            = self._req_items
        self._config_status_list.ItemsSource  = self._config_items
        self._family_status_list.ItemsSource  = self._family_items

        # --- Wire Tab ① events ---
        self._span_combo.SelectionChanged += self._on_span_changed
        self._config_combo.SelectionChanged += self._on_config_or_camber_changed
        self._camber_box.TextChanged += self._on_config_or_camber_changed

        w.FindName("BtnApplyConfig").Click  += self._on_apply_config
        w.FindName("BtnBrowseBeam").Click   += lambda s, e: self._on_browse_family("Beam",    self._beam_path_box,   self._beam_type_combo)
        w.FindName("BtnBrowseBearer").Click += lambda s, e: self._on_browse_family("Bearer",  self._bearer_path_box, self._bearer_type_combo)
        w.FindName("BtnBrowseJoist").Click  += lambda s, e: self._on_browse_family("Joist",   self._joist_path_box,  self._joist_type_combo)
        w.FindName("BtnBrowsePacker").Click += lambda s, e: self._on_browse_family("Packer",  self._packer_path_box, self._packer_type_combo)
        w.FindName("BtnLoadFamilies").Click += self._on_load_families

        # --- Wire Tab ③ / ④ events ---
        w.FindName("BtnCreateParams").Click += self._on_create_params
        w.FindName("BtnCreatePlanes").Click += self._on_create_planes
        w.FindName("BtnClose").Click        += self._on_close

    # ------------------------------------------------------------------
    # Tab ② — Configuration tab population
    # ------------------------------------------------------------------

    def _populate_config_tab(self):
        """Populate Bridge Configuration tab controls with current/default values.

        On first run: defaults to first span, first config, camber=20.
        On reopen: reads existing GPs and pre-selects the closest valid config.
        Does NOT write to the Revit document.
        """
        # --- Populate dropdowns ---
        for s in self._span_values:
            self._span_combo.Items.Add(span_display(s))

        for w_m in self._width_values:
            self._width_combo.Items.Add(width_display(w_m))

        # --- Try to read existing GP values to pre-select ---
        try:
            existing_gp = read_gp_values_mm(
                self._doc,
                ["Length", "Clear Span", "Crank Length", "Camber"],
            )
        except Exception:
            existing_gp = {}

        length_mm_gp     = existing_gp.get("Length")
        clear_span_mm_gp = existing_gp.get("Clear Span")
        crank_mm_gp      = existing_gp.get("Crank Length")
        camber_mm_gp     = existing_gp.get("Camber")

        # Handle missing GPs
        if length_mm_gp is None or clear_span_mm_gp is None or crank_mm_gp is None or camber_mm_gp is None:
            # Setup configuration dropdown as default
            self._span_combo.SelectedIndex = 0
            self._width_combo.SelectedIndex = 0
            self._rebuild_config_combo(self._span_values[0])
            self._camber_box.Text = "20"
            self._update_derived_preview()
            self._config_status_card.Visibility = System_Windows_Visibility_Visible()
            self._config_items.Clear()
            self._config_items.Add(StatusItem("Configuration", "Skipped", "Bridge Configuration has not been staged yet."))
            
        else:
            # --- Match Span ---
            matched_span_m = nearest_span(length_mm_gp) if length_mm_gp > 0.5 else None
            if matched_span_m is None:
                matched_span_m = self._span_values[0]
                span_idx = 0
            else:
                span_idx = self._span_values.index(matched_span_m)

            # Suppress the span SelectionChanged event during init by blocking updates
            self._span_combo.SelectedIndex = span_idx
            self._rebuild_config_combo(matched_span_m)   # also sets _config_objects

            # --- Match Width ---
            matched_width_m = nearest_width(clear_span_mm_gp) if clear_span_mm_gp > 0.5 else None
            if matched_width_m is None:
                self._width_combo.SelectedIndex = 0
            else:
                self._width_combo.SelectedIndex = self._width_values.index(matched_width_m)

            # --- Match Configuration ---
            if length_mm_gp > 0.5 and self._config_objects:
                cfg_match = resolve_config_from_gp(matched_span_m, crank_mm_gp)
                if cfg_match is not None and cfg_match in self._config_objects:
                    cfg_idx = self._config_objects.index(cfg_match)
                    self._config_combo.SelectedIndex = cfg_idx
                    self._current_config = cfg_match
                    self._current_is_special_22 = is_special_22(cfg_match)
                else:
                    # GPs exist but no exact match — mark as custom
                    self._config_combo.Items.Clear()
                    self._config_combo.Items.Add("Custom / not resolved")
                    self._config_combo.SelectedIndex = 0
                    self._config_objects = []
                    self._current_config = None

            # --- Match Camber ---
            if camber_mm_gp > 0.5:
                self._camber_box.Text = "{0:.0f}".format(camber_mm_gp)
            elif self._current_config is not None:
                self._camber_box.Text = "{0:.0f}".format(default_camber_mm(self._current_config))

            # --- Initialize family path boxes from defaults ---
            self._beam_path_box.Text   = FAMILY_DEFAULTS.get("Beam",    "")
            self._bearer_path_box.Text = FAMILY_DEFAULTS.get("Bearer",  "")
            self._joist_path_box.Text  = FAMILY_DEFAULTS.get("Joist",   "")
            self._packer_path_box.Text = FAMILY_DEFAULTS.get("Packer",  "")

            self._update_derived_preview()

    def _rebuild_config_combo(self, span_m):
        """Rebuild the Configuration ComboBox for the given span.

        Sets self._config_objects to the list of valid configs and selects
        the first one.
        """
        self._config_combo.Items.Clear()
        self._config_objects = get_configs_for_span(span_m)
        for cfg in self._config_objects:
            self._config_combo.Items.Add(config_display_name(cfg))

        if self._config_objects:
            self._config_combo.SelectedIndex = 0
            self._current_config = self._config_objects[0]
            self._current_is_special_22 = is_special_22(self._current_config)

            # Default camber when switching spans
            if self._camber_box is not None:
                self._camber_box.Text = "{0:.0f}".format(
                    default_camber_mm(self._current_config)
                )
        else:
            self._current_config = None
            self._current_is_special_22 = False

    def _update_derived_preview(self):
        """Compute and display derived GP values from current dropdown selections.

        Does NOT write to Revit.  Read-only preview only.
        """
        try:
            span_idx   = self._span_combo.SelectedIndex
            width_idx  = self._width_combo.SelectedIndex
            cfg_idx    = self._config_combo.SelectedIndex

            if span_idx < 0 or width_idx < 0:
                self._clear_derived_preview()
                return

            if cfg_idx < 0 or cfg_idx >= len(self._config_objects):
                self._clear_derived_preview()
                return

            span_m  = self._span_values[span_idx]
            width_m = self._width_values[width_idx]
            cfg     = self._config_objects[cfg_idx]

            length_mm_val    = span_m  * 1000.0
            clear_span_mm_val = width_m * 1000.0
            ck_mm_val         = crank_length_mm(cfg)

            try:
                camber_val = float(self._camber_box.Text)
            except (ValueError, AttributeError, TypeError):
                camber_val = None

            self._derived_length.Text     = "{0:.0f} mm".format(length_mm_val)
            self._derived_clear_span.Text = "{0:.0f} mm".format(clear_span_mm_val)
            self._derived_crank_len.Text  = "{0:.0f} mm".format(ck_mm_val)
            self._derived_camber.Text     = (
                "{0:.0f} mm".format(camber_val) if camber_val is not None else "—"
            )

        except Exception as exc:
            self._logger.warning("Error updating derived preview", exc=str(exc))
            self._clear_derived_preview()

    def _clear_derived_preview(self):
        """Reset derived parameter display to placeholder dashes."""
        for tb in [self._derived_length, self._derived_clear_span,
                   self._derived_crank_len, self._derived_camber]:
            if tb is not None:
                tb.Text = "—"

    # ------------------------------------------------------------------
    # Tab ② — Event handlers
    # ------------------------------------------------------------------

    def _on_span_changed(self, sender, args):
        """Rebuild Configuration dropdown when Span changes."""
        span_idx = self._span_combo.SelectedIndex
        if span_idx < 0 or span_idx >= len(self._span_values):
            return
        span_m = self._span_values[span_idx]
        self._rebuild_config_combo(span_m)
        self._update_derived_preview()

    def _on_config_or_camber_changed(self, sender, args):
        """Update derived preview when Config or Camber changes."""
        cfg_idx = self._config_combo.SelectedIndex
        if 0 <= cfg_idx < len(self._config_objects):
            self._current_config = self._config_objects[cfg_idx]
            self._current_is_special_22 = is_special_22(self._current_config)
        self._update_derived_preview()

    def _on_apply_config(self, sender, args):
        """Validate and stage bridge configuration to session state."""
        self._logger.info("Tab ② button clicked — staging Bridge Configuration")

        # --- Validate Span ---
        span_idx = self._span_combo.SelectedIndex
        if span_idx < 0 or span_idx >= len(self._span_values):
            self._set_footer("Error: Please select a Span.")
            return
        span_m = self._span_values[span_idx]

        # --- Validate Width ---
        width_idx = self._width_combo.SelectedIndex
        if width_idx < 0 or width_idx >= len(self._width_values):
            self._set_footer("Error: Please select a Width.")
            return
        width_m = self._width_values[width_idx]

        # --- Validate Configuration ---
        cfg_idx = self._config_combo.SelectedIndex
        if cfg_idx < 0 or cfg_idx >= len(self._config_objects):
            self._set_footer("Error: Please select a valid CRNK Configuration.")
            return
        cfg = self._config_objects[cfg_idx]

        # --- Validate config arithmetic ---
        ok, reason = validate_config(cfg, span_m)
        if not ok:
            self._set_footer("Configuration error: " + reason)
            self._logger.warning("Config validation failed", reason=reason)
            return

        # --- Validate Camber ---
        try:
            camber_mm_val = float(self._camber_box.Text.strip())
            if camber_mm_val < 0.0:
                raise ValueError("Camber must be zero or positive.")
        except ValueError as exc:
            self._set_footer("Error: Invalid Camber — " + str(exc))
            return

        # --- Compute derived values ---
        length_mm_val     = span_m  * 1000.0
        clear_span_mm_val = width_m * 1000.0
        ck_mm_val         = crank_length_mm(cfg)

        cfg_label = config_display_name(cfg)
        self._logger.info(
            "Staging bridge configuration",
            span_m=span_m,
            width_m=width_m,
            config=cfg_label,
            camber_mm=camber_mm_val,
            crank_mm=ck_mm_val,
        )

        # --- Clear previous config status ---
        self._config_items.Clear()

        # --- Store in Session State ---
        self._staged_config = {
            "Length": length_mm_val,
            "Clear Span": clear_span_mm_val,
            "Crank Length": ck_mm_val,
            "Camber": camber_mm_val
        }

        self._config_items.Add(StatusItem(
            "Configuration", "Updated",
            "Bridge Configuration staged in session. Proceed to Tab ③."
        ))
        
        self._set_footer("Configuration staged successfully. Proceed to Tab ③.")

    def _set_gp_value(self, gp_name, value_mm_val):
        """Write a value (in mm) to a named Global Parameter.

        Only valid for non-formula base parameters:
        Length, Clear Span, Crank Length, Camber.

        Args:
            gp_name       (str):   GP name.
            value_mm_val  (float): Value in millimetres.

        Returns:
            tuple: (bool ok, str detail)
        """
        try:
            gp_id = GlobalParametersManager.FindByName(self._doc, gp_name)
            if gp_id is None or element_id_value(gp_id) == -1:
                return False, (
                    "GP '{0}' not found — run Tab ③ first to create all parameters."
                ).format(gp_name)

            gp = self._doc.GetElement(gp_id)
            if gp is None:
                return False, "GP element for '{0}' is null.".format(gp_name)

            gp.SetValue(DoubleParameterValue(mm_to_internal(value_mm_val)))
            return True, "Set to {0:.1f} mm".format(value_mm_val)

        except Exception as exc:
            return False, "SetValue failed: {0}".format(str(exc))

    def _on_browse_family(self, family_name, path_box, type_combo):
        """Open a file browser to select an .rfa family file.

        Args:
            family_name (str):       Display label for the family.
            path_box    (TextBox):   The TextBox to populate with the chosen path.
            type_combo  (ComboBox):  The ComboBox to populate with type options.
        """
        try:
            from Microsoft.Win32 import OpenFileDialog
            
            dlg = OpenFileDialog()
            dlg.Title  = "Select {0} family (.rfa)".format(family_name)
            dlg.Filter = "Revit Family (*.rfa)|*.rfa|All files (*.*)|*.*"
            root = get_family_library_root()
            if os.path.isdir(root):
                dlg.InitialDirectory = root
                
            result = dlg.ShowDialog()
            # ShowDialog returns Nullable<bool>; test truthiness directly
            if result:
                path_box.Text = dlg.FileName
                
                # Discover types
                types = inspect_family_types(self._doc, dlg.FileName)
                type_combo.Items.Clear()
                if types:
                    type_combo.IsEnabled = True
                    type_combo.Items.Add("Select Type...")
                    for t_name in types:
                        type_combo.Items.Add(t_name)
                    # Select the first option or the default if only 1
                    if len(types) == 1:
                        type_combo.SelectedIndex = 1
                    else:
                        type_combo.SelectedIndex = 0
                else:
                    type_combo.IsEnabled = False
                    type_combo.Items.Add("Select family first")
                    type_combo.SelectedIndex = 0
                    
        except Exception as exc:
            self._logger.warning(
                "Browse dialog failed", family=family_name, exc=str(exc)
            )
            self._set_footer(
                "Could not open file browser: {0}".format(str(exc))
            )

    def _on_load_families(self, sender, args):
        """Load checked families into the current document."""
        self._family_items.Clear()

        # Build selection map from UI
        path_map = {
            "Beam":    self._beam_path_box.Text.strip()   if self._beam_path_box   else "",
            "Bearer":  self._bearer_path_box.Text.strip() if self._bearer_path_box else "",
            "Joist":   self._joist_path_box.Text.strip()  if self._joist_path_box  else "",
            "Packer":  self._packer_path_box.Text.strip() if self._packer_path_box else "",
        }
        cb_map = {
            "Beam":    self._cb_beam,
            "Bearer":  self._cb_bearer,
            "Joist":   self._cb_joist,
            "Packer":  self._cb_packer,
        }
        combo_map = {
            "Beam":    self._beam_type_combo,
            "Bearer":  self._bearer_type_combo,
            "Joist":   self._joist_type_combo,
            "Packer":  self._packer_type_combo,
        }

        selections = {}
        for name in FAMILY_NAMES:
            cb = cb_map.get(name)
            # IsChecked returns Nullable<bool>; cast to bool
            is_checked = (cb is not None and cb.IsChecked == True)  # noqa: E712
            if is_checked:
                combo = combo_map.get(name)
                selected_type = None
                
                if combo and combo.SelectedItem:
                    val = str(combo.SelectedItem)
                    if val not in ("Select family first", "Select Type..."):
                        selected_type = val
                        
                if not selected_type:
                    self._set_footer("Select a {0} type before loading.".format(name))
                    return
                            
                selections[name] = {
                    "path": path_map.get(name, ""),
                    "types": [selected_type]
                }

        if not selections:
            self._set_footer("No families selected. Check the boxes next to the families you want to load.")
            return

        self._set_footer("Loading families…")
        t = Transaction(self._doc, "Urbana: Load Bridge Families")
        t.Start()
        try:
            results = load_selected_families(self._doc, selections)
            t.Commit()

            for r in results:
                self._family_items.Add(StatusItem(r["name"], r["status"], r["detail"]))

            n_loaded   = sum(1 for r in results if r["status"] == STATUS_LOADED)
            n_existing = sum(1 for r in results if r["status"] == STATUS_ALREADY_LOADED)
            n_failed   = sum(1 for r in results if r["status"] in (
                STATUS_LOAD_FAILED, STATUS_NOT_FOUND, STATUS_PATH_MISSING, STATUS_ERROR
            ))
            summary = "{0} loaded, {1} already present{2}.".format(
                n_loaded, n_existing,
                ", {0} failed".format(n_failed) if n_failed else "",
            )
            self._set_footer("Families: " + summary)
            self._logger.info("Family load complete", summary=summary)

        except Exception as exc:
            t.RollBack()
            msg = "Error during family load: {0}".format(str(exc))
            self._logger.error(msg, exc=exc)
            self._family_items.Add(StatusItem("FATAL ERROR", "Error", msg))
            self._set_footer("Error — family load rolled back.")

    # ------------------------------------------------------------------
    # Tab ② — Required-GP pre-population (status unknown until run)
    # ------------------------------------------------------------------

    def _populate_tab2_prereqs(self):
        """Show the 6 required GPs with current present/missing status."""
        self._req_items.Clear()
        for name in PLANE_REQUIRED_GPS:
            gp_id = GlobalParametersManager.FindByName(self._doc, name)
            if gp_id and element_id_value(gp_id) != -1:
                item = StatusItem(name, "Existing", "Present in document")
            else:
                item = StatusItem(name, "Missing", "Run Tab ① first")
            self._req_items.Add(item)

    # ------------------------------------------------------------------
    # Tab ② — Create Global Parameters
    # ------------------------------------------------------------------

    def _on_create_params(self, sender, args):
        """Handle 'Create / Update Global Parameters' click."""
        if not hasattr(self, "_staged_config") or self._staged_config is None:
            self._set_footer("Bridge Configuration has not been completed.")
            self._param_items.Clear()
            self._param_items.Add(StatusItem("FATAL ERROR", "Error", "Configure the bridge in Tab ② before creating the controlling Global Parameters."))
            self._param_summary.Text = "Bridge Configuration missing."
            return

        self._logger.info("Tab ③ button clicked — starting Global Parameter creation")
        self._param_items.Clear()
        self._set_footer("Creating Global Parameters…")

        tg = TransactionGroup(self._doc, "Urbana: Create Global Parameters")
        tg.Start()
        try:
            t = Transaction(self._doc, "Create/Update Global Parameters")
            t.Start()
            
            # 1. Create/find required GPs & Establish formulas
            results = ensure_all(self._doc, DEFINITIONS)
            
            # 2. Write staged Bridge Configuration values
            updates = [
                ("Length",       self._staged_config["Length"]),
                ("Clear Span",   self._staged_config["Clear Span"]),
                ("Crank Length", self._staged_config["Crank Length"]),
                ("Camber",       self._staged_config["Camber"]),
            ]

            for gp_name, value_mm_val in updates:
                ok_set, detail = self._set_gp_value(gp_name, value_mm_val)
                status = "Updated" if ok_set else "Error"
                
                # Check if it was already in results
                found = False
                for r in results:
                    if r["name"] == gp_name:
                        r["status"] = status
                        r["detail"] = "{0} (from Bridge Configuration)".format(detail)
                        found = True
                        break
                if not found:
                    results.append({"name": gp_name, "status": status, "detail": "{0} (from Bridge Configuration)".format(detail)})

            # 3. Regenerate if required
            self._doc.Regenerate()
            
            t.Commit()
            tg.Assimilate()

            # 4. Verify values (logging warnings if they mismatched)
            try:
                from core.refplane_manager import read_gp_values_mm
                verify_map = read_gp_values_mm(
                    self._doc,
                    [gp_name for gp_name, _ in updates]
                )
                for gp_name, expected_mm in updates:
                    actual_mm = verify_map.get(gp_name, None)
                    if actual_mm is not None:
                        if abs(actual_mm - expected_mm) >= 1.0:
                            self._logger.warning("GP value mismatch after creation", gp_name=gp_name, expected=expected_mm, actual=actual_mm)
            except Exception as e:
                self._logger.warning("Failed to verify GP values: " + str(e))

            for r in results:
                self._param_items.Add(StatusItem(r["name"], r["status"], r.get("detail", "")))

            n_created  = sum(1 for r in results if r["status"] == "Created")
            n_existing = sum(1 for r in results if r["status"] == "Existing")
            n_errors   = sum(1 for r in results if r["status"] == "Error")
            n_conflict = sum(1 for r in results if r["status"] == "Conflict")
            n_updated  = sum(1 for r in results if r["status"] == "Updated")

            summary = (
                "{0} created, {1} existing, {2} updated/formula applied"
                "{3}{4}"
            ).format(
                n_created,
                n_existing,
                len(results) - n_created - n_existing - n_errors - n_conflict - n_updated + n_updated,
                " | {0} conflict(s)".format(n_conflict) if n_conflict else "",
                " | {0} error(s)".format(n_errors) if n_errors else "",
            )
            self._param_summary.Text = summary
            self._set_footer("Global Parameters done. " + summary)
            self._logger.info("Tab ③ complete", summary=summary)

            # Refresh Tab ④ prereq status
            self._populate_tab2_prereqs()

        except Exception as exc:
            tg.RollBack()
            msg = "Error during Global Parameter creation: {0}".format(str(exc))
            self._logger.error(msg, exc=exc)
            self._param_items.Add(StatusItem("FATAL ERROR", "Error", msg))
            self._param_summary.Text = msg
            self._set_footer("Error — see status list for details.")

    def _on_create_planes(self, sender, args):
        """Handle 'Create / Update Reference Planes' click."""
        self._logger.info("Tab \u2462 button clicked — starting Reference Plane creation")
        self._plane_items.Clear()
        self._set_footer("Validating pre-conditions…")

        # --- Pre-flight: active view ---
        view_ok, view_msg = active_view_is_supported(self._doc)
        if not view_ok:
            self._plane_items.Add(StatusItem("Active View", "Error", view_msg))
            self._plane_summary.Text = view_msg
            self._set_footer("Pre-flight failed — see status list.")
            return

        # --- Pre-flight: required GPs ---
        gps_ok, missing = required_global_parameters_present(self._doc, PLANE_REQUIRED_GPS)
        self._populate_tab2_prereqs()
        if not gps_ok:
            msg = "Missing Global Parameters: {0}. Run Tab ① first.".format(", ".join(missing))
            self._plane_items.Add(StatusItem("Required GPs", "Missing", msg))
            self._plane_summary.Text = msg
            self._set_footer("Pre-flight failed — required parameters missing.")
            return

        # --- Resolve bridge center ---
        center_xyz, origin_source = get_bridge_center(self._doc)
        self._origin_text.Text = "Bridge center from: {0}".format(origin_source)
        self._logger.info("Bridge center resolved", source=origin_source)

        # --- Read live GP values ---
        gp_names = [
            "Vertical Joist Bounding Distance", "Bearers Length",
            "Clear Span", "Abutment Width", "Length", "Crank Length",
        ]
        gp_values = read_gp_values_mm(self._doc, gp_names)

        # --- Determine topology (2/2 special or normal 3-part) ---
        # Primary: use flag set by Apply Config.
        # Fallback: infer from live Crank Length GP value.
        if self._current_config is not None:
            is_22 = self._current_is_special_22
        else:
            ck_val = gp_values.get("Crank Length", -1.0)
            is_22  = (ck_val >= 0.0 and ck_val < 1.0)  # Crank Length == 0 → 2/2
        self._logger.info(
            "Topology resolved", is_special_22=is_22,
            crank_length_mm=gp_values.get("Crank Length"),
        )

        self._set_footer("Creating Reference Planes and Dimensions…")

        t = Transaction(self._doc, "Urbana: Create Reference Skeleton")
        t.Start()
        try:
            all_results = []

            # -- Step 1: Reference Planes --
            h_results = ensure_horizontal_set(self._doc, center_xyz, gp_values)
            v_results = ensure_vertical_set(
                self._doc, center_xyz, gp_values, is_special_22=is_22
            )
            all_results.extend(h_results)
            all_results.extend(v_results)

            # Regenerate so new reference planes have valid References for dimensioning
            self._doc.Regenerate()

            # -- Step 2: Dimensions & Constraints --
            dim_results = ensure_all_dimensions(
                self._doc, center_xyz, is_special_22=is_22
            )
            all_results.extend(dim_results)

            t.Commit()

            # --- Post-creation: populate status list ---
            from System.Collections.Generic import List
            from Autodesk.Revit.DB import ElementId

            generated_ids  = List[ElementId]()
            planes_verified = 0

            # Expected plane names for counting — adjusted for topology
            if is_22:
                expected_plane_names = {
                    "H_Center",
                    "Vertical Joist Bounding Top", "Vertical Joist Bounding Bottom",
                    "Bearer Top", "Bearer Bottom",
                    "Clear Span Top", "Clear Span Bottom",
                    "Abutment Top", "Abutment Bottom",
                    "Left End", "Left Intermediate",
                    "V_Center",
                    "Right Intermediate", "Right End",
                }
            else:
                expected_plane_names = {
                    "H_Center",
                    "Vertical Joist Bounding Top", "Vertical Joist Bounding Bottom",
                    "Bearer Top", "Bearer Bottom",
                    "Clear Span Top", "Clear Span Bottom",
                    "Abutment Top", "Abutment Bottom",
                    "Left End", "Left Intermediate",
                    "Left Crank", "V_Center",
                    "Right Crank", "Right Intermediate", "Right End",
                }

            for r in all_results:
                self._plane_items.Add(StatusItem(r["name"], r["status"], r.get("detail", "")))

                elem_id = r.get("id")
                if elem_id is not None:
                    generated_ids.Add(elem_id)

                if r["name"] in expected_plane_names:
                    if r["status"] in ("Created", "Existing"):
                        planes_verified += 1

            total_expected = len(expected_plane_names)
            n_created  = sum(1 for r in all_results if r["status"] == "Created")
            n_existing = sum(1 for r in all_results if r["status"] == "Existing")
            n_errors   = sum(1 for r in all_results if r["status"] == "Error")

            topology_label = "2/2 topology" if is_22 else "normal CRNK topology"
            summary = "{0}/{1} planes verified ({2}). {3} created, {4} existing{5}. Origin: {6}".format(
                planes_verified, total_expected, topology_label,
                n_created, n_existing,
                " | {0} error(s)".format(n_errors) if n_errors else "",
                origin_source,
            )
            self._plane_summary.Text = summary
            self._set_footer("Reference skeleton done. " + summary)
            self._logger.info("Tab \u2462 complete", summary=summary)

            # Select and zoom to generated elements
            if generated_ids.Count > 0:
                self._uidoc.Selection.SetElementIds(generated_ids)
                self._uidoc.ShowElements(generated_ids)

        except Exception as exc:
            t.RollBack()
            msg = "Error during Reference Plane creation: {0}".format(str(exc))
            self._logger.error(msg, exc=exc)
            self._plane_items.Add(StatusItem("FATAL ERROR", "Error", msg))
            self._plane_summary.Text = msg
            self._set_footer("Error — transaction rolled back. No partial changes committed.")

    # ------------------------------------------------------------------
    # Footer / helpers
    # ------------------------------------------------------------------

    def _set_footer(self, text):
        if self._footer_status:
            self._footer_status.Text = text

    def _on_close(self, sender, args):
        self._window.Close()

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def show_dialog(self):
        """Show the window as a modal dialog."""
        self._window.ShowDialog()


# ---------------------------------------------------------------------------
# WPF Visibility helper — avoids importing System.Windows.Visibility
# which is not always available as a standalone name in IronPython.
# ---------------------------------------------------------------------------

def System_Windows_Visibility_Visible():
    """Return the WPF Visibility.Visible enum value."""
    from System.Windows import Visibility
    return Visibility.Visible
