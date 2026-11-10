import re
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator


DayOfWeek = Literal[
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
]

ParsingStatus = Literal["success", "clarification_needed", "error"]

_TIME_RE = re.compile(r"^\d{2}:\d{2}$")


class TimeSlot(BaseModel):
    """Временной интервал."""
    start_time: str = Field(description="Время начала в формате ЧЧ:ММ (24ч)")
    end_time: str = Field(description="Время окончания в формате ЧЧ:ММ (24ч)")

    @field_validator("start_time", "end_time")
    @classmethod
    def validate_time_format(cls, v: str) -> str:
        if not _TIME_RE.match(v):
            raise ValueError(f"Время '{v}' не соответствует формату ЧЧ:ММ")
        hours, minutes = int(v[:2]), int(v[3:])
        if not (0 <= hours <= 23 and 0 <= minutes <= 59):
            raise ValueError(f"Время '{v}' вне допустимого диапазона (00:00–23:59)")
        return v

    @model_validator(mode="after")
    def validate_start_before_end(self) -> "TimeSlot":
        # Известное ограничение: переходы через полночь (например, 22:00–02:00)
        # не поддерживаются и отсекаются здесь с ValueError.
        # На уровне промпта модель получает инструкцию возвращать clarification_needed
        # в таких случаях, но если она всё же вернёт некорректный слот —
        # ValidationError поднимется до parser.py и будет обработан как status="error".
        if self.start_time >= self.end_time:
            raise ValueError(
                f"start_time ({self.start_time}) должно быть раньше end_time ({self.end_time})"
            )
        return self



class RecurringSlot(BaseModel):
    """Повторяющийся шаблон доступности (например, по будням)."""
    days: list[DayOfWeek] = Field(description="Список дней недели")
    time: TimeSlot = Field(description="Интервал времени")
    description: Optional[str] = Field(default=None, description="Дополнительное пояснение")


class ExceptionSlot(BaseModel):
    """Исключение из расписания на конкретную дату или день."""
    date: Optional[str] = Field(default=None, description="Дата (ГГГГ-ММ-ДД) или указание дня")
    time: Optional[TimeSlot] = Field(default=None, description="Интервал исключения (если указан)")
    is_available: bool = Field(description="True — свободен в это время, False — занят")
    description: Optional[str] = Field(default=None, description="Причина или пояснение исключения")


class ParsingResult(BaseModel):
    """Итоговый результат структурирования доступности."""
    status: ParsingStatus = Field(description="Статус разбора: success, clarification_needed или error")
    raw_text: str = Field(description="Исходный текст пользователя")
    recurring_slots: list[RecurringSlot] = Field(
        default_factory=list,
        description="Список повторяющихся шаблонов"
    )
    exceptions: list[ExceptionSlot] = Field(
        default_factory=list,
        description="Список исключений (занятости или разовых слотов)"
    )
    summary_ru: Optional[str] = Field(
        default=None,
        description="Краткое человекочитаемое резюме извлечённого расписания (для подтверждения пользователем)"
    )
    clarification_message: Optional[str] = Field(
        default=None,
        description="Вопрос пользователю для уточнения при неоднозначности"
    )
    fallback_to_manual: bool = Field(
        default=False,
        description="True, если автоматический разбор недоступен и требуется ручной ввод"
    )
    error_message: Optional[str] = Field(
        default=None,
        description="Описание технической ошибки при сбое"
    )
