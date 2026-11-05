"""Shortcuts that start Maja like a normal app: on the desktop, in the Start menu, and
(optionally) in the Startup folder, so she opens when Matty logs in and reminders work
all day.

The shortcuts run pythonw.exe, which starts Python without a console window, and use
Maja's own icon (maja.ico).
"""

import os
import subprocess
import sys
from pathlib import Path

SUPPORTED = sys.platform == "win32"
MAIN = Path(__file__).with_name("main.py")
ICON = Path(__file__).with_name("maja.ico")
PROGRAMS = Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs"


def shortcut_path():
    """The Startup-folder shortcut."""
    return PROGRAMS / "Startup" / "Maja.lnk"


def is_enabled():
    return SUPPORTED and shortcut_path().exists()


# Paths are passed through environment variables, so nothing in them can run as a command.
# The desktop folder is asked from Windows, because it may be moved into OneDrive.
_CREATE_SHORTCUT = r"""
$shell = New-Object -ComObject WScript.Shell
$link = $env:MAJA_LINK
if ($link -eq 'DESKTOP') { $link = Join-Path $shell.SpecialFolders('Desktop') 'Maja.lnk' }
$shortcut = $shell.CreateShortcut($link)
$shortcut.TargetPath = $env:MAJA_PYTHONW
$shortcut.Arguments = '"' + $env:MAJA_MAIN + '"' + $env:MAJA_ARGS
$shortcut.WorkingDirectory = $env:MAJA_FOLDER
$shortcut.IconLocation = $env:MAJA_ICON
$shortcut.Description = "Maja, my personal assistant"
$shortcut.Save()
Write-Output $link
"""


def _create(link, arguments=""):
    """Create one shortcut. Returns its path, or None if it didn't work."""
    if not SUPPORTED:
        return None
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    if not pythonw.exists():
        pythonw = Path(sys.executable)
    if link != "DESKTOP":
        Path(link).parent.mkdir(parents=True, exist_ok=True)
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", _CREATE_SHORTCUT],
            env={**os.environ, "MAJA_LINK": str(link), "MAJA_PYTHONW": str(pythonw),
                 "MAJA_MAIN": str(MAIN), "MAJA_ARGS": arguments, "MAJA_FOLDER": str(MAIN.parent),
                 "MAJA_ICON": str(ICON)},
            capture_output=True, text=True, timeout=30, creationflags=subprocess.CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    created = Path(result.stdout.strip()) if result.stdout.strip() else None
    return created if created and created.exists() else None


def enable():
    """Create the Startup shortcut (Maja opens minimized at login). Returns True if it worked."""
    return _create(shortcut_path(), " --minimized") is not None


def disable():
    """Remove the Startup shortcut (only Maja's own)."""
    if SUPPORTED:
        shortcut_path().unlink(missing_ok=True)


def install_app():
    """Put Maja on the desktop and in the Start menu. Returns the shortcuts that were made."""
    made = [_create("DESKTOP"), _create(PROGRAMS / "Maja.lnk")]
    if is_enabled():
        enable()  # Give the existing Startup shortcut the new icon too.
    return [path for path in made if path is not None]


if __name__ == "__main__":
    # py autostart.py  ->  desktop and Start menu shortcuts
    shortcuts = install_app()
    print("\n".join(f"Created {path}" for path in shortcuts) or "Couldn't create the shortcuts.")
