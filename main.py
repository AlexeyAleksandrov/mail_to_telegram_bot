import imaplib
import email
from email.header import decode_header
import time
import telebot
import os
import logging
import json
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
    level=logging.INFO,
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
    """Удаляет лишние пробелы и переносы строк из текста."""
    if text:
        return " ".join(text.split())
    return ""


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


def get_email_body(msg):
    """Извлекает текстовую часть тела письма."""
    body = ""
    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            content_disposition = str(part.get("Content-Disposition"))
            # Пропускаем вложения
            if "attachment" in content_disposition:
                continue
            if content_type == "text/plain":
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or 'utf-8'
                    try:
                        body += payload.decode(charset, errors='replace')
                    except:
                        body += payload.decode('utf-8', errors='replace')
    else:
        # Письмо не multipart, просто берем payload
        payload = msg.get_payload(decode=True)
        if payload:
            charset = msg.get_content_charset() or 'utf-8'
            try:
                body = payload.decode(charset, errors='replace')
            except:
                body = payload.decode('utf-8', errors='replace')
    return clean_text(body)


def escape_markdown(text):
    """Экранирует специальные символы Markdown для Telegram."""
    if not text:
        return ""
    escape_chars = ['_', '*', '[', ']', '(', ')', '~', '`', '>', '#', '+', '-', '=', '|', '{', '}', '.', '!']
    for char in escape_chars:
        text = text.replace(char, '\\' + char)
    return text


def check_new_emails_and_notify():
    """Основная функция: проверяет почту и отправляет уведомления."""
    try:
        # Подключаемся к серверу
        mail = imaplib.IMAP4_SSL(IMAP_SERVER, IMAP_PORT)
        mail.login(EMAIL_ACCOUNT, EMAIL_PASSWORD)
        mail.select("INBOX")  # Выбираем папку "Входящие"

        # Загружаем уже обработанные письма
        processed_emails = load_processed_emails()
        logger.info(f"Загружено {len(processed_emails)} обработанных писем")

        # Ищем непрочитанные письма
        status, messages = mail.search(None, "UNSEEN")
        if status != "OK":
            logger.error("Ошибка при поиске писем")
            return

        email_ids = messages[0].split()
        bot = telebot.TeleBot(BOT_TOKEN)

        logger.info(f"Найдено непрочитанных писем: {len(email_ids)}")

        # Обрабатываем каждое новое письмо
        for e_id in email_ids:
            try:
                # Используем BODY.PEEK вместо FETCH чтобы не помечать письма как прочитанные
                status, msg_data = mail.fetch(e_id, "(BODY.PEEK[])")
                if status != "OK":
                    logger.error(f"Ошибка при получении письма {e_id}")
                    continue

                msg = email.message_from_bytes(msg_data[0][1])

                # Создаем уникальный идентификатор письма (UID + дата + отправитель)
                message_id = msg.get('Message-ID', '')
                date = msg.get('Date', '')
                from_ = msg.get('From', '')

                # Если нет Message-ID, создаем свой на основе содержимого
                if not message_id:
                    message_id = f"{e_id.decode()}_{date}_{from_}"

                # Проверяем, не обрабатывали ли мы уже это письмо
                if message_id in processed_emails:
                    logger.info(f"Письмо {message_id} уже было обработано, пропускаем")
                    continue

                subject = decode_mime_words(msg["Subject"])
                from_ = decode_mime_words(msg["From"])
                to_ = decode_mime_words(msg["To"])
                date_ = msg["Date"]

                # Если темы нет, устанавливаем значение по умолчанию
                if not subject:
                    subject = "(Без темы)"

                body = get_email_body(msg)

                # Экранируем специальные символы Markdown
                subject_escaped = escape_markdown(subject)
                from_escaped = escape_markdown(from_)
                to_escaped = escape_markdown(to_)
                date_escaped = escape_markdown(date_)
                body_escaped = escape_markdown(body)

                # Формируем сообщение для Telegram
                telegram_message = (
                    f"📨 *Новое письмо*\n\n"
                    f"*От:* {from_escaped}\n"
                    f"*Кому:* {to_escaped}\n"
                    f"*Дата:* {date_escaped}\n"
                    f"*Тема:* {subject_escaped}\n\n"
                    f"*Содержимое:*\n{body_escaped[:1000]}"  # Ограничиваем длину сообщения
                )

                # Отправляем сообщение в Telegram
                bot.send_message(CHAT_ID, telegram_message, parse_mode="Markdown")
                logger.info(f"Уведомление отправлено для письма ID: {e_id.decode()} от {from_}")

                # Сохраняем информацию об обработанном письме
                save_processed_email(message_id)
                logger.info(f"Письмо {message_id} добавлено в обработанные")

            except Exception as e:
                logger.error(f"Ошибка при обработке письма {e_id}: {str(e)}")
                # Попробуем отправить без форматирования Markdown в случае ошибки
                try:
                    # ... (код для отправки без форматирования из предыдущего ответа)
                    pass
                except Exception as e2:
                    logger.error(f"Не удалось отправить письмо {e_id} даже без форматирования: {str(e2)}")

        mail.close()
        mail.logout()

    except Exception as e:
        logger.error(f"Общая ошибка: {str(e)}")


# ====== ЗАПУСК ======
if __name__ == "__main__":
    # Проверяем обязательные переменные окружения
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