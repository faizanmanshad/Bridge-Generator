# -*- coding: utf-8 -*-
"""
script.py — pyRevit entry point for Reference Plane.pushbutton.

Reference Plane.pushbutton /
Urbana Bridge Generator — Revit 2024.3 / pyRevit 6.4.0 / IronPython 2.7

Execution sequence:
    1. Guard: require an open project (not family) document.
    2. Initialise the logger with a run ID.
    3. Load and show the two-tab WPF window (BridgeSetupWindow).
    4. All Revit document logic executes inside the window's event handlers
       via core/ managers — this file stays thin.

Python engine: IronPython 2.7 (pyRevit default for Revit 2024).
               No f-strings, no dataclasses, no Python 3-only syntax.
"""

import os
import sys
import datetime
import traceback as tb_module

# ---------------------------------------------------------------------------
# Bundle directory — all core/ and ui/ imports resolve relative to this
# ---------------------------------------------------------------------------
BUNDLE_DIR = os.path.dirname(os.path.abspath(__file__))

if BUNDLE_DIR not in sys.path:
    sys.path.insert(0, BUNDLE_DIR)

# ---------------------------------------------------------------------------
# Revit / pyRevit globals (injected by pyRevit runtime)
# ---------------------------------------------------------------------------
import clr
clr.AddReference("RevitAPI")
clr.AddReference("RevitAPIUI")

from Autodesk.Revit.UI import TaskDialog

from pyrevit import script as pyscript

doc   = __revit__.ActiveUIDocument.Document if __revit__.ActiveUIDocument else None
uidoc = __revit__.ActiveUIDocument
uiapp = __revit__

# ---------------------------------------------------------------------------
# Guard: require an open project document
# ---------------------------------------------------------------------------
if doc is None or doc.IsFamilyDocument:
    TaskDialog.Show(
        "Urbana Bridge Setup",
        "Please open a project (not a family) document before running this tool.",
    )
    raise SystemExit("No project document open.")

# ---------------------------------------------------------------------------
# Guard: ensure supported Revit version
# ---------------------------------------------------------------------------
try:
    from core.revit_compat import ensure_supported_revit, get_revit_version_name
    ensure_supported_revit()
except Exception as ex:
    TaskDialog.Show("Urbana Bridge Setup — Unsupported Version", str(ex))
    raise SystemExit(str(ex))

# ---------------------------------------------------------------------------
# Main execution
# ---------------------------------------------------------------------------
try:
    from core.logging_utils import new_logger

    run_id = "RP-{0}".format(datetime.datetime.now().strftime("%Y%m%d-%H%M%S"))
    logger = new_logger(run_id=run_id)
    logger.set_context(document_name=doc.Title)
    
    revit_env_name = get_revit_version_name()
    logger.info(
        "Reference Plane.pushbutton started",
        run_id=run_id,
        document=doc.Title,
        revit_env=revit_env_name,
    )
    
    logger.info("Urbana Bridge Generator runtime: {0}".format(revit_env_name))

    from ui.BridgeSetupWindow import BridgeSetupWindow

    window = BridgeSetupWindow(uidoc, BUNDLE_DIR)
    window.show_dialog()

    logger.info("Reference Plane.pushbutton session ended", run_id=run_id)

except SystemExit:
    # Clean guard exits — not an error
    pass

except Exception as exc:
    raw_tb = tb_module.format_exc()

    # Attempt to log
    try:
        from core.logging_utils import get_logger
        get_logger().error(
            "Unhandled exception in script.py",
            exc=exc,
        )
    except Exception:
        pass

    TaskDialog.Show(
        "Urbana Bridge Setup — Unexpected Error",
        "An unexpected error occurred.\n\n"
        "Error: {0}\n\n"
        "See the pyRevit output panel for the full traceback.".format(str(exc)),
    )

    output = pyscript.get_output()
    output.print_md(
        "## Urbana Bridge Setup — Unhandled Exception\n```\n{0}\n```".format(raw_tb)
    )
    raise
