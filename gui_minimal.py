"""Compatibility launcher for the single maintained Tkinter interface.

Historically this module contained a second processing pipeline.  Keeping only
this alias ensures old shortcuts and PyInstaller entry points use the same GUI
and :class:`processing_service.DocumentProcessingService` as every other entry
point.
"""

from gui_main import PDFRenamerGUI, main


SimplePDFRenamerGUI = PDFRenamerGUI


__all__ = ["PDFRenamerGUI", "SimplePDFRenamerGUI", "main"]


if __name__ == "__main__":
    main()
