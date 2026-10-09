"""Windows desktop integration for the GUI: one instance, taskbar identity,
window icon and an initial size that fits the screen.

Every function degrades to a no-op outside Windows or when the Windows API
is unavailable, so the GUI and the tests also run without it.
"""

from __future__ import annotations

import ctypes
import hashlib
import os
from pathlib import Path

APP_NAME = "PDF Datei Umbenenner"
MAIN_WINDOW_TITLE = "PDF Datei Umbenenner - Automatisches Umbenennen"
# Own taskbar group and icon instead of the one of pythonw.exe.
APP_USER_MODEL_ID = "PDFDateiUmbenenner.App"
ICON_FILE = "app_icon.ico"
MAXIMUM_WINDOW_SIZE = (1200, 760)
# Title bar and borders around the client area at 100 % (Windows 10/11).
WINDOW_FRAME = (16, 39)

_ERROR_ALREADY_EXISTS = 183
_MB_OK = 0x0
_MB_ICONERROR = 0x10
_MB_ICONINFORMATION = 0x40
_MB_SETFOREGROUND = 0x10000
_SW_RESTORE = 9
_SPI_GETWORKAREA = 0x0030

# Keeps the mutex handle alive for the lifetime of the process.
_instance_handle = None


def _windows() -> bool:
    return os.name == "nt"


def instance_mutex_name(project_dir: Path) -> str:
    """One name per installation: instances of other folders do not block."""

    key = str(Path(project_dir).resolve()).casefold().encode("utf-8")
    return "Local\\PDFDateiUmbenenner-" + hashlib.sha256(key).hexdigest()[:24]


def acquire_single_instance(project_dir: Path) -> bool:
    """Return False if another process of this installation already runs.

    A named mutex disappears with its process, so a crash leaves no stale
    lock. If the check itself fails, starting is allowed.
    """

    global _instance_handle
    if not _windows():
        return True
    try:
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.restype = wintypes.HANDLE
        kernel32.CreateMutexW.argtypes = (ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR)
        handle = kernel32.CreateMutexW(None, False, instance_mutex_name(project_dir))
        if not handle:
            return True
        if ctypes.get_last_error() == _ERROR_ALREADY_EXISTS:
            kernel32.CloseHandle(wintypes.HANDLE(handle))
            return False
        _instance_handle = handle
        return True
    except (AttributeError, OSError):
        return True


def show_message(title: str, text: str, *, error: bool = False) -> None:
    """Modal Windows message box; works without Tk and without a console."""

    if not _windows():
        return
    flags = _MB_OK | _MB_SETFOREGROUND | (_MB_ICONERROR if error else _MB_ICONINFORMATION)
    try:
        ctypes.windll.user32.MessageBoxW(None, text, title, flags)
    except (AttributeError, OSError):
        pass


def activate_existing_window(title: str = MAIN_WINDOW_TITLE) -> bool:
    """Restore and bring the already open main window to the front."""

    if not _windows():
        return False
    try:
        user32 = ctypes.windll.user32
        hwnd = user32.FindWindowW("TkTopLevel", title)
        if not hwnd:
            return False
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, _SW_RESTORE)
        # Allowed right after the user closed our message box.
        return bool(user32.SetForegroundWindow(hwnd))
    except (AttributeError, OSError):
        return False


def report_already_running() -> None:
    show_message(
        APP_NAME,
        "PDF Datei Umbenenner ist bereits geöffnet.\n\n"
        "Es arbeitet immer nur ein Fenster mit denselben Dateien. "
        "Das vorhandene Fenster wird jetzt angezeigt.",
    )
    activate_existing_window()


def set_app_user_model_id() -> None:
    if not _windows():
        return
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)
    except (AttributeError, OSError):
        pass


def apply_window_icon(root, project_dir: Path) -> bool:
    """Icon of the window, its dialogs and the taskbar button."""

    icon = Path(project_dir) / ICON_FILE
    if not _windows() or not icon.is_file():
        return False
    try:
        root.iconbitmap(default=str(icon))
        return True
    except Exception:  # Tk reports unusable icons with TclError.
        return False


def screen_work_area(root=None) -> tuple[int, int, int, int]:
    """Primary work area (without taskbar) in the GUI's logical pixels."""

    if _windows():
        try:
            from ctypes import wintypes

            rect = wintypes.RECT()
            if ctypes.windll.user32.SystemParametersInfoW(
                _SPI_GETWORKAREA, 0, ctypes.byref(rect), 0
            ):
                return rect.left, rect.top, rect.right, rect.bottom
        except (AttributeError, OSError):
            pass
    if root is not None:
        return 0, 0, root.winfo_screenwidth(), root.winfo_screenheight()
    return 0, 0, MAXIMUM_WINDOW_SIZE[0] + WINDOW_FRAME[0], MAXIMUM_WINDOW_SIZE[1] + WINDOW_FRAME[1]


def initial_window_geometry(
    work_area: tuple[int, int, int, int],
    maximum: tuple[int, int] = MAXIMUM_WINDOW_SIZE,
    frame: tuple[int, int] = WINDOW_FRAME,
) -> tuple[str, bool]:
    """Return a Tk geometry and whether to maximize the window.

    The full window fits: centred at its maximum size. Otherwise the
    window is maximized; the geometry is what restoring it shows.
    """

    left, top, right, bottom = work_area
    area_width, area_height = right - left, bottom - top
    max_width, max_height = maximum
    frame_width, frame_height = frame
    if area_width >= max_width + frame_width and area_height >= max_height + frame_height:
        x = left + (area_width - max_width - frame_width) // 2
        y = top + (area_height - max_height - frame_height) // 2
        return f"{max_width}x{max_height}+{x}+{y}", False
    width = max(400, min(max_width, area_width - frame_width))
    height = max(300, min(max_height, area_height - frame_height))
    return f"{width}x{height}+{left}+{top}", True


def is_compact(work_area: tuple[int, int, int, int]) -> bool:
    """Small screens (e.g. 1366x768) leave no room for decorative spacing."""

    return initial_window_geometry(work_area)[1]
