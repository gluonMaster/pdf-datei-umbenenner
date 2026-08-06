"""
Модуль для управления очередью ручной проверки проблемных файлов
"""

import json
from dataclasses import dataclass, asdict
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
    
    def to_dict(self) -> dict:
        """Преобразует ReviewItem в словарь"""
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: dict) -> 'ReviewItem':
        """Создает ReviewItem из словаря"""
        return cls(**data)


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
        
        self.store_path = store_path
        self.items: List[ReviewItem] = []
    
    def add(self, item: ReviewItem) -> None:
        """
        Добавляет элемент в очередь
        
        Args:
            item: Элемент для добавления
        """
        self.items.append(item)
    
    def load(self) -> None:
        """
        Загружает очередь из файла
        
        Если файл не существует или поврежден, создает пустую очередь
        """
        self.items = []
        
        if not self.store_path.exists():
            return
        
        try:
            with open(self.store_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                
            # Преобразуем список словарей в список ReviewItem
            for item_dict in data.get('review_items', []):
                try:
                    item = ReviewItem.from_dict(item_dict)
                    self.items.append(item)
                except (TypeError, KeyError) as e:
                    # Пропускаем поврежденные записи
                    print(f"Warnung: Beschaedigter Eintrag uebersprungen: {e}")
                    continue
                    
        except (json.JSONDecodeError, IOError) as e:
            print(f"Warnung: Fehler beim Laden von {self.store_path.name}: {e}")
            self.items = []
    
    def save(self) -> bool:
        """
        Сохраняет очередь в файл
        
        Returns:
            True если сохранение прошло успешно, False в случае ошибки
        """
        try:
            data = {
                'review_items': [item.to_dict() for item in self.items]
            }
            
            with open(self.store_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            
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
