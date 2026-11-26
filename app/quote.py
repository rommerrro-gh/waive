import logging
import os
import re
import smtplib
import ssl
import time
from collections import deque
from email.message import EmailMessage
from threading import Lock
from typing import Annotated

from fastapi import APIRouter, HTTPException, Request
from psycopg import OperationalError
from psycopg_pool import PoolTimeout
from pydantic import BaseModel, Field, StringConstraints, field_validator

router = APIRouter(prefix="/quote")
logger = logging.getLogger(__name__)
attempts = deque()
attempt_lock = Lock()

Contact = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
EMAIL = re.compile(r"^[^\s\x00-\x1f\x7f<>@,;]+@[^\s\x00-\x1f\x7f<>@,;.]+(?:\.[^\s\x00-\x1f\x7f<>@,;.]+)+$")


class QuoteItem(BaseModel):
    offer_id: Annotated[str, StringConstraints(min_length=1, max_length=100)]
    workplaces: Annotated[int | None, Field(ge=1, le=1_000_000)] = None


class QuoteRequest(BaseModel):
    name: Contact
    email: Annotated[str, StringConstraints(strip_whitespace=True, max_length=254)]
    phone: Annotated[str, StringConstraints(strip_whitespace=True, max_length=30)]
    company: Contact
    items: Annotated[list[QuoteItem], Field(min_length=1, max_length=30)]

    @field_validator("name", "company")
    @classmethod
    def contact_line(cls, value):
        if any(ord(char) < 32 for char in value):
            raise ValueError("Укажите значение одной строкой")
        return value

    @field_validator("email")
    @classmethod
    def email_address(cls, value):
        if not EMAIL.fullmatch(value):
            raise ValueError("Укажите корректный email")
        return value

    @field_validator("phone")
    @classmethod
    def phone_number(cls, value):
        if not re.fullmatch(r"\+?[\d() .-]+", value) or not 7 <= sum(c.isdigit() for c in value) <= 15:
            raise ValueError("Укажите корректный телефон")
        return value


def mail_enabled():
    host, sender, recipient = (os.getenv(key, "") for key in ("SMTP_HOST", "SMTP_FROM", "QUOTE_RECIPIENT"))
    return bool(host and EMAIL.fullmatch(sender) and EMAIL.fullmatch(recipient))


@router.get("/config")
def quote_config():
    recipient = os.getenv("QUOTE_RECIPIENT", "")
    return {"automatic_delivery": mail_enabled(), "recipient": recipient if EMAIL.fullmatch(recipient) else ""}


def make_message(payload, offers):
    message = EmailMessage()
    message["From"] = os.environ["SMTP_FROM"]
    message["To"] = os.environ["QUOTE_RECIPIENT"]
    message["Reply-To"] = payload.email
    message["Subject"] = "Waive -- запрос коммерческого предложения"
    lines = [
        "Просим подготовить коммерческое предложение.",
        f"Имя: {payload.name}", f"Компания: {payload.company}",
        f"Email: {payload.email}", f"Телефон: {payload.phone}", "",
    ]
    for position, entry in enumerate(payload.items, 1):
        offer = offers[entry.offer_id]
        lines.append(f'{position}. {offer["name"]} (арт. {offer["vendor_code"] or entry.offer_id})')
        if entry.workplaces:
            lines.append(f"   Подтвердить покрытие {entry.workplaces} рабочих мест.")
        if offer["url"]:
            lines.append(f'   {offer["url"]}')
    message.set_content("\n".join(lines))
    return message


def deliver(message):
    host = os.environ["SMTP_HOST"]
    port = int(os.getenv("SMTP_PORT", "587"))
    tls = ssl.create_default_context()
    if port == 465:
        smtp = smtplib.SMTP_SSL(host, port, timeout=8, context=tls)
    else:
        smtp = smtplib.SMTP(host, port, timeout=8)
    with smtp:
        if port != 465:
            smtp.starttls(context=tls)
        if username := os.getenv("SMTP_USERNAME"):
            smtp.login(username, os.environ["SMTP_PASSWORD"])
        smtp.send_message(message)


@router.post("/request")
def send_quote(payload: QuoteRequest, request: Request):
    if not mail_enabled():
        raise HTTPException(503, "Автоматическая отправка почты пока не настроена")
    ids = list(dict.fromkeys(item.offer_id for item in payload.items))
    if len(ids) != len(payload.items):
        raise HTTPException(422, "Уберите повторяющиеся позиции из запроса")
    with attempt_lock:
        now = time.monotonic()
        while attempts and now - attempts[0] >= 60:
            attempts.popleft()
        if len(attempts) >= 6:
            raise HTTPException(429, "Слишком много запросов. Попробуйте через минуту")
        attempts.append(now)
    try:
        with request.app.state.pool.connection() as connection:
            rows = connection.execute("""
                SELECT o.id, o.name, o.vendor_code, o.url
                FROM offer o JOIN active_catalog a ON a.snapshot_id = o.snapshot_id
                WHERE o.id = ANY(%s)
            """, (ids,)).fetchall()
        offers = {row["id"]: row for row in rows}
        if len(offers) != len(ids):
            raise HTTPException(422, "Часть позиций устарела. Обновите список товаров")
        deliver(make_message(payload, offers))
    except (OperationalError, PoolTimeout):
        raise HTTPException(503, "Каталог временно недоступен") from None
    except (smtplib.SMTPException, OSError, ValueError, KeyError):
        logger.warning("Не удалось отправить запрос КП через SMTP")
        raise HTTPException(502, "Почта не приняла запрос. Можно открыть черновик в почтовом приложении") from None
    return {"sent": True}
