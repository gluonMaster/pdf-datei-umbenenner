"""
Модуль для парсинга имен и дисциплин из текста
"""

import re
import json
from pathlib import Path
from typing import Optional, Tuple, Dict, List

from alias_manager import AliasManager


class TextParser:
    """Класс для парсинга структурированного текста из бланков"""
    
    # Кеш для конфигурации дисциплин
    _subjects_cache: Optional[Dict[str, any]] = None
    _config_path = Path(__file__).parent / "subjects_config.json"
    
    # Менеджер алиасов для автообучения
    _alias_manager: Optional[AliasManager] = None
    
    @classmethod
    def _get_alias_manager(cls) -> AliasManager:
        """
        Получает экземпляр менеджера алиасов (singleton)
        
        Returns:
            AliasManager
        """
        if cls._alias_manager is None:
            cls._alias_manager = AliasManager(create_if_missing=False)
        return cls._alias_manager

    @classmethod
    def set_alias_manager(cls, manager: AliasManager) -> None:
        """Use a shared alias manager so newly confirmed aliases apply at once."""

        if not isinstance(manager, AliasManager):
            raise TypeError("manager muss ein AliasManager sein")
        cls._alias_manager = manager
    
    @classmethod
    def _load_subjects_config(cls) -> Dict[str, any]:
        """
        Загружает конфигурацию дисциплин из JSON-файла
        
        Returns:
            Словарь с конфигурацией (subjects, aliases)
        """
        # Используем кеш, если уже загружено
        if cls._subjects_cache is not None:
            return cls._subjects_cache
        
        # Дефолтная конфигурация на случай отсутствия файла
        default_config = {
            "subjects": [
                "Biologie", "Chemie", "Deutsch", "Englisch",
                "Geografie", "Mathematik", "Physik",
                "Sachkunde", "Sachunterricht"
            ],
            "aliases": {
                "Mathe": "Mathematik",
                "Bio": "Biologie",
                "Geo": "Geografie"
            }
        }
        
        try:
            if cls._config_path.exists():
                with open(cls._config_path, 'r', encoding='utf-8') as f:
                    cls._subjects_cache = json.load(f)
            else:
                # Парсинг, особенно dry-run, является только чтением.
                cls._subjects_cache = default_config
        except (json.JSONDecodeError, IOError) as e:
            # В случае ошибки используем дефолтную конфигурацию
            print(f"Warnung: Fehler beim Laden von subjects_config.json: {e}")
            cls._subjects_cache = default_config
        
        return cls._subjects_cache
    
    @classmethod
    def get_valid_subjects(cls) -> List[str]:
        """
        Получает список валидных дисциплин
        
        Returns:
            Список дисциплин (основные + алиасы)
        """
        config = cls._load_subjects_config()
        subjects = config.get("subjects", [])
        aliases = list(config.get("aliases", {}).keys())
        return subjects + aliases
    
    @classmethod
    def get_subject_normalization(cls) -> Dict[str, str]:
        """
        Получает словарь нормализации алиасов дисциплин
        
        Returns:
            Словарь алиас -> полное название
        """
        config = cls._load_subjects_config()
        return config.get("aliases", {})
    
    @classmethod
    def normalize_subject(cls, subject: Optional[str]) -> Optional[str]:
        """
        Нормализует название дисциплины с применением:
        1. Встроенных алиасов из subjects_config.json
        2. Автообученных алиасов из subjects_aliases.json
        
        Args:
            subject: Название дисциплины для нормализации
            
        Returns:
            Нормализованное название или None
        """
        if not subject:
            return None
        
        # Сначала применяем встроенные алиасы
        normalization = cls.get_subject_normalization()
        normalized = normalization.get(subject, subject)
        
        # Затем применяем автообученные алиасы
        alias_manager = cls._get_alias_manager()
        normalized = alias_manager.apply_alias(normalized)
        
        return normalized
    
    @staticmethod
    def clean_text(text: str) -> str:
        """
        Очищает текст от артефактов OCR
        
        Args:
            text: Исходный текст
            
        Returns:
            Очищенный текст
        """
        # Удаляем множественные пробелы
        text = re.sub(r'\s+', ' ', text)
        # Удаляем ведущие/завершающие пробелы
        text = text.strip()
        return text
    
    @classmethod
    def extract_student_name(cls, text: str) -> Optional[str]:
        """
        Извлекает имя и фамилию студента из текста
        
        Args:
            text: Текст для парсинга
            
        Returns:
            Имя в формате "Фамилия Имя" или None
        """
        lines = text.split('\n')
        valid_subjects = cls.get_valid_subjects()
        
        for line in lines[:3]:  # Проверяем первые 3 строки
            line = cls.clean_text(line)
            words = line.split()
            
            # Проверяем формат "Фамилия Имя Дисциплина" (3 слова)
            if len(words) == 3:
                surname, name, subject = words
                
                # Проверяем, что третье слово - дисциплина
                if subject in valid_subjects:
                    # Проверяем, что первые два слова выглядят как имена
                    if (cls._is_mostly_letters(surname) and 
                        cls._is_mostly_letters(name) and
                        surname[0].isupper() and name[0].isupper()):
                        return f"{surname} {name}"
            
            # Проверяем традиционный формат: два слова, начинающиеся с заглавной буквы
            elif len(words) == 2:
                # Ищем паттерн: два слова, начинающиеся с заглавной буквы
                # Допускаем латиницу, кириллицу и дефисы
                pattern = r'^([A-ZА-ЯЁ][a-zа-яё\-]+)\s+([A-ZА-ЯЁ][a-zа-яё\-]+)$'
                match = re.match(pattern, line)
                
                if match:
                    surname, name = match.groups()
                    # Проверяем, что это не дисциплина
                    if line not in valid_subjects:
                        return f"{surname} {name}"
        
        # Если точное совпадение не найдено, пробуем более мягкий паттерн
        for line in lines[:3]:
            line = cls.clean_text(line)
            
            # Более мягкий паттерн: два слова из букв
            words = line.split()
            if len(words) == 2:
                word1, word2 = words
                # Проверяем, что состоят в основном из букв
                if (cls._is_mostly_letters(word1) and 
                    cls._is_mostly_letters(word2) and
                    line not in valid_subjects):
                    return f"{word1} {word2}"
        
        return None
    
    @classmethod
    def extract_subject(cls, text: str) -> Optional[str]:
        """
        Извлекает название дисциплины из текста
        
        Args:
            text: Текст для парсинга
            
        Returns:
            Название дисциплины или None
        """
        lines = text.split('\n')
        valid_subjects = cls.get_valid_subjects()
        
        # Сначала проверяем формат "Фамилия Имя Дисциплина" в первой строке
        for line in lines[:3]:  # Проверяем первые 3 строки
            line = cls.clean_text(line)
            words = line.split()
            
            # Если 3 слова, третье должно быть дисциплиной
            if len(words) == 3:
                potential_subject = words[2]
                
                # Проверяем точное совпадение
                if potential_subject in valid_subjects:
                    # Нормализуем (применяем встроенные и автообученные алиасы)
                    return cls.normalize_subject(potential_subject)
        
        # Ищем точное совпадение с известными дисциплинами
        for line in lines[:5]:  # Проверяем первые 5 строк
            line = cls.clean_text(line)
            
            for subject in valid_subjects:
                if subject.lower() == line.lower():
                    return cls.normalize_subject(subject)
        
        # Ищем частичное совпадение
        text_lower = text.lower()
        for subject in valid_subjects:
            if subject.lower() in text_lower:
                return cls.normalize_subject(subject)
        
        # Если ничего не найдено, пробуем применить алиасы к сырому тексту
        # (на случай, если в тексте есть опечатка, но она уже знакома системе)
        for line in lines[:5]:
            line = cls.clean_text(line)
            words = line.split()
            
            for word in words:
                # Применяем нормализацию к каждому слову
                normalized = cls.normalize_subject(word)
                if normalized and normalized in valid_subjects:
                    return normalized
        
        return None
    
    @staticmethod
    def _is_mostly_letters(word: str) -> bool:
        """
        Проверяет, что слово состоит преимущественно из букв
        
        Args:
            word: Слово для проверки
            
        Returns:
            True если >80% символов - буквы
        """
        if not word:
            return False
        
        letter_count = sum(1 for c in word if c.isalpha() or c == '-')
        return letter_count / len(word) >= 0.8
    
    @classmethod
    def parse_document(cls, text: str) -> Tuple[Optional[str], Optional[str]]:
        """
        Парсит документ и извлекает имя и дисциплину
        
        Args:
            text: Текст документа
            
        Returns:
            Кортеж (имя, дисциплина)
        """
        student_name = cls.extract_student_name(text)
        subject = cls.extract_subject(text)
        
        return student_name, subject
