"""
Тесты для модуля review_queue.py
"""

import json
import tempfile
from pathlib import Path
import pytest

from review_queue import ReviewItem, ReviewStore


class TestReviewItem:
    """Тесты для класса ReviewItem"""
    
    def test_create_review_item(self):
        """Тест создания ReviewItem"""
        item = ReviewItem(
            file_path="test.pdf",
            raw_name="Max Mustermann",
            raw_subject="Mathematik",
            reason="Test reason",
            suggestions=["Mathe", "Physik"],
            preview_text="Sample text from PDF"
        )
        
        assert item.file_path == "test.pdf"
        assert item.raw_name == "Max Mustermann"
        assert item.raw_subject == "Mathematik"
        assert item.reason == "Test reason"
        assert len(item.suggestions) == 2
        assert item.preview_text == "Sample text from PDF"
    
    def test_to_dict(self):
        """Тест преобразования в словарь"""
        item = ReviewItem(
            file_path="test.pdf",
            raw_name="Max Mustermann",
            raw_subject="Mathematik",
            reason="Test reason",
            suggestions=["Mathe"],
            preview_text="Sample text"
        )
        
        data = item.to_dict()
        
        assert isinstance(data, dict)
        assert data['file_path'] == "test.pdf"
        assert data['raw_name'] == "Max Mustermann"
        assert data['suggestions'] == ["Mathe"]
    
    def test_from_dict(self):
        """Тест создания из словаря"""
        data = {
            'file_path': "test.pdf",
            'raw_name': "Max Mustermann",
            'raw_subject': "Mathematik",
            'reason': "Test reason",
            'suggestions': ["Mathe"],
            'preview_text': "Sample text"
        }
        
        item = ReviewItem.from_dict(data)
        
        assert item.file_path == "test.pdf"
        assert item.raw_name == "Max Mustermann"
        assert item.suggestions == ["Mathe"]


class TestReviewStore:
    """Тесты для класса ReviewStore"""
    
    def test_create_store(self):
        """Тест создания хранилища"""
        with tempfile.TemporaryDirectory() as tmpdir:
            store_path = Path(tmpdir) / "test_review.json"
            store = ReviewStore(store_path)
            
            assert store.store_path == store_path
            assert store.count() == 0
    
    def test_add_item(self):
        """Тест добавления элемента"""
        with tempfile.TemporaryDirectory() as tmpdir:
            store = ReviewStore(Path(tmpdir) / "test.json")
            
            item = ReviewItem(
                file_path="test.pdf",
                raw_name="Max Mustermann",
                raw_subject="Mathematik",
                reason="Test",
                suggestions=[],
                preview_text="Text"
            )
            
            store.add(item)
            assert store.count() == 1
            
            items = store.get_all()
            assert len(items) == 1
            assert items[0].file_path == "test.pdf"
    
    def test_save_and_load(self):
        """Тест сохранения и загрузки"""
        with tempfile.TemporaryDirectory() as tmpdir:
            store_path = Path(tmpdir) / "test_review.json"
            
            # Создаем и сохраняем
            store1 = ReviewStore(store_path)
            item1 = ReviewItem(
                file_path="test1.pdf",
                raw_name="Max Mustermann",
                raw_subject="Mathematik",
                reason="Test reason 1",
                suggestions=["Mathe"],
                preview_text="Text 1"
            )
            item2 = ReviewItem(
                file_path="test2.pdf",
                raw_name="Anna Schmidt",
                raw_subject="Biologie",
                reason="Test reason 2",
                suggestions=["Bio"],
                preview_text="Text 2"
            )
            
            store1.add(item1)
            store1.add(item2)
            
            assert store1.save()
            assert store_path.exists()
            
            # Загружаем в новое хранилище
            store2 = ReviewStore(store_path)
            store2.load()
            
            assert store2.count() == 2
            
            items = store2.get_all()
            assert items[0].file_path == "test1.pdf"
            assert items[0].raw_name == "Max Mustermann"
            assert items[1].file_path == "test2.pdf"
            assert items[1].raw_subject == "Biologie"
    
    def test_clear(self):
        """Тест очистки очереди"""
        with tempfile.TemporaryDirectory() as tmpdir:
            store = ReviewStore(Path(tmpdir) / "test.json")
            
            item = ReviewItem(
                file_path="test.pdf",
                raw_name="Max Mustermann",
                raw_subject="Mathematik",
                reason="Test",
                suggestions=[],
                preview_text="Text"
            )
            
            store.add(item)
            assert store.count() == 1
            
            store.clear()
            assert store.count() == 0
    
    def test_remove_item(self):
        """Тест удаления элемента"""
        with tempfile.TemporaryDirectory() as tmpdir:
            store = ReviewStore(Path(tmpdir) / "test.json")
            
            item1 = ReviewItem(
                file_path="test1.pdf",
                raw_name="Max Mustermann",
                raw_subject="Mathematik",
                reason="Test",
                suggestions=[],
                preview_text="Text"
            )
            item2 = ReviewItem(
                file_path="test2.pdf",
                raw_name="Anna Schmidt",
                raw_subject="Biologie",
                reason="Test",
                suggestions=[],
                preview_text="Text"
            )
            
            store.add(item1)
            store.add(item2)
            assert store.count() == 2
            
            # Удаляем первый элемент
            result = store.remove("test1.pdf")
            assert result is True
            assert store.count() == 1
            
            items = store.get_all()
            assert items[0].file_path == "test2.pdf"
            
            # Пытаемся удалить несуществующий элемент
            result = store.remove("nonexistent.pdf")
            assert result is False
            assert store.count() == 1
    
    def test_load_nonexistent_file(self):
        """Тест загрузки несуществующего файла"""
        with tempfile.TemporaryDirectory() as tmpdir:
            store_path = Path(tmpdir) / "nonexistent.json"
            store = ReviewStore(store_path)
            
            store.load()
            
            # Должна создаться пустая очередь без ошибок
            assert store.count() == 0
    
    def test_load_corrupted_file(self):
        """Тест загрузки поврежденного файла"""
        with tempfile.TemporaryDirectory() as tmpdir:
            store_path = Path(tmpdir) / "corrupted.json"
            
            # Создаем поврежденный JSON
            with open(store_path, 'w') as f:
                f.write("{ invalid json }")
            
            store = ReviewStore(store_path)
            store.load()
            
            # Должна создаться пустая очередь без ошибок
            assert store.count() == 0
    
    def test_json_format(self):
        """Тест формата сохраненного JSON"""
        with tempfile.TemporaryDirectory() as tmpdir:
            store_path = Path(tmpdir) / "test_format.json"
            store = ReviewStore(store_path)
            
            item = ReviewItem(
                file_path="test.pdf",
                raw_name="Max Mustermann",
                raw_subject="Mathematik",
                reason="Test reason",
                suggestions=["Mathe", "Physik"],
                preview_text="Sample text"
            )
            
            store.add(item)
            store.save()
            
            # Проверяем структуру сохраненного JSON
            with open(store_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            assert 'review_items' in data
            assert isinstance(data['review_items'], list)
            assert len(data['review_items']) == 1
            
            item_data = data['review_items'][0]
            assert item_data['file_path'] == "test.pdf"
            assert item_data['raw_name'] == "Max Mustermann"
            assert item_data['suggestions'] == ["Mathe", "Physik"]


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
