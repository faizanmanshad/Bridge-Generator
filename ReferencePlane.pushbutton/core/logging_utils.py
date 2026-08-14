# -*- coding: utf-8 -*-
"""
logging_utils.py — Structured logging for Reference Plane.pushbutton.

Reference Plane.pushbutton / core/
Urbana Bridge Generator — Revit 2024.3 / pyRevit 6.4.0 / IronPython 2.7

Writes JSON-lines to:
    %LOCALAPPDATA%\\BridgeGenerator\\logs\\ReferencePlane_YYYY-MM-DD.log

Mirrors the BridgeLogger pattern from Generate Bridge.pushbutton/lib/logging_utils.py
but scoped to this tool.

Python compatibility: IronPython 2.7 — no f-strings, no dataclasses.
"""

import os
import sys
import io
import json
import datetime
import traceback as tb_module


# ---------------------------------------------------------------------------
# Log directory
# ---------------------------------------------------------------------------

def _get_log_dir():
    """Return (and create if needed) the log directory."""
    local_app = os.environ.get("LOCALAPPDATA", os.path.expanduser("~"))
    log_dir   = os.path.join(local_app, "BridgeGenerator", "logs")
    if not os.path.isdir(log_dir):
        try:
            os.makedirs(log_dir)
        except OSError:
            log_dir = os.environ.get("TEMP", os.path.expanduser("~"))
    return log_dir


def _today_log_path():
    today = datetime.datetime.now().strftime("%Y-%m-%d")
    return os.path.join(_get_log_dir(), "ReferencePlane_{0}.log".format(today))


# ---------------------------------------------------------------------------
# Logger class
# ---------------------------------------------------------------------------

class RefPlaneLogger(object):
    """Structured JSON-lines logger for Reference Plane.pushbutton.

    Usage:
        logger = get_logger()
        logger.info("Parameter created", name="Clear Span", status="Created")
        logger.error("Unexpected error", exc=exc)
    """

    def __init__(self, log_path, run_id=None):
        self._log_path = log_path
        self._run_id   = run_id or "none"
        self._context  = {}

    def _write(self, level, message, **kwargs):
        entry = {
            "timestamp": datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
            "level":     level,
            "run_id":    kwargs.pop("run_id", self._run_id),
            "message":   message,
            "tool":      "ReferencePlane.pushbutton",
        }
        for k, v in self._context.items():
            entry.setdefault(k, v)
        entry.update(kwargs)

        line = json.dumps(entry, ensure_ascii=False, default=str) + "\n"
        try:
            with io.open(self._log_path, "a", encoding="utf-8") as fh:
                fh.write(line)
        except (IOError, OSError):
            sys.stderr.write("[ReferencePlane] LOG WRITE FAILED: " + line)

    def debug(self, message, **kwargs):
        self._write("DEBUG", message, **kwargs)

    def info(self, message, **kwargs):
        self._write("INFO", message, **kwargs)

    def warning(self, message, **kwargs):
        self._write("WARNING", message, **kwargs)

    def error(self, message, exc=None, **kwargs):
        """Log an error. If exc is provided, captures traceback automatically."""
        if exc is not None:
            kwargs.setdefault("exception", str(exc))
            kwargs.setdefault("traceback", tb_module.format_exc())
        self._write("ERROR", message, **kwargs)

    def set_context(self, **kwargs):
        """Attach persistent fields to every future log entry."""
        self._context.update(kwargs)

    @property
    def log_path(self):
        return self._log_path


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_instance = None


def get_logger():
    """Return (or create) the module-level singleton logger."""
    global _instance
    if _instance is None:
        _instance = RefPlaneLogger(log_path=_today_log_path())
    return _instance


def new_logger(run_id=None):
    """Create a fresh logger bound to run_id and update the singleton."""
    global _instance
    _instance = RefPlaneLogger(log_path=_today_log_path(), run_id=run_id)
    return _instance
