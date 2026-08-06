"""
Einfacher Starter für die GUI-Version des PDF Umbeneners
"""

import sys
from pathlib import Path

# Füge das aktuelle Verzeichnis zum Python-Pfad hinzu
current_dir = Path(__file__).parent
sys.path.insert(0, str(current_dir))

try:
    from gui_main import main
    main()
except Exception as e:
    print(f"Fehler beim Starten der GUI: {e}")
    input("Drücken Sie Enter zum Beenden...")