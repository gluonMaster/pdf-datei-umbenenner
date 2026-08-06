"""
Модуль для открытия файлов в системных приложениях по умолчанию
"""

import os
import sys
import subprocess
from pathlib import Path


class FileOpener:
    """Класс для открытия файлов в приложениях по умолчанию"""
    
    @staticmethod
    def open_file(file_path: Path | str) -> bool:
        """
        Открывает файл в приложении по умолчанию для данного типа файла
        
        Args:
            file_path: Путь к файлу (Path или str)
            
        Returns:
            True если файл успешно открыт, False в случае ошибки
        """
        if isinstance(file_path, str):
            file_path = Path(file_path)
        
        # Проверяем существование файла
        if not file_path.exists():
            print(f"Fehler: Datei nicht gefunden: {file_path}")
            return False
        
        try:
            # Определяем ОС и используем соответствующую команду
            if sys.platform == "win32":
                # Windows: используем os.startfile
                os.startfile(str(file_path))
            elif sys.platform == "darwin":
                # macOS: используем open
                subprocess.run(["open", str(file_path)], check=True)
            else:
                # Linux: используем xdg-open
                subprocess.run(["xdg-open", str(file_path)], check=True)
            
            return True
            
        except Exception as e:
            print(f"Fehler beim Öffnen der Datei: {str(e)}")
            return False
    
    @staticmethod
    def open_pdf(pdf_path: Path | str) -> bool:
        """
        Открывает PDF файл в приложении по умолчанию
        Удобная обёртка для открытия PDF
        
        Args:
            pdf_path: Путь к PDF файлу
            
        Returns:
            True если файл успешно открыт, False в случае ошибки
        """
        return FileOpener.open_file(pdf_path)
