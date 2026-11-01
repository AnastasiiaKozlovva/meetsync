import asyncio
import json
import logging
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

# Обеспечиваем корректный вывод UTF-8 в консоли Windows
if sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")
if sys.stderr.encoding.lower() != "utf-8":
    sys.stderr.reconfigure(encoding="utf-8")

# Добавляем src в sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from pydantic import ValidationError

from groq import GroqError, RateLimitError
from meetsync_llm import AvailabilityParser, ParsingResult
from meetsync_llm.schemas import TimeSlot

# Настройка логирования для тестов
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")


class TestTimeSlotValidation(unittest.TestCase):
    """Unit-тесты валидации модели TimeSlot."""

    def test_valid_time_slot(self):
        slot = TimeSlot(start_time="09:00", end_time="18:00")
        self.assertEqual(slot.start_time, "09:00")

    def test_invalid_format_raises(self):
        with self.assertRaises(ValidationError):
            TimeSlot(start_time="9:00", end_time="18:00")

    def test_out_of_range_hours_raises(self):
        with self.assertRaises(ValidationError):
            TimeSlot(start_time="25:00", end_time="26:00")

    def test_start_not_before_end_raises(self):
        with self.assertRaises(ValidationError):
            TimeSlot(start_time="18:00", end_time="09:00")

    def test_equal_times_raise(self):
        with self.assertRaises(ValidationError):
            TimeSlot(start_time="10:00", end_time="10:00")


class TestAvailabilityParserMocked(unittest.IsolatedAsyncioTestCase):
    """Изолированные unit-тесты логики парсера с использованием моков."""

    def setUp(self):
        # Создаём парсер с фиктивным ключом, чтобы не трогать реальный API
        self.parser = AvailabilityParser(api_key="gsk_mock_test_key_for_unit_tests")

    def _make_mock_response(self, content_dict: dict) -> MagicMock:
        """Собирает mock-объект ответа API из словаря."""
        mock_choice = MagicMock()
        mock_choice.message.content = json.dumps(content_dict)
        mock_api_res = MagicMock()
        mock_api_res.choices = [mock_choice]
        return mock_api_res

    async def test_empty_input_returns_clarification(self):
        """Пустая строка возвращает clarification_needed без обращения к сети."""
        result = await self.parser.parse_availability("   ")
        self.assertEqual(result.status, "clarification_needed")
        self.assertIn("пустое сообщение", result.clarification_message)
        self.assertFalse(result.fallback_to_manual)

    async def test_mock_success_parsing(self):
        """Корректный ответ от LLM парсится в структуру ParsingResult."""
        mock_response_data = {
            "status": "success",
            "summary_ru": "Пользователь свободен по понедельникам и вторникам с 18:00 до 21:00, кроме среды.",
            "recurring_slots": [
                {
                    "days": ["monday", "tuesday"],
                    "time": {"start_time": "18:00", "end_time": "21:00"},
                    "description": "по будням"
                }
            ],
            "exceptions": [
                {
                    "date": "wednesday",
                    "time": {"start_time": "18:00", "end_time": "21:00"},
                    "is_available": False,
                    "description": "кроме среды"
                }
            ],
            "clarification_message": None
        }

        self.parser.client.chat.completions.create = AsyncMock(
            return_value=self._make_mock_response(mock_response_data)
        )

        input_text = "По будням свободен с 18 до 21, кроме среды"
        result = await self.parser.parse_availability(input_text)
        self.assertEqual(result.status, "success")
        # raw_text должен совпадать с исходным вводом — пользователь видит его при подтверждении
        self.assertEqual(result.raw_text, input_text)
        self.assertEqual(len(result.recurring_slots), 1)
        self.assertEqual(result.recurring_slots[0].time.start_time, "18:00")
        self.assertEqual(len(result.exceptions), 1)
        self.assertFalse(result.exceptions[0].is_available)
        # summary_ru обязателен при success и не должен быть пустой строкой
        self.assertIsNotNone(result.summary_ru)
        self.assertNotEqual(result.summary_ru, "")
        self.assertFalse(result.fallback_to_manual)

    async def test_mock_clarification_needed(self):
        """Неоднозначный ответ переходит в статус clarification_needed."""
        mock_response_data = {
            "status": "clarification_needed",
            "summary_ru": None,
            "recurring_slots": [],
            "exceptions": [],
            "clarification_message": "Уточните конкретное время встречи."
        }

        self.parser.client.chat.completions.create = AsyncMock(
            return_value=self._make_mock_response(mock_response_data)
        )

        result = await self.parser.parse_availability("Давай как-нибудь на неделе")
        self.assertEqual(result.status, "clarification_needed")
        self.assertEqual(result.clarification_message, "Уточните конкретное время встречи.")
        # По контракту ТЗ: summary_ru должен быть null при clarification_needed
        self.assertIsNone(result.summary_ru)
        self.assertFalse(result.fallback_to_manual)

    async def test_mock_rate_limit_sets_fallback(self):
        """Ошибка 429 RateLimitError выставляет fallback_to_manual=True."""
        self.parser.client.chat.completions.create = AsyncMock(
            side_effect=RateLimitError(
                message="Rate limit exceeded",
                response=MagicMock(status_code=429),
                body={},
            )
        )

        result = await self.parser.parse_availability("Свободен завтра")
        self.assertEqual(result.status, "error")
        self.assertTrue(result.fallback_to_manual)
        self.assertIn("перегружен", result.error_message)

    async def test_mock_groq_api_error_handling(self):
        """Общий GroqError возвращает status='error' без fallback_to_manual."""
        self.parser.client.chat.completions.create = AsyncMock(
            side_effect=GroqError("Simulated API Connection Error")
        )

        result = await self.parser.parse_availability("Свободен завтра")
        self.assertEqual(result.status, "error")
        self.assertFalse(result.fallback_to_manual)
        self.assertIn("Ошибка сервиса", result.error_message)

    async def test_mock_invalid_json_handling(self):
        """Некорректный JSON от модели не ломает приложение."""
        # Намеренно строим мок вручную: передаём сырую невалидную строку,
        # а не dict — _make_mock_response здесь не подходит.
        mock_choice = MagicMock()
        mock_choice.message.content = "Not a valid JSON object"
        mock_api_res = MagicMock()
        mock_api_res.choices = [mock_choice]

        self.parser.client.chat.completions.create = AsyncMock(return_value=mock_api_res)

        result = await self.parser.parse_availability("Свободен завтра")
        self.assertEqual(result.status, "error")
        self.assertIn("Не удалось обработать ответ модели", result.error_message)

    async def test_mock_empty_choices_returns_clarification(self):
        """Пустой список choices возвращает clarification_needed без краша."""
        mock_api_res = MagicMock()
        mock_api_res.choices = []

        self.parser.client.chat.completions.create = AsyncMock(return_value=mock_api_res)

        result = await self.parser.parse_availability("Свободен в пятницу")
        self.assertEqual(result.status, "clarification_needed")
        # Пользователь должен получить понятный текст — проверяем, что сообщение не пустое
        self.assertIsNotNone(result.clarification_message)
        self.assertIn("Сервис не вернул ответ", result.clarification_message)

    async def test_missing_status_defaults_to_clarification(self):
        """Отсутствие поля status в ответе модели → clarification_needed (не success)."""
        mock_response_data = {
            "recurring_slots": [],
            "exceptions": [],
            "clarification_message": None
        }

        self.parser.client.chat.completions.create = AsyncMock(
            return_value=self._make_mock_response(mock_response_data)
        )

        result = await self.parser.parse_availability("Ладно")
        self.assertEqual(result.status, "clarification_needed")
        # Отсутствие поля status — это неполный ответ модели, а не техническая ошибка
        self.assertFalse(result.fallback_to_manual)

    async def test_mock_invalid_schema_sets_fallback(self):
        """Модель вернула JSON с невалидным TimeSlot → pydantic.ValidationError → error + fallback_to_manual=True."""
        # start_time="99:00" не пройдёт validate_time_format в TimeSlot
        mock_response_data = {
            "status": "success",
            "recurring_slots": [
                {"days": ["monday"], "time": {"start_time": "99:00", "end_time": "21:00"}}
            ],
            "exceptions": [],
            "clarification_message": None,
        }

        self.parser.client.chat.completions.create = AsyncMock(
            return_value=self._make_mock_response(mock_response_data)
        )

        result = await self.parser.parse_availability("В понедельник с 99:00")
        self.assertEqual(result.status, "error")
        # pydantic.ValidationError → fallback_to_manual=True, ручной ввод обязателен
        self.assertTrue(result.fallback_to_manual)
        self.assertIsNotNone(result.error_message)


async def run_live_api_health_check() -> bool:
    """Функция живого (live) вызова API для проверки валидности GROQ_API_KEY и серверов.

    Не является unit-тестом — запускается вручную через main().
    Не начинается с 'test_' — чтобы pytest не подхватывал её как unit-тест.
    """
    print("\n--- Запуск Live API Health Check ---")
    try:
        live_parser = AvailabilityParser()
        print(f"Используется модель: {live_parser.model}")

        test_phrase = "В пятницу свободен с 15:00 до 18:00"
        print(f"Отправка тестового запроса: '{test_phrase}'...")

        result = await asyncio.wait_for(
            live_parser.parse_availability(test_phrase),
            timeout=30.0,
        )

        if result.status == "success":
            print("✅ Live API Check пройден успешно!")
            print(f"Статус={result.status}, слотов={len(result.recurring_slots)}, исключений={len(result.exceptions)}")
            print(f"summary_ru: {result.summary_ru}")
            return True
        else:
            print(f"⚠️ Live API вернул статус: {result.status}. Ошибка: {result.error_message}")
            return False

    except asyncio.TimeoutError:
        print("❌ Live API Check: превышен таймаут 30 секунд")
        return False
    except Exception as exc:
        print(f"❌ Сбой Live API Check: {exc}")
        return False


def main() -> None:
    # 1. Запуск мок-тестов через unittest
    print("=" * 60)
    print("1. ЗАПУСК ИЗОЛИРОВАННЫХ UNIT-ТЕСТОВ (МОКИ)")
    print("=" * 60)
    loader = unittest.defaultTestLoader
    suite = unittest.TestSuite()
    suite.addTests(loader.loadTestsFromTestCase(TestTimeSlotValidation))
    suite.addTests(loader.loadTestsFromTestCase(TestAvailabilityParserMocked))
    runner = unittest.TextTestRunner(verbosity=2)
    test_result = runner.run(suite)

    if not test_result.wasSuccessful():
        print("❌ Ошибка в unit-тестах!")
        sys.exit(1)

    # 2. Запуск живого теста к реальному API
    print("\n" + "=" * 60)
    print("2. ЗАПУСК ЖИВОГО ТЕСТА (LIVE API CHECK)")
    print("=" * 60)
    live_ok = asyncio.run(run_live_api_health_check())
    if not live_ok:
        sys.exit(1)

    print("\nВсе проверки успешно пройдены!")


if __name__ == "__main__":
    main()
