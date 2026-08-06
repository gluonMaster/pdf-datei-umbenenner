"""
Demo: Полная демонстрация работы review_queue в контексте main.py
Показывает как проблемные файлы попадают в очередь и сохраняются в JSON
"""

import sys
import json
from pathlib import Path

# Добавляем родительскую папку в путь
sys.path.insert(0, str(Path(__file__).parent.parent))

from validator import Validator, ValidationLevel


def demo_validation_pipeline():
    """Демонстрация полного pipeline валидации"""
    print("="*70)
    print("ДЕМОНСТРАЦИЯ: Review Queue Pipeline")
    print("="*70)
    print()
    
    # Примеры проблемных данных, которые встречаются в реальных сканах
    test_cases = [
        ("Max Mustermann", "Mathematik", "✓ Корректные данные"),
        ("Anna Schmidt", "Biologia", "✗ Неизвестная дисциплина (OCR-ошибка)"),
        ("Peter Mueller", "Matematik", "✗ Опечатка в дисциплине"),
        ("M", "Deutsch", "✗ Слишком короткое имя"),
        ("John123 Doe", "Physik", "✗ Имя содержит цифры"),
        ("Lisa Wagner", "Chemie", "✓ Корректные данные"),
        ("Tom Smith", "Geografie", "✓ Корректные данные"),
    ]
    
    manual_check_items = []
    success_items = []
    
    print("Обработка тестовых случаев:\n")
    
    for idx, (name, subject, description) in enumerate(test_cases, 1):
        print(f"{idx}. {description}")
        print(f"   Имя: {name}")
        print(f"   Дисциплина: {subject}")
        
        # Валидация
        validation_result = Validator.validate_document_data(name, subject)
        
        print(f"   Результат валидации: {validation_result.level.value}")
        
        if validation_result.level == ValidationLevel.MANUAL_CHECK:
            print(f"   ⚠ Причина: {validation_result.message}")
            
            if validation_result.suggestions:
                print(f"   💡 Подсказки: {', '.join(validation_result.suggestions)}")
            
            # Имитируем создание ReviewItem
            review_item = {
                "file_path": f"file_{idx}.pdf",
                "raw_name": name,
                "raw_subject": subject,
                "reason": validation_result.message,
                "suggestions": validation_result.suggestions,
                "preview_text": f"Name: {name}\nFach: {subject}\n[... остальной текст ...]"
            }
            manual_check_items.append(review_item)
            print("   → Добавлен в очередь ручной проверки")
            
        elif validation_result.level == ValidationLevel.WARNING:
            print(f"   ⚠ Предупреждение: {validation_result.message}")
            print("   → Будет автоматически переименован (с предупреждением)")
            success_items.append((name, subject))
            
        else:  # VALID
            print("   ✓ Будет автоматически переименован")
            success_items.append((name, subject))
        
        print()
    
    # Статистика
    print("="*70)
    print("СТАТИСТИКА")
    print("="*70)
    print(f"Всего обработано:               {len(test_cases)}")
    print(f"Успешно (автоматически):        {len(success_items)}")
    print(f"Требует ручной проверки:        {len(manual_check_items)}")
    print()
    
    # Показываем как будет выглядеть manual_review.json
    if manual_check_items:
        print("="*70)
        print("СОДЕРЖИМОЕ manual_review.json")
        print("="*70)
        
        json_content = {
            "review_items": manual_check_items
        }
        
        json_str = json.dumps(json_content, ensure_ascii=False, indent=2)
        print(json_str)
        print()
        
        # Детальный разбор каждого проблемного случая
        print("="*70)
        print("ДЕТАЛИ ПРОБЛЕМНЫХ ФАЙЛОВ")
        print("="*70)
        
        for idx, item in enumerate(manual_check_items, 1):
            print(f"\n{idx}. Файл: {item['file_path']}")
            print(f"   Извлечённые данные:")
            print(f"     - Имя: {item['raw_name']}")
            print(f"     - Дисциплина: {item['raw_subject']}")
            print(f"   Проблема: {item['reason']}")
            
            if item['suggestions']:
                print(f"   Предложения для исправления:")
                for sug in item['suggestions']:
                    print(f"     • {sug}")
            
            print(f"   Фрагмент текста:")
            for line in item['preview_text'].split('\n'):
                print(f"     {line}")
    
    print("\n" + "="*70)
    print("СЛЕДУЮЩИЕ ШАГИ")
    print("="*70)
    print("1. Оператор откроет GUI вкладку 'Проверка'")
    print("2. Увидит список проблемных файлов с подсказками")
    print("3. Выберет корректную дисциплину из предложенных")
    print("4. Система автоматически:")
    print("   - Переименует файл корректно")
    print("   - Обучит алиас (если выбрана подсказка)")
    print("   - Удалит файл из очереди")
    print("="*70)


if __name__ == '__main__':
    demo_validation_pipeline()
