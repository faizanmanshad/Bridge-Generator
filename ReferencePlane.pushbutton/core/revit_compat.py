# -*- coding: utf-8 -*-

"""Compatibility helpers for Urbana Bridge Generator.

One extension is shared between supported Revit versions.
Only Revit API differences belong in this module.
"""

from pyrevit import HOST_APP

SUPPORTED_REVIT_MAJORS = (2022, 2024)

def get_revit_major():
    """Return the active Revit major version as an integer."""
    try:
        # Autodesk Application.VersionNumber is normally the major
        # release number, e.g. "2022" or "2024".
        return int(HOST_APP.app.VersionNumber)
    except Exception as ex:
        raise RuntimeError(
            "Unable to determine Autodesk Revit version: {0}".format(ex)
        )

def get_revit_version_name():
    """Return useful version information for diagnostics."""
    app = HOST_APP.app

    try:
        name = app.VersionName
    except Exception:
        name = "Autodesk Revit"

    try:
        number = app.VersionNumber
    except Exception:
        number = str(get_revit_major())

    try:
        build = app.VersionBuild
    except Exception:
        build = "Unknown build"

    return "{0} | {1} | {2}".format(name, number, build)

REVIT_MAJOR = get_revit_major()

def is_revit_2022():
    return REVIT_MAJOR == 2022

def is_revit_2024():
    return REVIT_MAJOR == 2024

def ensure_supported_revit():
    """Fail early with a useful message for unvalidated versions."""
    if REVIT_MAJOR not in SUPPORTED_REVIT_MAJORS:
        raise RuntimeError(
            "Urbana Bridge Generator currently supports "
            "Autodesk Revit 2022 and Autodesk Revit 2024.x. "
            "Detected Revit {0}.".format(REVIT_MAJOR)
        )

def supports_revit(minimum_version):
    return REVIT_MAJOR >= int(minimum_version)

def element_id_value(element_id):
    """Return a Python integer from an Autodesk ElementId."""
    if element_id is None:
        return None

    if REVIT_MAJOR >= 2024:
        # Revit 2024 introduced the full Int64 Value property.
        if hasattr(element_id, "Value"):
            return int(element_id.Value)

    # Revit 2022 path.
    return int(element_id.IntegerValue)

# Note: family library root paths are machine-specific installation details,
# not Revit API version differences.  They do not belong here.
# The Browse dialog in family_manager.py falls back to the OS default
# when no explicit directory is configured.
