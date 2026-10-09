"""Small, launcher-friendly entry point for the GUI application.

The launchers start it with pythonw.exe as a separate process: without a
console, output goes to .launcher-logs\\gui.log and start failures appear
in a message box.
"""

import os
import sys
import traceback
from pathlib import Path

current_dir = Path(__file__).parent
sys.path.insert(0, str(current_dir))

GUI_LOG = current_dir / ".launcher-logs" / "gui.log"


def _redirect_output_to_log():
    try:
        GUI_LOG.parent.mkdir(exist_ok=True)
        stream = open(GUI_LOG, "w", encoding="utf-8", errors="backslashreplace", buffering=1)
    except OSError:
        stream = open(os.devnull, "w", encoding="utf-8")
    sys.stdout = sys.stderr = stream


def run():
    """Import lazily so launcher health-check failures remain diagnosable."""

    from desktop_integration import (
        APP_NAME,
        acquire_single_instance,
        report_already_running,
        show_message,
    )

    # A second double click must not open another window on the same files.
    if not acquire_single_instance(current_dir):
        report_already_running()
        return 0
    if Path(sys.executable).stem.casefold() == "pythonw":
        _redirect_output_to_log()
    try:
        from gui_main import main
    except Exception as error:
        traceback.print_exc()
        show_message(
            APP_NAME,
            f"Die Anwendung konnte nicht gestartet werden:\n{error}\n\nDetails: {GUI_LOG}",
            error=True,
        )
        return 1
    return main()


if __name__ == "__main__":
    sys.exit(run())
