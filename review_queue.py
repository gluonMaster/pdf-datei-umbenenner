"""
Модуль для управления очередью ручной проверки проблемных файлов
"""

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import List, Optional


@dataclass
class ReviewItem:
    """
    Элемент очереди ручной проверки
    
    Attributes:
        file_path: Путь к файлу (относительно корневой папки)
        raw_name: Извлеченное имя (может быть некорректным)
        raw_subject: Извлеченная дисциплина (может быть некорректной)
        reason: Причина попадания в ручную проверку
        suggestions: Список предложенных дисциплин для исправления
        preview_text: Фрагмент текста из первой страницы для контекста
    """
    file_path: str
    raw_name: Optional[str]
    raw_subject: Optional[str]
    reason: str
    suggestions: List[str]
    preview_text: str
    # Pipeline metadata.  Defaults keep every pre-pipeline JSON entry valid.
    form_type: Optional[str] = None
    parser_confidence: Optional[float] = None
    parser_reasons: List[str] = field(default_factory=list)
    student_match_status: Optional[str] = None
    student_suggestions: List[str] = field(default_factory=list)
    canonical_student_number: Optional[int] = None
    canonical_name: Optional[str] = None
    canonical_subject: Optional[str] = None
    selected_month: Optional[int] = None
    selected_year: Optional[int] = None
    planned_filename: Optional[str] = None
    extraction_source: Optional[str] = None
    ocr_status: Optional[str] = None
    ocr_name_confidence: Optional[float] = None
    ocr_subject_confidence: Optional[float] = None
    
    def to_dict(self) -> dict:
        """Преобразует ReviewItem в словарь"""
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: dict) -> 'ReviewItem':
        """Создает ReviewItem из старого или текущего словаря.

        Unknown keys are ignored so a newer queue can also be opened by a
        slightly older application build.  Missing pipeline fields use the
        dataclass defaults above.
        """
        if not isinstance(data, dict):
            raise TypeError("Ein Warteschlangeneintrag muss ein JSON-Objekt sein")
        known_fields = {item.name for item in fields(cls)}
        payload = {key: value for key, value in data.items() if key in known_fields}
        payload.setdefault("suggestions", [])
        payload.setdefault("preview_text", "")
        payload.setdefault("parser_reasons", [])
        payload.setdefault("student_suggestions", [])
        item = cls(**payload)
        item._validate_types()
        return item

    def _validate_types(self) -> None:
        """Reject malformed queue entries before they reach the GUI or a save."""

        if not isinstance(self.file_path, str) or not self.file_path.strip():
            raise TypeError("'file_path' muss nichtleerer Text sein")
        if not isinstance(self.reason, str):
            raise TypeError("'reason' muss Text sein")
        if not isinstance(self.preview_text, str):
            raise TypeError("'preview_text' muss Text sein")

        optional_text = (
            "raw_name",
            "raw_subject",
            "form_type",
            "student_match_status",
            "canonical_name",
            "canonical_subject",
            "planned_filename",
            "extraction_source",
            "ocr_status",
        )
        for field_name in optional_text:
            value = getattr(self, field_name)
            if value is not None and not isinstance(value, str):
                raise TypeError(f"'{field_name}' muss Text oder null sein")

        for field_name in ("suggestions", "parser_reasons", "student_suggestions"):
            value = getattr(self, field_name)
            if not isinstance(value, list) or not all(
                isinstance(entry, str) for entry in value
            ):
                raise TypeError(f"'{field_name}' muss eine Textliste sein")

        if self.canonical_student_number is not None and (
            not isinstance(self.canonical_student_number, int)
            or isinstance(self.canonical_student_number, bool)
        ):
            raise TypeError("'canonical_student_number' muss eine Ganzzahl oder null sein")
        if self.selected_month is not None and (
            not isinstance(self.selected_month, int)
            or isinstance(self.selected_month, bool)
            or not 1 <= self.selected_month <= 12
        ):
            raise TypeError("'selected_month' muss eine Ganzzahl von 1 bis 12 oder null sein")
        if self.selected_year is not None and (
            not isinstance(self.selected_year, int)
            or isinstance(self.selected_year, bool)
            or self.selected_year < 1
        ):
            raise TypeError("'selected_year' muss eine positive Ganzzahl oder null sein")

        for field_name in (
            "parser_confidence",
            "ocr_name_confidence",
            "ocr_subject_confidence",
        ):
            value = getattr(self, field_name)
            if value is not None and (
                not isinstance(value, (int, float)) or isinstance(value, bool)
            ):
                raise TypeError(f"'{field_name}' muss eine Zahl oder null sein")


class ReviewStore:
    """Класс для хранения и управления очередью ручной проверки"""
    
    def __init__(self, store_path: Optional[Path] = None):
        """
        Инициализирует хранилище
        
        Args:
            store_path: Путь к файлу JSON для хранения очереди
                       По умолчанию manual_review.json в текущей директории
        """
        if store_path is None:
            store_path = Path(__file__).parent / "manual_review.json"
        
        self.store_path = Path(store_path)
        self.items: List[ReviewItem] = []
        # A damaged queue must never be treated as an intentionally empty one:
        # doing so would let the next save overwrite the only recovery source.
        self.load_error: Optional[str] = None
    
    def add(self, item: ReviewItem) -> None:
        """
        Добавляет элемент в очередь
        
        Args:
            item: Элемент для добавления
        """
        self.items.append(item)
    
    def load(self) -> bool:
        """
        Загружает очередь из файла
        
        Если файла нет, начинает с пустой очереди. Повреждённый файл
        переводит хранилище в fail-closed режим без права перезаписи.
        """
        self.items = []
        self.load_error = None

        if not self.store_path.exists():
            return True

        try:
            with open(self.store_path, 'r', encoding='utf-8') as f:
                data = json.load(f)

            if not isinstance(data, dict):
                raise TypeError("JSON-Wurzel muss ein Objekt sein")
            if 'review_items' not in data:
                raise TypeError("Pflichtfeld 'review_items' fehlt")
            raw_items = data['review_items']
            if not isinstance(raw_items, list):
                raise TypeError("'review_items' muss eine Liste sein")

            loaded_items = [ReviewItem.from_dict(item_dict) for item_dict in raw_items]
            self.items = loaded_items
            return True
        except (json.JSONDecodeError, OSError, UnicodeError, TypeError, ValueError) as e:
            self.items = []
            self.load_error = (
                f"Warteschlange '{self.store_path.name}' ist beschädigt oder nicht lesbar: {e}. "
                "Die Datei wird nicht überschrieben."
            )
            print(f"Fehler: {self.load_error}")
            return False
    
    def save(self) -> bool:
        """
        Сохраняет очередь в файл
        
        Returns:
            True если сохранение прошло успешно, False в случае ошибки
        """
        if self.load_error:
            print(f"Fehler: {self.load_error}")
            return False

        try:
            data = {
                'review_items': [item.to_dict() for item in self.items]
            }
            
            self.store_path.parent.mkdir(parents=True, exist_ok=True)
            encoded = (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
            descriptor, temporary_name = tempfile.mkstemp(
                dir=self.store_path.parent,
                prefix=f".{self.store_path.name}.",
                suffix=".tmp",
            )
            temporary_path = Path(temporary_name)
            try:
                with os.fdopen(descriptor, "wb") as handle:
                    handle.write(encoded)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary_path, self.store_path)
            except Exception as error:
                try:
                    temporary_path.unlink(missing_ok=True)
                except OSError as cleanup_error:
                    raise OSError(
                        f"Speichern fehlgeschlagen: {error}. Temporäre Datei "
                        f"konnte nicht entfernt werden: {temporary_path} ({cleanup_error})."
                    ) from error
                raise
            return True
            
        except (IOError, OSError) as e:
            print(f"Fehler beim Speichern von {self.store_path.name}: {e}")
            return False
    
    def clear(self) -> None:
        """Очищает очередь"""
        self.items = []
    
    def count(self) -> int:
        """
        Возвращает количество элементов в очереди
        
        Returns:
            Количество элементов
        """
        return len(self.items)
    
    def get_all(self) -> List[ReviewItem]:
        """
        Возвращает все элементы очереди
        
        Returns:
            Список элементов
        """
        return self.items.copy()
    
    def remove(self, file_path: str) -> bool:
        """
        Удаляет элемент из очереди по пути к файлу
        
        Args:
            file_path: Путь к файлу для удаления
            
        Returns:
            True если элемент был найден и удален, False иначе
        """
        original_count = len(self.items)
        self.items = [item for item in self.items if item.file_path != file_path]
        return len(self.items) < original_count
