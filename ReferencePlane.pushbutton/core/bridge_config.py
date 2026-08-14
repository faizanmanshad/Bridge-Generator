# -*- coding: utf-8 -*-
"""
bridge_config.py — CRNK configuration database and derived-value calculations.

Reference Plane.pushbutton / core/
Urbana Bridge Generator — Revit 2024.3 / pyRevit 6.4.0 / IronPython 2.7

SINGLE SOURCE OF TRUTH for all supported CRNK bridge configurations.
No other file should contain this data.

Key concepts:
  - segments list with 2 elements = special CRNK-2/2 (no centre straight).
  - segments list with 3 elements = normal A/B/C.
  - Crank Length = centre_segment / 2    (3-part)
  - Crank Length = 0                      (2/2 special)

Python compatibility: IronPython 2.7 — no f-strings, no dataclasses.
"""

# ---------------------------------------------------------------------------
# CRNK configuration table
# ---------------------------------------------------------------------------
# Each span key (float metres) maps to a list of config dicts.
# Each config dict has:
#   "segments"          list of floats (metres) — 2 or 3 elements
#   "default_camber_mm" float (mm) — engineering default; absent where not specified

CRNK_CONFIGS = {
    4.0:  [
        {"segments": [2.0, 2.0],        "default_camber_mm": 20.0},
    ],
    6.0:  [
        {"segments": [2.0, 2.0, 2.0],   "default_camber_mm": 20.0},
    ],
    8.0:  [
        {"segments": [2.0, 4.0, 2.0],   "default_camber_mm": 20.0},
    ],
    10.0: [
        {"segments": [2.0, 6.0, 2.0],   "default_camber_mm": 30.0},
    ],
    12.0: [
        {"segments": [2.0, 8.0, 2.0],   "default_camber_mm": 40.0},
    ],
    14.0: [
        {"segments": [2.0, 10.0, 2.0]},
        {"segments": [4.0, 6.0, 4.0]},
    ],
    16.0: [
        {"segments": [4.0, 8.0, 4.0]},
    ],
    18.0: [
        {"segments": [4.0, 10.0, 4.0]},
        {"segments": [6.0, 6.0, 6.0]},
    ],
    20.0: [
        {"segments": [4.0, 12.0, 4.0]},
        {"segments": [6.0, 8.0, 6.0]},
    ],
    22.0: [
        {"segments": [6.0, 10.0, 6.0]},
    ],
    24.0: [
        {"segments": [6.0, 12.0, 6.0]},
    ],
}

# Supported span values in ascending order (metres)
SUPPORTED_SPANS_M = sorted(CRNK_CONFIGS.keys())

# Supported bridge widths (metres) — directly maps to Clear Span GP (width * 1000 mm)
SUPPORTED_WIDTHS_M = [1.5, 2.0, 2.5, 3.0]

# Fallback camber when a config row has no explicit default
DEFAULT_CAMBER_MM = 20.0

# Segment sum/symmetry validation tolerance (metres)
_TOL_M = 0.001


# ---------------------------------------------------------------------------
# Config identity
# ---------------------------------------------------------------------------

def is_special_22(cfg):
    """Return True if this config is the special 2/2 (no centre straight segment)."""
    return len(cfg["segments"]) == 2


def config_display_name(cfg):
    """Return the user-facing label for a config.

    Examples:
        [2.0, 2.0]       -> "CRNK - 2/2"
        [2.0, 6.0, 2.0]  -> "CRNK - 2/6/2"
        [4.0, 12.0, 4.0] -> "CRNK - 4/12/4"
    """
    segs = cfg["segments"]
    parts = []
    for s in segs:
        # Display as integer where possible (2.0 -> "2", 12.0 -> "12")
        if s == int(s):
            parts.append(str(int(s)))
        else:
            parts.append("{0:.1f}".format(s))
    return "CRNK - " + "/".join(parts)


# ---------------------------------------------------------------------------
# Derived values
# ---------------------------------------------------------------------------

def crank_length_mm(cfg):
    """Return Crank Length in mm derived from the selected configuration.

    For a 3-part A/B/C config: Crank Length = (B_metres / 2) * 1000
    For the special 2/2 config: Crank Length = 0.0

    This is what is written to the 'Crank Length' Global Parameter.
    """
    if is_special_22(cfg):
        return 0.0
    centre_m = cfg["segments"][1]
    return (centre_m / 2.0) * 1000.0


def default_camber_mm(cfg):
    """Return the recommended engineering default camber for this config (mm).

    Falls back to DEFAULT_CAMBER_MM (20) when not explicitly specified.
    """
    return float(cfg.get("default_camber_mm", DEFAULT_CAMBER_MM))


# ---------------------------------------------------------------------------
# Lookup and filtering
# ---------------------------------------------------------------------------

def get_configs_for_span(span_m):
    """Return list of config dicts valid for the given span (metres).

    Returns empty list if span is not in the table.
    """
    return list(CRNK_CONFIGS.get(float(span_m), []))


def span_display(span_m):
    """Format a span value for UI display.  e.g. 10.0 -> '10.0 m'"""
    return "{0:.1f} m".format(float(span_m))


def width_display(width_m):
    """Format a width value for UI display.  e.g. 2.5 -> '2.5 m'"""
    return "{0:.1f} m".format(float(width_m))


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate_config(cfg, span_m):
    """Validate a configuration dict against a span value.

    Checks performed:
      1. sum(segments) == span_m  ±  tolerance
      2. 3-part configs must be symmetric: A == C  ±  tolerance

    Args:
        cfg    (dict):  Configuration dict with "segments" key.
        span_m (float): Bridge span in metres.

    Returns:
        tuple: (bool is_valid, str reason)
    """
    segs  = cfg["segments"]
    total = sum(segs)

    if abs(total - float(span_m)) > _TOL_M:
        return False, (
            "Segment sum {0:.3f} m does not match span {1:.1f} m"
        ).format(total, span_m)

    if len(segs) == 3 and abs(segs[0] - segs[2]) > _TOL_M:
        return False, (
            "Configuration is not symmetric: A={0} m, C={1} m"
        ).format(segs[0], segs[2])

    return True, "Valid"


# ---------------------------------------------------------------------------
# Config resolution from live GP values (used on window reopen)
# ---------------------------------------------------------------------------

def resolve_config_from_gp(span_m, crank_length_mm_value):
    """Find the matching CRNK config from existing Global Parameter values.

    Used when the Bridge Setup window is reopened to pre-populate controls
    without overwriting the user's existing bridge configuration.

    Args:
        span_m                 (float): Current Length GP value / 1000.0
        crank_length_mm_value  (float): Current Crank Length GP value in mm.

    Returns:
        dict: Matching config dict, or None if no match is found.
    """
    configs = get_configs_for_span(span_m)
    if not configs:
        return None

    for cfg in configs:
        expected_mm = crank_length_mm(cfg)
        if abs(expected_mm - crank_length_mm_value) < 1.0:   # 1 mm tolerance
            return cfg

    return None


def nearest_span(length_mm):
    """Return the nearest supported span (metres) for a given Length value (mm).

    Returns None if no supported span is within 1 mm of the given value.
    """
    target_m = length_mm / 1000.0
    for s in SUPPORTED_SPANS_M:
        if abs(s - target_m) < 0.001:
            return s
    return None


def nearest_width(clear_span_mm):
    """Return the nearest supported width (metres) for a given Clear Span (mm).

    Returns None if no supported width is within 1 mm of the given value.
    """
    target_m = clear_span_mm / 1000.0
    for w in SUPPORTED_WIDTHS_M:
        if abs(w - target_m) < 0.001:
            return w
    return None
