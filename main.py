import imaplib
import email
from email.header import decode_header
import time
import telebot
import os
import logging
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
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('logs/bot.log', encoding='utf-8')
    ]
)
logger = logging.getLogger(__name__)


# ====== ФУНКЦИИ ======
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


def check_new_emails_and_notify():
    """Основная функция: проверяет почту и отправляет уведомления."""
    try:
        # Подключаемся к серверу
        mail = imaplib.IMAP4_SSL(IMAP_SERVER, IMAP_PORT)
        mail.login(EMAIL_ACCOUNT, EMAIL_PASSWORD)
        mail.select("INBOX")  # Выбираем папку "Входящие"

        # Ищем непрочитанные письма
        status, messages = mail.search(None, "UNSEEN")
        if status != "OK":
            logger.error("Ошибка при поиске писем")
            return

        email_ids = messages[0].split()
        bot = telebot.TeleBot(BOT_TOKEN)

        # Обрабатываем каждое новое письмо
        for e_id in email_ids:
            try:
                status, msg_data = mail.fetch(e_id, "(RFC822)")
                if status != "OK":
                    continue

                msg = email.message_from_bytes(msg_data[0][1])
                subject = decode_mime_words(msg["Subject"])
                from_ = decode_mime_words(msg["From"])
                to_ = decode_mime_words(msg["To"])

                # Если темы нет, устанавливаем значение по умолчанию
                if not subject:
                    subject = "(Без темы)"

                body = get_email_body(msg)

                # Формируем сообщение для Telegram
                telegram_message = (
                    f"📨 *Новое письмо*\n\n"
                    f"*От:* {from_}\n"
                    f"*Кому:* {to_}\n"
                    f"*Тема:* {subject}\n\n"
                    f"*Содержимое:*\n{body[:1000]}"  # Ограничиваем длину сообщения
                )
                # Отправляем сообщение в Telegram
                bot.send_message(CHAT_ID, telegram_message, parse_mode="Markdown")
                logger.info(f"Уведомление отправлено для письма ID: {e_id.decode()}")

            except Exception as e:
                logger.error(f"Ошибка при обработке письма {e_id}: {str(e)}")

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