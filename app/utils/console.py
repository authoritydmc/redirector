"""Console encoding safety.

Windows terminals still default to a legacy code page (cp1252 in most cases),
where the emoji and box-drawing characters used throughout the startup banner
and log messages raise ``UnicodeEncodeError``. Because that happens while
*writing* to stdout, it kills the process during import of the config module -
before the app ever binds a port, and with a traceback that says nothing about
the real cause.

Reconfiguring the streams to UTF-8 with ``errors='replace'`` turns a fatal
crash into a few substituted glyphs. Streams that do not support
``reconfigure`` (notebook capture, some CI log wrappers) are left alone.
"""

from __future__ import annotations

import sys

_APPLIED = False


def force_utf8_streams() -> None:
    """Idempotently switch stdout/stderr to UTF-8 with lossy error handling."""
    global _APPLIED
    if _APPLIED:
        return
    _APPLIED = True
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError, AttributeError, LookupError):
            # Already-detached or non-encodable stream; nothing to do.
            pass


force_utf8_streams()
