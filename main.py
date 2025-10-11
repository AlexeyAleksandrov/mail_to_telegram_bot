import imaplib
import email
from email.header import decode_header
import time
import telebot
import os
import logging
import json
import re
import html
from dotenv import load_dotenv

# Загружаем переменные окружения
load_dotenv()

# ====== КОНФИГУРАЦИЯ ИЗ ПЕРЕМЕННЫХ ОКРУЖЕНИЯ ======
IMAP_SERVER = os.getenv('IMAP_SERVER', 'imap.yandex.ru')
IMAP_PORT = int(os.getenv('IMAP_PORT', '993'))
EMAIL_ACCOUNT = os.getenv('EMAIL_ACCOUNT')
EMAIL_PASSWORD = os.getenv('EMAIL_PASSWORD')
BOT_TOKEN = os.getenv('BOT_TOKEN')
CHAT_ID = os.getenv('CHAT_ID')
CHECK_INTERVAL = int(os.getenv('CHECK_INTERVAL', '300'))

# ====== НАСТРОЙКА ЛОГГИРОВАНИЯ ======
logging.basicConfig(
    level=logging.Info,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# ====== ФУНКЦИИ ДЛЯ СОХРАНЕНИЯ СОСТОЯНИЯ ======
PROCESSED_EMAILS_FILE = 'data/processed_emails.json'


def load_processed_emails():
    """Загружает множество уже обработанных UID писем."""
    try:
        if os.path.exists(PROCESSED_EMAILS_FILE):
            with open(PROCESSED_EMAILS_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                return set(data.get('processed_uids', []))
    except Exception as e:
        logger.error(f"Ошибка при загрузке обработанных писем: {e}")
    return set()


def save_processed_email(uid):
    """Сохраняет UID письма в файл."""
    try:
        processed_uids = load_processed_emails()
        processed_uids.add(uid)

        data = {
            'processed_uids': list(processed_uids),
            'last_updated': time.time()
        }

        with open(PROCESSED_EMAILS_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Ошибка при сохранении обработанного письма: {e}")


def clean_text(text):
    """
    Убирает невидимые символы и огромные пробелы, но сохраняет весь текст.
    """
    if not text:
        return ""

    # Удаляем невидимые символы (Braille, нулевые пробелы и т.д.)
    text = re.sub(r'[\u2800\u200b\u200c\u200d\u200e\u200f\ufeff\x00]', '', text)

    # Заменяем неразрывные пробелы и табы на обычные пробелы
    text = re.sub(r'[\t\u00A0]', ' ', text)

    # Убираем множественные пробелы (но оставляем одиночные)
    text = re.sub(r' {2,}', ' ', text)

    # Убираем множественные пустые строки (оставляем максимум 2 подряд)
    text = re.sub(r'\n\s*\n', '\n\n', text)

    # Убираем пробелы в начале и конце строк
    lines = [line.strip() for line in text.split('\n')]
    text = '\n'.join(lines)

    # Убираем пустые строки в начале и конце
    text = text.strip()

    return text


def decode_mime_words(encoded_str):
    """Корректно декодирует строки в MIME-формате (например, тему письма)."""
    if encoded_str is None:
        return ""
    decoded_parts = decode_header(encoded_str)
    decoded_str = ""
    for part, encoding in decoded_parts:
        if isinstance(part, bytes):
            if encoding:
                decoded_str += part.decode(encoding)
            else:
                decoded_str += part.decode('utf-8', errors='ignore')
        else:
            decoded_str += part
    return decoded_str


def html_to_text(html_content):
    """Преобразует HTML в чистый текст, сохраняя всё содержимое."""
    if not html_content:
        return ""

    # Удаляем HTML-теги, но сохраняем текст между ними
    text = re.sub(r'<[^>]+>', ' ', html_content)

    # Декодируем HTML-сущности
    text = html.unescape(text)

    # Очищаем текст от мусора, но сохраняем содержимое
    text = clean_text(text)

    return text


def get_email_body(msg):
    """Извлекает текстовую часть тела письма, отдавая предпочтение plain text над HTML."""
    plain_text = ""
    html_text = ""

    if msg.is_multipart():
        # Обрабатываем multipart сообщение
        for part in msg.walk():
            content_type = part.get_content_type()
            content_disposition = str(part.get("Content-Disposition"))

            # Пропускаем вложения
            if "attachment" in content_disposition:
                continue

            # Получаем содержимое части
            payload = part.get_payload(decode=True)
            if payload:
                charset = part.get_content_charset() or 'utf-8'
                try:
                    decoded_payload = payload.decode(charset, errors='replace')
                except:
                    decoded_payload = payload.decode('utf-8', errors='replace')

                if content_type == "text/plain":
                    plain_text = decoded_payload
                elif content_type == "text/html" and not plain_text:
                    html_text = decoded_payload
    else:
        # Обрабатываем простое сообщение
        content_type = msg.get_content_type()
        payload = msg.get_payload(decode=True)
        if payload:
            charset = msg.get_content_charset() or 'utf-8'
            try:
                decoded_payload = payload.decode(charset, errors='replace')
            except:
                decoded_payload = payload.decode('utf-8', errors='replace')

            if content_type == "text/plain":
                plain_text = decoded_payload
            elif content_type == "text/html":
                html_text = decoded_payload

    # Отдаем предпочтение plain text
    if plain_text:
        return clean_text(plain_text)
    elif html_text:
        # Конвертируем HTML в текст
        return html_to_text(html_text)
    else:
        return ""


def escape_markdown(text):
    """Экранирует специальные символы Markdown для Telegram, но не дефисы."""
    if not text:
        return ""
    # Экранируем только действительно проблемные символы, но не дефисы
    escape_chars = ['_', '*', '[', ']', '(', ')', '~', '`', '>', '#', '+', '=', '|', '{', '}']
    for char in escape_chars:
        text = text.replace(char, '\\' + char)
    return text


def truncate_text(text, max_length=3000):
    """Обрезает текст до максимальной длины, стараясь не обрезать слова."""
    if len(text) <= max_length:
        return text

    # Обрезаем до максимальной длины
    truncated = text[:max_length]

    # Пытаемся обрезать до последнего пробела
    last_space = truncated.rfind(' ')
    if last_space > max_length * 0.8:  # Если есть подходящее место для обрезки
        return truncated[:last_space] + "..."
    else:
        return truncated + "..."


def check_new_emails_and_notify():
    """Основная функция: проверяет почту и отправляет уведомления."""
    try:
        # Подключаемся к серверу
        mail = imaplib.IMAP4_SSL(IMAP_SERVER, IMAP_PORT)
        mail.login(EMAIL_ACCOUNT, EMAIL_PASSWORD)
        mail.select("INBOX")

        processed_emails = load_processed_emails()
        logger.info(f"Загружено {len(processed_emails)} обработанных писем")

        status, messages = mail.search(None, "UNSEEN")
        if status != "OK":
            logger.error("Ошибка при поиске писем")
            return

        email_ids = messages[0].split()
        bot = telebot.TeleBot(BOT_TOKEN)

        logger.info(f"Найдено непрочитанных писем: {len(email_ids)}")

        for e_id in email_ids:
            try:
                status, msg_data = mail.fetch(e_id, "(BODY.PEEK[])")
                if status != "OK":
                    logger.error(f"Ошибка при получении письма {e_id}")
                    continue

                msg = email.message_from_bytes(msg_data[0][1])

                message_id = msg.get('Message-ID', '')
                date = msg.get('Date', '')
                from_ = msg.get('From', '')

                if not message_id:
                    message_id = f"{e_id.decode()}_{date}_{from_}"

                if message_id in processed_emails:
                    logger.info(f"Письмо {message_id} уже было обработано, пропускаем")
                    continue

                subject = decode_mime_words(msg["Subject"])
                from_ = decode_mime_words(msg["From"])
                to_ = decode_mime_words(msg["To"])
                date_ = msg["Date"]

                if not subject:
                    subject = "(Без темы)"

                body = get_email_body(msg)

                # Обрезаем тело письма до разумной длины
                body_truncated = truncate_text(body, 3000)

                # Экранируем специальные символы Markdown (кроме дефисов)
                subject_escaped = escape_markdown(subject)
                from_escaped = escape_markdown(from_)
                to_escaped = escape_markdown(to_)
                date_escaped = escape_markdown(date_)
                body_escaped = escape_markdown(body_truncated)

                # Формируем сообщение для Telegram
                telegram_message = (
                    f"📨 *Новое письмо*\n\n"
                    f"*От:* {from_escaped}\n"
                    f"*Кому:* {to_escaped}\n"
                    f"*Дата:* {date_escaped}\n"
                    f"*Тема:* {subject_escaped}\n\n"
                )

                # Добавляем содержимое только если оно есть
                if body_escaped.strip():
                    telegram_message += f"*Содержимое:*\n{body_escaped}"
                else:
                    telegram_message += "*Содержимое:* (пустое письмо или только вложения)"

                # Отправляем сообщение в Telegram
                bot.send_message(CHAT_ID, telegram_message, parse_mode="Markdown")
                logger.info(f"Уведомление отправлено для письма ID: {e_id.decode()} от {from_}")

                # Сохраняем информацию о обработанном письме
                save_processed_email(message_id)
                logger.info(f"Письмо {message_id} добавлено в обработанные")

            except Exception as e:
                logger.error(f"Ошибка при обработке письма {e_id}: {str(e)}")
                try:
                    simple_message = (
                        f"📨 Новое письмо\n\n"
                        f"От: {from_}\n"
                        f"Кому: {to_}\n"
                        f"Дата: {date_}\n"
                        f"Тема: {subject}\n\n"
                    )
                    if body.strip():
                        simple_message += f"Содержимое:\n{body_truncated}"
                    else:
                        simple_message += "Содержимое: (пустое письмо или только вложения)"

                    bot.send_message(CHAT_ID, simple_message, parse_mode=None)
                    logger.info(f"Письмо {e_id} отправлено без форматирования Markdown")
                    save_processed_email(message_id)
                except Exception as e2:
                    logger.error(f"Не удалось отправить письмо {e_id} даже без форматирования: {str(e2)}")

        mail.close()
        mail.logout()

    except Exception as e:
        logger.error(f"Общая ошибка: {str(e)}")


# ====== ЗАПУСК ======
if __name__ == "__main__":
    required_vars = ['EMAIL_ACCOUNT', 'EMAIL_PASSWORD', 'BOT_TOKEN', 'CHAT_ID']
    missing_vars = [var for var in required_vars if not os.getenv(var)]

    if missing_vars:
        logger.error(f"Отсутствуют обязательные переменные окружения: {', '.join(missing_vars)}")
        exit(1)

    logger.info("Бот для проверки почты запущен...")
    logger.info(f"Интервал проверки: {CHECK_INTERVAL} секунд")

    while True:
        check_new_emails_and_notify()
        time.sleep(CHECK_INTERVAL)