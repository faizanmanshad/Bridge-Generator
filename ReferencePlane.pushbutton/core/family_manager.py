# -*- coding: utf-8 -*-
"""
family_manager.py — Family loading and detection for the Timber Deck Bridge.

Reference Plane.pushbutton / core/
Urbana Bridge Generator — Revit 2024.3 / pyRevit 6.4.0 / IronPython 2.7

Manages loading of the four primary Timber Deck Bridge Revit families:
    Beam, Bearer, Joist, Packer

FAMILY_DEFAULTS contains the centralized default path for each family.
All paths are currently empty — the user provides official Urbana paths
via the Browse button in the Bridge Configuration tab.  Update this dict
when the official paths are known; do not scatter paths elsewhere.

Python compatibility: IronPython 2.7 — no f-strings, no dataclasses.
"""

import os
import clr
clr.AddReference("RevitAPI")

from Autodesk.Revit.DB import (
    FilteredElementCollector,
    Family,
)

from core.logging_utils import get_logger
from core.revit_compat import REVIT_MAJOR


# ---------------------------------------------------------------------------
# Centralized family defaults
# ---------------------------------------------------------------------------
# Leave paths empty until official Urbana family locations are provided.
# These are the ONLY place family paths should be defined.
FAMILY_DEFAULTS = {
    "Beam":    "",
    "Bearer":  "",
    "Joist":   "",
    "Packer":  "",
}

# FAMILY_LIBRARY_ROOT: no centralized default path.
# The Browse dialog in BridgeSetupWindow uses the OS default directory.
# Do NOT add machine-specific paths here.
FAMILY_LIBRARY_ROOTS = {
    2022: r"D:\REVIT 2022\Libraries",
    2024: r"D:\REVIT 2024\Libraries",
}

def get_family_library_root():
    return FAMILY_LIBRARY_ROOTS.get(REVIT_MAJOR, "")

# Display order
FAMILY_NAMES = ["Beam", "Bearer", "Joist", "Packer"]


# ---------------------------------------------------------------------------
# Status constants (kept consistent with BridgeSetupWindow status system)
# ---------------------------------------------------------------------------
STATUS_ALREADY_LOADED = "Already Loaded"
STATUS_LOADED         = "Loaded"
STATUS_PATH_MISSING   = "Path Missing"
STATUS_NOT_FOUND      = "Not Found"
STATUS_LOAD_FAILED    = "Load Failed"
STATUS_ERROR          = "Error"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def inspect_family_types(doc, rfa_path):
    """Open the family document in the background to read available types.
    
    Args:
        doc (Document): Active Revit document.
        rfa_path (str): Path to the .rfa file.
        
    Returns:
        list: Names of available family types in the document, or empty list on error.
    """
    from Autodesk.Revit.DB import ModelPathUtils, OpenOptions, DetachFromCentralOption
    import os
    
    if not os.path.isfile(rfa_path):
        return []
        
    logger = get_logger()
    types = []
    
    # 1. Attempt to read from a Type Catalog (.txt) if it exists
    txt_path = os.path.splitext(rfa_path)[0] + ".txt"
    if os.path.isfile(txt_path):
        try:
            with open(txt_path, 'r') as f:
                lines = f.readlines()
            # The first line is headers. Data rows start at index 1.
            # E.g. "W310X38.7",...
            for line in lines[1:]:
                line = line.strip()
                if not line:
                    continue
                # The first column is the type name
                parts = line.split(',')
                if parts:
                    type_name = parts[0].strip('"').strip()
                    if type_name:
                        types.append(type_name)
            
            if types:
                return types
        except Exception as exc:
            logger.error("Failed to read type catalog", path=txt_path, exc=exc)
            # Fall back to opening the RFA if type catalog fails
            
    # 2. Fall back to opening the RFA and reading its internal FamilyManager
    opt = OpenOptions()
    opt.DetachFromCentralOption = DetachFromCentralOption.DoNotDetach
    modelPath = ModelPathUtils.ConvertUserVisiblePathToModelPath(rfa_path)
    
    try:
        famDoc = doc.Application.OpenDocumentFile(modelPath, opt)
    except Exception as exc:
        logger.error("Failed to open family doc (Possibly newer Revit version)", path=rfa_path, exc=exc)
        return []
        
    try:
        fm = famDoc.FamilyManager
        for ft in fm.Types:
            types.append(ft.Name)
    except Exception as exc:
        logger.error("Failed to read family types", path=rfa_path, exc=exc)
    finally:
        famDoc.Close(False)
        
    return types

def is_family_loaded(doc, family_name):
    """Return True if a Family with the given name is loaded in the document.

    Performs exact-name matching against all Family elements in the model.

    Args:
        doc         (Document): Active Revit document.
        family_name (str):      Exact family name, without .rfa extension.

    Returns:
        bool
    """
    try:
        for fam in FilteredElementCollector(doc).OfClass(Family).ToElements():
            if fam.Name == family_name:
                return True
    except Exception as exc:
        get_logger().warning(
            "Error scanning families",
            family=family_name,
            exc=str(exc),
        )
    return False


def load_family(doc, family_name, rfa_path):
    """Load a single Revit family from an .rfa file, with all safety checks.

    Checks:
      1. Path is not empty.
      2. File exists on disk.
      3. Family is not already loaded (to avoid duplicates).
      4. Calls doc.LoadFamily() inside the current transaction context.

    Args:
        doc         (Document): Active Revit document.
        family_name (str):      Display name used for logging and status.
        rfa_path    (str):      Absolute path to the .rfa file.

    Returns:
        dict: {name: str, status: str, detail: str}
    """
    logger = get_logger()

    # Guard: empty path
    if not rfa_path or not rfa_path.strip():
        return {
            "name":   family_name,
            "status": STATUS_PATH_MISSING,
            "detail": "No path specified. Use Browse to select the .rfa file.",
        }

    # Guard: file not found
    if not os.path.isfile(rfa_path):
        return {
            "name":   family_name,
            "status": STATUS_NOT_FOUND,
            "detail": "File not found: {0}".format(rfa_path),
        }

    # Guard: already loaded
    if is_family_loaded(doc, family_name):
        return {
            "name":   family_name,
            "status": STATUS_ALREADY_LOADED,
            "detail": "'{0}' is already loaded in the document.".format(family_name),
        }

    # Load the family
    try:
        # Revit 2024 API: Document.LoadFamily(string filename) -> bool
        # Returns True on success, False if already loaded or load failed.
        success = doc.LoadFamily(rfa_path)

        if success:
            logger.info("Family loaded", name=family_name, path=rfa_path)
            return {
                "name":   family_name,
                "status": STATUS_LOADED,
                "detail": "Loaded from: {0}".format(os.path.basename(rfa_path)),
            }
        else:
            # LoadFamily may return False when already loaded (races) — re-check
            if is_family_loaded(doc, family_name):
                return {
                    "name":   family_name,
                    "status": STATUS_ALREADY_LOADED,
                    "detail": "Detected as loaded after LoadFamily returned False.",
                }
            logger.warning("LoadFamily returned False", name=family_name, path=rfa_path)
            return {
                "name":   family_name,
                "status": STATUS_LOAD_FAILED,
                "detail": "LoadFamily returned False. Check the .rfa file is valid.",
            }

    except Exception as exc:
        msg = "Exception loading '{0}': {1}".format(family_name, str(exc))
        logger.error(msg, exc=exc)
        return {
            "name":   family_name,
            "status": STATUS_ERROR,
            "detail": msg,
        }


def is_family_symbol_loaded(doc, family_name, type_name):
    """Return True if a specific family symbol is loaded.
    
    Args:
        doc         (Document): Active Revit document.
        family_name (str):      Exact family name, without .rfa extension.
        type_name   (str):      Exact family type name.
    """
    try:
        for fam in FilteredElementCollector(doc).OfClass(Family).ToElements():
            if fam.Name == family_name:
                for symbol_id in fam.GetFamilySymbolIds():
                    symbol = doc.GetElement(symbol_id)
                    if symbol and symbol.Name == type_name:
                        return True
    except Exception:
        pass
    return False

def get_family_symbol(doc, family_name, type_name):
    """Return the actual FamilySymbol element if loaded in the document.
    
    Args:
        doc         (Document): Active Revit document.
        family_name (str):      Exact family name, without .rfa extension.
        type_name   (str):      Exact family type name.
    """
    try:
        from Autodesk.Revit.DB import FilteredElementCollector, Family
        for fam in FilteredElementCollector(doc).OfClass(Family).ToElements():
            if fam.Name == family_name:
                for symbol_id in fam.GetFamilySymbolIds():
                    symbol = doc.GetElement(symbol_id)
                    if symbol and symbol.Name == type_name:
                        return symbol
    except Exception as exc:
        get_logger().error("Error fetching family symbol", family=family_name, type=type_name, exc=exc)
    return None

def enumerate_symbol_parameters(symbol):
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
    except Exception as exc:
        get_logger().error("Error enumerating parameters", exc=exc)
    return params

def load_family_types(doc, family_name, rfa_path, selected_types):
    """Load specific types from a Revit family.

    Args:
        doc            (Document): Active Revit document.
        family_name    (str):      Display name.
        rfa_path       (str):      Absolute path to the .rfa file.
        selected_types (list):     List of type names to load.

    Returns:
        dict: {name: str, status: str, detail: str}
    """
    logger = get_logger()

    if not rfa_path or not rfa_path.strip():
        return {
            "name":   family_name,
            "status": STATUS_PATH_MISSING,
            "detail": "No path specified.",
        }

    if not os.path.isfile(rfa_path):
        return {
            "name":   family_name,
            "status": STATUS_NOT_FOUND,
            "detail": "File not found: {0}".format(rfa_path),
        }

    if not selected_types:
        return {
            "name":   family_name,
            "status": STATUS_ERROR,
            "detail": "No types selected to load.",
        }

    loaded_types = []
    already_loaded = []
    failed = []

    for type_name in selected_types:
        if is_family_symbol_loaded(doc, family_name, type_name):
            already_loaded.append(type_name)
            continue
            
        try:
            # doc.LoadFamilySymbol(string filename, string name) -> bool
            success = doc.LoadFamilySymbol(rfa_path, type_name)
            if success:
                loaded_types.append(type_name)
            else:
                if is_family_symbol_loaded(doc, family_name, type_name):
                    already_loaded.append(type_name)
                else:
                    failed.append(type_name)
        except Exception as exc:
            logger.error("Failed to load symbol (Check Revit version compatibility)", family=family_name, type=type_name, exc=exc)
            failed.append(type_name)

    detail_parts = []
    if loaded_types:
        detail_parts.append("Loaded: " + ", ".join(loaded_types))
    if already_loaded:
        detail_parts.append("Already loaded: " + ", ".join(already_loaded))
    if failed:
        detail_parts.append("Failed: " + ", ".join(failed))

    status = STATUS_LOADED if loaded_types else (STATUS_ALREADY_LOADED if already_loaded else STATUS_LOAD_FAILED)

    return {
        "name":   family_name,
        "status": status,
        "detail": " | ".join(detail_parts),
    }

def load_selected_families(doc, selections):
    """Load a selected subset of families with specific types.

    Args:
        doc        (Document): Active Revit document.
        selections (dict):     {family_name: {"path": rfa_path, "types": [type1, type2]}}

    Returns:
        list: Status dicts [{name, status, detail}, ...] in FAMILY_NAMES order.
    """
    results = []
    for name in FAMILY_NAMES:
        if name in selections:
            data = selections[name]
            rfa_path = data.get("path", "")
            types = data.get("types", [])
            result = load_family_types(doc, name, rfa_path, types)
            results.append(result)
    return results
