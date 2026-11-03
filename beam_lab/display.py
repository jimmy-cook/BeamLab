"""Native pixel rendering for Tk on Windows; no global display settings are changed."""
from __future__ import annotations

import ctypes
import sys


def enable_dpi_awareness():
    """Set awareness BEFORE Tk creates a window when running from Python source.

    The frozen executable uses windows.manifest, which Windows reads at launch.
    E_ACCESSDENIED means the manifest/host already chose the process awareness.
    """
    if sys.platform != "win32":
        return
    try:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        function = user32.SetProcessDpiAwarenessContext
        function.argtypes = [ctypes.c_void_p]
        function.restype = ctypes.c_bool
        if function(ctypes.c_void_p(-4)) or ctypes.get_last_error() == 5:
            return
    except (AttributeError, OSError):
        pass
    try:
        result = ctypes.windll.shcore.SetProcessDpiAwareness(2)
        if result in (0, -2147024891):
            return
    except (AttributeError, OSError):
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except (AttributeError, OSError):
        pass


def window_dpi(window_id, fallback=96):
    if sys.platform == "win32":
        try:
            function = ctypes.windll.user32.GetDpiForWindow
            function.argtypes = [ctypes.c_void_p]
            function.restype = ctypes.c_uint
            value = function(window_id)
            if 72 <= value <= 768:
                return value
        except (AttributeError, OSError):
            pass
    return fallback


def initial_size(screen_width, screen_height, dpi):
    """Fit the physical screen while retaining a comfortable size at 100–300%."""
    scale = dpi/96
    width = min(round(1240*scale), max(320, screen_width-round(48*scale)))
    height = min(round(860*scale), max(240, screen_height-round(100*scale)))
    return width, height
