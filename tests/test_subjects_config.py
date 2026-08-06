"""
Тестовый скрипт для проверки работы subjects_config.json
"""

from validator import Validator
from text_parser import TextParser


def test_config_loading():
    """Проверка загрузки конфигурации"""
    print("=== Тест загрузки конфигурации ===")
    
    # Проверка валидатора
    validator_subjects = Validator.get_valid_subjects()
    print(f"Validator - валидные дисциплины ({len(validator_subjects)}): {validator_subjects}")
    
    # Проверка парсера
    parser_subjects = TextParser.get_valid_subjects()
    print(f"TextParser - валидные дисциплины ({len(parser_subjects)}): {parser_subjects}")
    
    # Проверка алиасов
    aliases = TextParser.get_subject_normalization()
    print(f"Алиасы: {aliases}")
    
    # Проверяем, что оба модуля используют одну конфигурацию
    assert validator_subjects == parser_subjects, "Списки дисциплин должны совпадать!"
    print("✓ Списки дисциплин в обоих модулях совпадают")
    

def test_validation():
    """Проверка валидации дисциплин"""
    print("\n=== Тест валидации ===")
    
    # Валидные дисциплины
    valid_tests = ["Mathematik", "Mathe", "Biologie", "Deutsch"]
    for subject in valid_tests:
        result = Validator.validate_subject(subject)
        print(f"'{subject}': {result.level.value} - {result.message}")
        assert result.is_valid(), f"'{subject}' должен быть валидным!"
    
    # Невалидная дисциплина
    invalid_result = Validator.validate_subject("Geschichte")
    print(f"'Geschichte': {invalid_result.level.value} - {invalid_result.message}")
    assert not invalid_result.is_valid(), "'Geschichte' не должен быть валидным!"
    
    print("✓ Валидация работает корректно")


def test_parsing():
    """Проверка парсинга текста"""
    print("\n=== Тест парсинга ===")
    
    test_text = """Mueller Anna Mathematik
Klasse 5a
Test vom 15.11.2024"""
    
    name, subject = TextParser.parse_document(test_text)
    print(f"Извлечено имя: {name}")
    print(f"Извлечена дисциплина: {subject}")
    
    assert name == "Mueller Anna", f"Ожидалось 'Mueller Anna', получено '{name}'"
    assert subject == "Mathematik", f"Ожидалось 'Mathematik', получено '{subject}'"
    
    print("✓ Парсинг работает корректно")


if __name__ == "__main__":
    try:
        test_config_loading()
        test_validation()
        test_parsing()
        print("\n✓✓✓ Все тесты пройдены успешно! ✓✓✓")
    except Exception as e:
        print(f"\n✗ Ошибка: {e}")
        import traceback
        traceback.print_exc()
