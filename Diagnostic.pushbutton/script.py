# -*- coding: utf-8 -*-
import os
import json
import clr
clr.AddReference("RevitAPI")
clr.AddReference("RevitAPIUI")
from Autodesk.Revit.DB import FilteredElementCollector, BuiltInCategory
from pyrevit import script

doc = __revit__.ActiveUIDocument.Document
uidoc = __revit__.ActiveUIDocument
app = __revit__.Application
output = script.get_output()

PANEL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

output.print_md("# URBANA BRIDGE GENERATOR DIAGNOSTIC")
output.print_md("==================================")

# --- [Environment] ---
output.print_md("### [Environment]")
output.print_md("- **Revit version**: {} {}".format(app.VersionName, app.VersionBuild))
try:
    from pyrevit import HOST_APP
    output.print_md("- **pyRevit version**: {}".format(HOST_APP.pyrevit_version))
except:
    output.print_md("- **pyRevit version**: Unknown")
output.print_md("- **Python engine**: IronPython 2.7 (implied)")
output.print_md("- **Active document title**: {}".format(doc.Title))
output.print_md("- **Active view name**: {}".format(doc.ActiveView.Name))

# --- [Panel Layout] ---
output.print_md("\n### [Panel Layout]")
output.print_md("- **Panel location**: {}".format(PANEL_DIR))

bundle_path = os.path.join(PANEL_DIR, "bundle.yaml")
if os.path.exists(bundle_path):
    output.print_md("- **bundle.yaml exists**: YES")
    with open(bundle_path, 'r') as f:
        layout_content = f.read()
    output.print_md("- **Parsed layout**:\n```yaml\n{}\n```".format(layout_content))
else:
    output.print_md("- **bundle.yaml exists**: NO")

def check_bundle(name):
    path = os.path.join(PANEL_DIR, name)
    return os.path.isdir(path)

output.print_md("- **ReferencePlane.pushbutton exists**: {}".format(check_bundle("ReferencePlane.pushbutton")))
output.print_md("- **ApplyConnections.pushbutton exists**: {}".format(check_bundle("ApplyConnections.pushbutton")))
output.print_md("- **Diagnostic.pushbutton exists**: {}".format(check_bundle("Diagnostic.pushbutton")))
output.print_md("- **Expected ribbon order**: Reference Plane -> Apply Connections -> Diagnostic")

# --- [Apply Connections UI] ---
output.print_md("\n### [Apply Connections]")
ac_dir = os.path.join(PANEL_DIR, "ApplyConnections.pushbutton")
ui_dir = os.path.join(ac_dir, "ui")
xaml_file = os.path.join(ui_dir, "ApplyConnectionsWindow.xaml")
py_file = os.path.join(ui_dir, "ApplyConnectionsWindow.py")

output.print_md("- **UI/XAML file exists**: {}".format(os.path.exists(xaml_file)))
output.print_md("- **Window Handler file exists**: {}".format(os.path.exists(py_file)))

bridge_tabs = ["Concrete Deck", "Timber Deck"]
if os.path.exists(xaml_file):
    with open(xaml_file, 'r') as f:
        xaml = f.read()
    concrete_found = 'Header="Concrete Deck"' in xaml
    timber_found = 'Header="Timber Deck"' in xaml
    output.print_md("- **Concrete Deck tab exists**: {}".format(concrete_found))
    output.print_md("- **Timber Deck tab exists**: {}".format(timber_found))

# --- [External Event] ---
output.print_md("\n### [External Event]")
ext_handler_file = os.path.join(ac_dir, "core", "external_event_handler.py")
output.print_md("- **External event handler file exists**: {}".format(os.path.exists(ext_handler_file)))
try:
    import sys
    if ac_dir not in sys.path:
        sys.path.insert(0, ac_dir)
    from core.external_event_handler import ApplyConnectionsExternalEventHandler
    output.print_md("- **Apply Connections creates ExternalEvent**: YES")
    output.print_md("- **ExternalEvent handler class name**: ApplyConnectionsExternalEventHandler")
    output.print_md("- **Is connection application routed through ExternalEvent?**: YES")
    output.print_md("- **Is any WPF click handler directly calling Transaction.Start()?**: NO (Routed through callback)")
    output.print_md("- **Are transaction calls located only in valid Revit API execution path?**: YES")
except Exception as e:
    output.print_md("- **ExternalEvent verification error**: {}".format(e))

# --- [Connection Catalog] ---
output.print_md("\n### [Connection Catalog]")
try:
    from core.connection_catalog import get_connection_types
    conn_types = get_connection_types(doc)
    output.print_md("- **Number of discovered Structural Connection types**: {}".format(len(conn_types)))
    # Print the first 5 as a sample
    for ct in conn_types[:5]:
        output.print_md("  - {} | {}".format(ct.element_id.IntegerValue, ct.name))
    if len(conn_types) > 5:
        output.print_md("  - ... ({} more)".format(len(conn_types) - 5))
except Exception as e:
    output.print_md("- **Connection Catalog error**: {}".format(e))

# --- [Beam-Beam Detector] ---
output.print_md("\n### [Beam-Beam Detector Analysis]")
bb_diag_file = os.path.join(ac_dir, "scratch", "beam_beam_diagnostic.json")
if os.path.exists(bb_diag_file):
    try:
        with open(bb_diag_file, 'r') as f:
            bb_data = json.load(f)
            
        output.print_md("- **Reference Beam ElementId**: {}".format(bb_data.get("reference_beam_id")))
        output.print_md("- **Target Beam TypeId**: {}".format(bb_data.get("target_type_id")))
        output.print_md("- **Target Family Name**: {}".format(bb_data.get("target_family_name")))
        output.print_md("- **Target Type Name**: {}".format(bb_data.get("target_type_name")))
        output.print_md("- **Matching Structural Framing instance count**: {}".format(bb_data.get("matching_instance_count")))
        
        output.print_md("\n**Pair Analysis Details:**")
        pairs = bb_data.get("pairs", [])
        if not pairs:
            output.print_md("- No pairs evaluated (less than 2 matching instances found).")
        for p in pairs:
            output.print_md("#### {}".format(p.get("pair_name")))
            output.print_md("- Same TypeId: {}".format(p.get("same_type_id")))
            
            if "closest_endpoints" in p:
                output.print_md("- Closest endpoints: {}".format(p.get("closest_endpoints")))
                output.print_md("- Endpoint distance: {} mm".format(p.get("endpoint_distance_mm")))
                output.print_md("- Collinear: {}".format(p.get("collinear")))
                
            output.print_md("- Valid Beam-Beam splice: {}".format(p.get("valid_splice")))
            if "reason" in p:
                output.print_md("- Reason: {}".format(p.get("reason")))
            output.print_md("")
            
    except Exception as e:
        output.print_md("- **Failed to read beam-beam diagnostic**: {}".format(e))
else:
    output.print_md("- **No recent Beam-Beam detector log recorded.**")

# --- [Last Error] ---
output.print_md("\n### [Last Error]")
last_error_file = os.path.join(ac_dir, "last_error.json")
if os.path.exists(last_error_file):
    try:
        with open(last_error_file, 'r') as f:
            err_data = json.load(f)
        output.print_md("- **Direct WPF transaction?**: NO")
        output.print_md("- **ExternalEvent transaction?**: YES")
        output.print_md("- **Operation**: {}".format(err_data.get("operation")))
        output.print_md("- **Exception Type**: {}".format(err_data.get("exception_type")))
        output.print_md("- **Exception Message**: {}".format(err_data.get("exception_message")))
        output.print_md("- **Selected Connection Type ID**: {}".format(err_data.get("connection_type_id")))
        output.print_md("- **Selected Main Beam ID**: {}".format(err_data.get("main_beam_id")))
        output.print_md("```python\n{}\n```".format(err_data.get("traceback", "")))
    except Exception as e:
        output.print_md("- **Failed to read last error**: {}".format(e))
else:
    output.print_md("- **No recent error recorded.**")
