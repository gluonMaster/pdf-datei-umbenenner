"""
Модуль для извлечения текста из PDF файлов
"""

import pdfplumber
from pathlib import Path
from typing import Optional


class PDFExtractor:
    """Класс для извлечения текста из PDF файлов"""
    
    @staticmethod
    def extract_first_lines(pdf_path: Path, num_lines: int = 10) -> Optional[str]:
        """
        Извлекает первые N строк текста из PDF файла
        
        Args:
            pdf_path: Путь к PDF файлу
            num_lines: Количество строк для извлечения
            
        Returns:
            Текст первых строк или None в случае ошибки
        """
        try:
            with pdfplumber.open(pdf_path) as pdf:
                if len(pdf.pages) == 0:
                    return None
                
                # Извлекаем текст с первой страницы
                first_page = pdf.pages[0]
                text = first_page.extract_text()
                
                if not text:
                    return None
                
                # Берем первые N строк
                lines = text.split('\n')
                return '\n'.join(lines[:num_lines])
                
        except Exception as e:
            print(f"Fehler beim Lesen der Datei {pdf_path.name}: {str(e)}")
            return None
    
    @staticmethod
    def is_valid_pdf(pdf_path: Path) -> bool:
        """
        Проверяет, является ли файл валидным PDF
        
        Args:
            pdf_path: Путь к файлу
            
        Returns:
            True если файл валидный PDF
        """
        if not pdf_path.exists() or not pdf_path.is_file():
            return False
            
        if pdf_path.suffix.lower() != '.pdf':
            return False
            
        try:
            with pdfplumber.open(pdf_path) as pdf:
                return len(pdf.pages) > 0
        except:
            return False
