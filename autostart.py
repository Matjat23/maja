"""Start Maja automatically when Matty logs in to Windows, so reminders work all day.

This adds a "Maja" shortcut to the Windows Startup folder. It starts Maja minimized
with pythonw.exe, which runs Python without a console window.
"""

import os
import subprocess
import sys
from pathlib import Path

SUPPORTED = sys.platform == "win32"
MAIN = Path(__file__).with_name("main.py")


def shortcut_path():
    return Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup" / "Maja.lnk"


def is_enabled():
    return SUPPORTED and shortcut_path().exists()


# Paths are passed through environment variables, so nothing in them can run as a command.
_CREATE_SHORTCUT = r"""
$shortcut = (New-Object -ComObject WScript.Shell).CreateShortcut($env:MAJA_LINK)
$shortcut.TargetPath = $env:MAJA_PYTHONW
$shortcut.Arguments = '"' + $env:MAJA_MAIN + '" --minimized'
$shortcut.WorkingDirectory = $env:MAJA_FOLDER
$shortcut.Description = "Maja, Matty's personal assistant"
$shortcut.Save()
"""


def enable():
    """Create the Startup shortcut. Returns True if it worked."""
    if not SUPPORTED:
        return False
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    if not pythonw.exists():
        pythonw = Path(sys.executable)
    shortcut_path().parent.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", _CREATE_SHORTCUT],
            env={**os.environ, "MAJA_LINK": str(shortcut_path()), "MAJA_PYTHONW": str(pythonw),
                 "MAJA_MAIN": str(MAIN), "MAJA_FOLDER": str(MAIN.parent)},
            capture_output=True, timeout=30, creationflags=subprocess.CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return is_enabled()


def disable():
    """Remove the Startup shortcut (only Maja's own)."""
    if SUPPORTED:
        shortcut_path().unlink(missing_ok=True)
