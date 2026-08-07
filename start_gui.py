"""Small, launcher-friendly entry point for the GUI application."""

import sys
from pathlib import Path

current_dir = Path(__file__).parent
sys.path.insert(0, str(current_dir))


def run():
    """Import lazily so launcher health-check failures remain diagnosable."""

    from gui_main import main

    return main()


if __name__ == "__main__":
    run()
