"""
Модуль для управления алиасами дисциплин (автообучение)
Позволяет сохранять пары: ошибочное_название → правильное_название
"""

import json
import os
import tempfile
from pathlib import Path
from typing import Dict, Optional


class AliasManager:
    """Класс для управления алиасами дисциплин"""
    
    def __init__(
        self,
        alias_file: Optional[Path] = None,
        *,
        create_if_missing: bool = True,
    ):
        """
        Инициализирует менеджер алиасов
        
        Args:
            alias_file: Путь к JSON-файлу с алиасами
                       По умолчанию subjects_aliases.json в текущей директории
        """
        if alias_file is None:
            alias_file = Path(__file__).parent / "subjects_aliases.json"
        
        self.alias_file = alias_file
        self.create_if_missing = create_if_missing
        self.aliases: Dict[str, str] = {}
        self.load_error: Optional[str] = None
        
        # Автоматически загрузить алиасы при создании
        self.load()
    
    def load(self) -> bool:
        """
        Загружает алиасы из JSON-файла
        
        Returns:
            True если загрузка прошла успешно, False в случае ошибки
        """
        self.aliases = {}
        self.load_error = None
        
        if not self.alias_file.exists():
            if not self.create_if_missing:
                return True
            # Обратная совместимость для явного использования менеджера.
            try:
                return bool(self.save())
            except Exception as e:
                print(f"Warnung: Alias-Datei konnte nicht erstellt werden: {e}")
                return False
        
        try:
            with open(self.alias_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            if not isinstance(data, dict):
                raise TypeError("JSON-Wurzel muss ein Objekt sein")
            if 'aliases' not in data:
                raise TypeError("Pflichtfeld 'aliases' fehlt")
            aliases = data['aliases']
            if not isinstance(aliases, dict) or not all(
                isinstance(source, str)
                and source.strip()
                and isinstance(target, str)
                and target.strip()
                for source, target in aliases.items()
            ):
                raise TypeError("'aliases' muss Textpaare enthalten")
            self.aliases = aliases
            return True
        except (json.JSONDecodeError, OSError, UnicodeError, TypeError, ValueError) as e:
            self.aliases = {}
            self.load_error = (
                f"Alias-Datei '{self.alias_file.name}' ist beschädigt oder nicht lesbar: {e}. "
                "Die Datei wird nicht überschrieben."
            )
            print(f"Fehler: {self.load_error}")
            return False
    
    def save(self) -> bool:
        """
        Сохраняет алиасы в JSON-файл
        
        Returns:
            True если сохранение прошло успешно, False в случае ошибки
        """
        if self.load_error:
            print(f"Fehler: {self.load_error}")
            return False

        try:
            data = {
                'aliases': self.aliases
            }
            
            self.alias_file.parent.mkdir(parents=True, exist_ok=True)
            encoded = (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
            descriptor, temporary_name = tempfile.mkstemp(
                dir=self.alias_file.parent,
                prefix=f".{self.alias_file.name}.",
                suffix=".tmp",
            )
            temporary_path = Path(temporary_name)
            try:
                with os.fdopen(descriptor, "wb") as handle:
                    handle.write(encoded)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary_path, self.alias_file)
            except Exception:
                temporary_path.unlink(missing_ok=True)
                raise
            return True
        except (IOError, OSError) as e:
            print(f"Fehler beim Speichern von {self.alias_file.name}: {e}")
            return False

    def add_and_save(self, incorrect: str, correct: str) -> bool:
        """Persist one alias and restore the in-memory map if saving fails."""

        previous = self.aliases.copy()
        if not self.add_alias(incorrect, correct):
            return False
        try:
            if self.save():
                return True
        except Exception:
            self.aliases = previous
            raise
        self.aliases = previous
        return False
    
    def add_alias(self, incorrect: str, correct: str) -> bool:
        """
        Добавляет новый алиас (или обновляет существующий)
        
        Args:
            incorrect: Неправильное название дисциплины (например, "Mathmatik")
            correct: Правильное название дисциплины (например, "Mathematik")
            
        Returns:
            True если алиас добавлен успешно
        """
        # Нормализуем: убираем лишние пробелы
        incorrect = incorrect.strip()
        correct = correct.strip()
        
        # Игнорируем, если строки пустые или идентичные
        if not incorrect or not correct or incorrect == correct:
            return False
        
        # Добавляем алиас
        self.aliases[incorrect] = correct
        return True
    
    def apply_alias(self, subject: str) -> str:
        """
        Применяет алиас к названию дисциплины (если есть)
        
        Args:
            subject: Название дисциплины для нормализации
            
        Returns:
            Нормализованное название дисциплины (или исходное, если алиас не найден)
        """
        if not subject:
            return subject
        
        # Проверяем точное совпадение
        if subject in self.aliases:
            return self.aliases[subject]
        
        # Проверяем совпадение без учета регистра
        subject_lower = subject.lower()
        for incorrect, correct in self.aliases.items():
            if incorrect.lower() == subject_lower:
                return correct
        
        # Алиас не найден — возвращаем исходное название
        return subject
    
    def get_all_aliases(self) -> Dict[str, str]:
        """
        Возвращает все алиасы
        
        Returns:
            Словарь алиасов {неправильное: правильное}
        """
        return self.aliases.copy()
    
    def remove_alias(self, incorrect: str) -> bool:
        """
        Удаляет алиас
        
        Args:
            incorrect: Неправильное название для удаления
            
        Returns:
            True если алиас был удалён, False если не найден
        """
        if incorrect in self.aliases:
            del self.aliases[incorrect]
            return True
        return False
    
    def count(self) -> int:
        """
        Возвращает количество алиасов
        
        Returns:
            Количество алиасов
        """
        return len(self.aliases)
