import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

import pydantic
from groq import AsyncGroq, GroqError, RateLimitError

from meetsync_llm.prompts import SYSTEM_PROMPT
from meetsync_llm.schemas import ParsingResult

logger = logging.getLogger(__name__)

_MOSCOW_TZ = ZoneInfo("Europe/Moscow")
_WEEKDAYS_EN = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

def load_env_file(filepath: Path) -> None:
    """Загрузка переменных из .env без внешних зависимостей."""
    if not filepath.exists():
        return
    with open(filepath, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip().strip("'\"")
                if key not in os.environ:
                    os.environ[key] = value


class AvailabilityParser:
    """Модуль преобразования текста о доступности в структурированные данные через Groq Cloud."""

    DEFAULT_MODEL = "qwen/qwen3.8-27b"
    DEFAULT_TEMPERATURE = 0.1

    def __init__(self, api_key: Optional[str] = None, model: str = DEFAULT_MODEL):
        self.model = model

        # Поиск ключа: параметр -> окружение -> файл .env проекта
        if not api_key:
            env_path = Path(__file__).resolve().parent.parent.parent / ".env"
            load_env_file(env_path)
            api_key = os.environ.get("GROQ_API_KEY")

        if not api_key:
            logger.error("Ключ GROQ_API_KEY не обнаружен")
            raise ValueError("Не найден GROQ_API_KEY ни в параметрах, ни в переменных окружения, ни в .env")

        self.client = AsyncGroq(api_key=api_key)

    def _build_user_message(self, text: str) -> str:
        """Добавляет контекст текущей даты и таймзоны к тексту пользователя."""
       
        now = datetime.now(_MOSCOW_TZ)
        # Явный маппинг вместо strftime("%A"): %A зависит от системной локали ОС,
        # что даёт неанглийский результат на Windows с русской локалью.
        weekday_en = _WEEKDAYS_EN[now.weekday()]
        date_context = f"{now.strftime('%Y-%m-%d')}, {weekday_en}"
        return f"[Текущая дата (Europe/Moscow): {date_context}]\n{text}"

    async def parse_availability(
        self, text: str, history: list[dict] | None = None
    ) -> ParsingResult:
        """Асинхронный разбор текста доступности."""
        text_clean = text.strip()
        if not text_clean:
            return ParsingResult(
                status="clarification_needed",
                raw_text=text,
                clarification_message="Вы отправили пустое сообщение. Укажите, в какие дни и часы вы свободны."
            )

        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    *(history or []),
                    {"role": "user", "content": self._build_user_message(text_clean)},
                ],
                response_format={"type": "json_object"},
                temperature=self.DEFAULT_TEMPERATURE,
            )

            if not response.choices:
                logger.warning("Groq API вернул пустой список choices")
                return ParsingResult(
                    status="clarification_needed",
                    raw_text=text,
                    clarification_message="Сервис не вернул ответ. Попробуйте переформулировать запрос."
                )

            raw_content = response.choices[0].message.content or "{}"
            parsed_json = json.loads(raw_content)

            # Дефолт — clarification_needed, чтобы не считать пустой ответ успехом
            status = parsed_json.get("status", "clarification_needed")

            return ParsingResult(
                status=status,
                raw_text=text,
                recurring_slots=parsed_json.get("recurring_slots", []),
                exceptions=parsed_json.get("exceptions", []),
                summary_ru=parsed_json.get("summary_ru"),
                clarification_message=parsed_json.get("clarification_message"),
            )

        except RateLimitError as exc:
            logger.warning("Groq API: превышен лимит запросов (429): %s", exc)
            return ParsingResult(
                status="error",
                raw_text=text,
                fallback_to_manual=True,
                error_message="Сервис временно перегружен. Пожалуйста, введите расписание вручную.",
            )

        except pydantic.ValidationError as exc:
            logger.error("Ответ модели не прошёл валидацию схемы: %s", exc, exc_info=True)
            return ParsingResult(
                status="error",
                raw_text=text,
                # Модель вернула данные, не прошедшие схему (например, некорректный формат времени).
                # Автоматический разбор невозможен — предлагаем ручной ввод.
                fallback_to_manual=True,
                error_message="Ответ сервиса содержит некорректные данные. Попробуйте повторить запрос или введите расписание вручную.",
            )

        except GroqError as exc:
            logger.error("Ошибка при обращении к Groq API: %s", exc, exc_info=True)
            return ParsingResult(
                status="error",
                raw_text=text,
                error_message="Ошибка сервиса распознавания текста (Groq API). Попробуйте позже.",
            )

        except json.JSONDecodeError as exc:
            # При response_format={"type": "json_object"} Groq SDK гарантирует валидный JSON.
            # Этот блок защищает от потенциального сбоя самого SDK или неожиданного изменения API.
            logger.error("Ошибка декодирования ответа модели (невалидный JSON): %s", exc, exc_info=True)
            return ParsingResult(
                status="error",
                raw_text=text,
                error_message="Не удалось обработать ответ модели. Пожалуйста, повторите запрос.",
            )

        except Exception as exc:
            logger.error("Непредвиденная системная ошибка: %s", exc, exc_info=True)
            return ParsingResult(
                status="error",
                raw_text=text,
                error_message="Внутренняя ошибка сервиса при разборе текста.",
            )
