"""
Модуль для валидации извлеченных данных
"""

import re
import json
import difflib
from pathlib import Path
from enum import Enum
from typing import Optional, Dict, List


class ValidationLevel(Enum):
    """Уровни валидации"""
    VALID = "valid"              # Полностью валидный
    WARNING = "warning"          # Валидный с предупреждениями
    MANUAL_CHECK = "manual"      # Требует ручной проверки


class ValidationResult:
    """Результат валидации"""
    
    def __init__(self, level: ValidationLevel, message: str = "", suggestions: Optional[List[str]] = None,
                 canonical_value: Optional[str] = None):
        self.level = level
        self.message = message
        self.suggestions = suggestions or []  # Список подсказок для исправления
        self.canonical_value = canonical_value
    
    def is_valid(self) -> bool:
        """Можно ли автоматически переименовать файл"""
        return self.level in [ValidationLevel.VALID, ValidationLevel.WARNING]


class Validator:
    """Класс для валидации извлеченных данных"""
    
    # Кеш для конфигурации дисциплин
    _subjects_cache: Optional[Dict[str, any]] = None
    _config_path = Path(__file__).parent / "subjects_config.json"
    
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
                # Чтение конфигурации не должно иметь побочных эффектов (важно
                # для dry-run); запись выполняется только явными редакторами.
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
            Список дисциплин
        """
        config = cls._load_subjects_config()
        # Возвращаем основные дисциплины + все алиасы
        subjects = config.get("subjects", [])
        aliases = list(config.get("aliases", {}).keys())
        return subjects + aliases
    
    @classmethod
    def suggest_subjects(cls, subject: str, limit: int = 5) -> List[str]:
        """
        Предлагает похожие дисциплины на основе введенной строки
        
        Args:
            subject: Введенная дисциплина
            limit: Максимальное количество подсказок (по умолчанию 5)
            
        Returns:
            Список похожих дисциплин (до limit штук)
        """
        if not subject:
            return []
        
        valid_subjects = cls.get_valid_subjects()
        
        # Используем difflib для поиска близких совпадений
        # cutoff=0.6 означает минимум 60% схожести
        suggestions = difflib.get_close_matches(
            subject, 
            valid_subjects, 
            n=limit, 
            cutoff=0.6
        )
        
        return suggestions
    
    @staticmethod
    def validate_name(name: Optional[str]) -> ValidationResult:
        """
        Валидирует имя студента
        
        Args:
            name: Имя для валидации
            
        Returns:
            Результат валидации
        """
        if not name:
            return ValidationResult(
                ValidationLevel.MANUAL_CHECK,
                "Name nicht gefunden"
            )
        
        # Проверка на минимальную длину
        if len(name) < 5:
            return ValidationResult(
                ValidationLevel.MANUAL_CHECK,
                f"Name zu kurz: {name}"
            )
        
        # Проверка, что имя состоит из двух частей
        parts = name.split()
        if len(parts) != 2:
            return ValidationResult(
                ValidationLevel.MANUAL_CHECK,
                f"Name hat unerwartetes Format: {name}"
            )
        
        # Проверка на цифры
        if re.search(r'\d', name):
            return ValidationResult(
                ValidationLevel.MANUAL_CHECK,
                f"Name enthaelt Zahlen: {name}"
            )
        
        # Проверка на подозрительные символы
        suspicious_chars = r'[!@#$%^&*()+=\[\]{};:"|<>?/\\]'
        if re.search(suspicious_chars, name):
            return ValidationResult(
                ValidationLevel.MANUAL_CHECK,
                f"Name enthaelt verdaechtige Zeichen: {name}"
            )
        
        # Проверка на смешанные алфавиты в одном слове
        for part in parts:
            has_latin = bool(re.search(r'[A-Za-z]', part))
            has_cyrillic = bool(re.search(r'[А-Яа-яЁё]', part))
            
            if has_latin and has_cyrillic:
                return ValidationResult(
                    ValidationLevel.WARNING,
                    f"Name enthaelt gemischte Schriften: {name}"
                )
        
        # Проверка, что каждая часть начинается с заглавной буквы
        if not all(part[0].isupper() for part in parts):
            return ValidationResult(
                ValidationLevel.WARNING,
                f"Name beginnt nicht mit Grossbuchstaben: {name}"
            )
        
        return ValidationResult(ValidationLevel.VALID)
    
    @classmethod
    def validate_subject(cls, subject: Optional[str]) -> ValidationResult:
        """
        Валидирует дисциплину
        
        Args:
            subject: Дисциплина для валидации
            
        Returns:
            Результат валидации с подсказками при ошибке
        """
        if not subject:
            return ValidationResult(
                ValidationLevel.MANUAL_CHECK,
                "Fach nicht gefunden"
            )
        
        config = cls._load_subjects_config()
        canonical_subjects = config.get("subjects", [])
        aliases = config.get("aliases", {})
        valid_subjects = canonical_subjects + list(aliases.keys())
        if subject not in valid_subjects:
            # Получаем подсказки для неизвестной дисциплины
            suggestions = cls.suggest_subjects(subject, limit=5)
            
            return ValidationResult(
                ValidationLevel.MANUAL_CHECK,
                f"Unbekanntes Fach: {subject}",
                suggestions=suggestions
            )
        
        return ValidationResult(
            ValidationLevel.VALID,
            canonical_value=aliases.get(subject, subject),
        )
    
    @classmethod
    def validate_document_data(cls, name: Optional[str], 
                              subject: Optional[str]) -> ValidationResult:
        """
        Валидирует полные данные документа
        
        Args:
            name: Имя студента
            subject: Дисциплина
            
        Returns:
            Результат валидации с подсказками при ошибке
        """
        name_result = cls.validate_name(name)
        subject_result = cls.validate_subject(subject)
        
        # Если хотя бы одна проверка требует ручной проверки
        if (name_result.level == ValidationLevel.MANUAL_CHECK or 
            subject_result.level == ValidationLevel.MANUAL_CHECK):
            messages = []
            if name_result.message:
                messages.append(name_result.message)
            if subject_result.message:
                messages.append(subject_result.message)
            
            # Пробрасываем подсказки из валидации дисциплины
            return ValidationResult(
                ValidationLevel.MANUAL_CHECK,
                "; ".join(messages),
                suggestions=subject_result.suggestions,
                canonical_value=subject_result.canonical_value,
            )
        
        # Если есть предупреждения
        if (name_result.level == ValidationLevel.WARNING or 
            subject_result.level == ValidationLevel.WARNING):
            messages = []
            if name_result.message:
                messages.append(name_result.message)
            if subject_result.message:
                messages.append(subject_result.message)
            
            return ValidationResult(
                ValidationLevel.WARNING,
                "; ".join(messages) if messages else "",
                canonical_value=subject_result.canonical_value,
            )
        
        return ValidationResult(
            ValidationLevel.VALID,
            canonical_value=subject_result.canonical_value,
        )
