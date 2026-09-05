"""
Telegram бот, который общается в твоём стиле.
Читает экспорт Telegram-чата (result.json) и учится твоей манере общения.
Использует Google Gemini API (есть бесплатный tier).
"""

import asyncio
import json
import logging
import os
from pathlib import Path

# Telegram
from telegram import Update
from telegram.ext import (
    Application, MessageHandler, CommandHandler,
    ContextTypes, filters
)

# Google Gemini
from google import genai 

# ─────────────────────────────────────────────
# НАСТРОЙКИ — заполни перед запуском
# ─────────────────────────────────────────────
BOT_TOKEN        = os.getenv("BOT_TOKEN", "8920483390:AAECgLUKMF6KClOTccqBwM7Uuv1ArFDTdzo")
ADMIN_BOT_TOKEN  = os.getenv("ADMIN_BOT_TOKEN", "8923834508:AAEoQMf1D65gLhhOEnqlUK1NSnWfNAH9CO0")
GEMINI_API_KEY   = os.getenv("GEMINI_API_KEY", "AQ.Ab8RN6JCu4hcxWF3SepaAaJ2AjmmChs9lwVviUsSkVlWxeZn3A")
ADMIN_USER_ID    = int(os.getenv("ADMIN_USER_ID", "5098399620"))
RESULT_JSON_PATH = "result.json"        # путь к экспорту Telegram

# Твоё имя в Telegram (как в экспорте, поле "from")
# Оставь "" — определится автоматически
YOUR_NAME = ""

# Модель. gemini-1.5-flash — быстрая и бесплатная
GEMINI_MODEL = "gemini-2.0-flash-lite"

# Сколько твоих сообщений брать для анализа стиля
MAX_STYLE_MESSAGES = 1000

# Сколько сообщений помнить в одном диалоге
MAX_HISTORY = 30
# ─────────────────────────────────────────────

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Хранилище истории: {user_id: [{"role": ..., "parts": ...}]}
dialog_history: dict[int, list] = {}
known_users: dict[int, str] = {}
auto_reply_enabled = True


# ══════════════════════════════════════════════
# 1. ЗАГРУЗКА И АНАЛИЗ СТИЛЯ
# ══════════════════════════════════════════════

def load_my_messages(json_path: str, your_name: str, limit: int) -> tuple[str, list[str]]:
    path = Path(json_path)
    if not path.exists():
        raise FileNotFoundError(f"Файл {json_path} не найден. Положи result.json рядом с bot.py.")

    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    # Поддержка двух форматов экспорта Telegram
    if "messages" in data:
        messages = data["messages"]
    elif "chats" in data:
        messages = []
        for chat in data["chats"].get("list", []):
            messages.extend(chat.get("messages", []))
    else:
        messages = []

    # Автоопределение имени
    if not your_name:
        name_count: dict[str, int] = {}
        for m in messages:
            if m.get("type") != "message":
                continue
            sender = m.get("from") or ""
            if sender:
                name_count[sender] = name_count.get(sender, 0) + 1
        if not name_count:
            raise ValueError("Не найдено сообщений с полем 'from'. Проверь result.json.")
        your_name = max(name_count, key=name_count.__getitem__)
        logger.info(f"Автоопределение имени: «{your_name}» ({name_count[your_name]} сообщений)")

    # Собираем текстовые сообщения
    my_msgs: list[str] = []
    for m in messages:
        if m.get("type") != "message":
            continue
        if (m.get("from") or "") != your_name:
            continue
        text = m.get("text", "")
        if isinstance(text, list):
            text = "".join(p if isinstance(p, str) else p.get("text", "") for p in text)
        text = text.strip()
        if text and len(text) > 1:
            my_msgs.append(text)

    logger.info(f"Найдено {len(my_msgs)} твоих сообщений, берём последние {limit}.")
    return your_name, my_msgs[-limit:]


def build_system_prompt(your_name: str, messages: list[str]) -> str:
    sample = "\n".join(f"- {m}" for m in messages[:200])
    return f"""Ты — точная копия человека по имени {your_name} в переписке.
Твоя задача: отвечать ТОЧНО в его стиле, как будто это он сам пишет.

Ниже — реальные сообщения этого человека из Telegram. Изучи их досконально:

{sample}

━━━ ПРОФИЛЬ ЧЕЛОВЕКА ━━━
- Полное имя: Илья Лозовой.
- Возраст: примерно 13 лет на 2026 год, учится в 7-м классе.
- Учится в 7А классе школы №23 в Уфе; родной город — Уфа, также бывает в Москве и Китае.
- Интересуется учебой, домашними заданиями, оценками, ВПР и подготовкой к экзаменам; во второй четверти был отличником.
- Часто обсуждает математику, русский, английский, биологию и историю, а также общается с учителями по оценкам и расписанию.
- Пишет Telegram-ботов, создает веб-страницы на HTML/CSS/JS, программирует на Python и разбирается в хостинге.
- Собирает и настраивает ПК, разбирается в комплектующих, драйверах, прокси, VPN, Radmin и сетевых протоколах.
- Монтирует видео в Adobe Premiere Pro, занимается цветокоррекцией и создает ролики для YouTube, школы и церкви.
- Любит Minecraft и моды Create, WATERFrAMES и TaCZ, строит серверы; также интересуется Steam Deck, Nintendo Switch, LEGO-играми, The Escapists, CS2 и Forza Horizon.
- Участвует в D&D как Мастер, пишет фэнтези и супергеройские книги, публикует их на Author.today.
- Интересуется AR/VR, в частности очками XReal Air 2 Ultra.
- Участвует в церковных мероприятиях: подростковых служениях, прославлении, подготовке слайдов, играх и дискотеках.
- Помогает друзьям с техникой и любит разбираться в новых технологиях.

Используй этот профиль только для естественных ответов от лица Ильи. Не перечисляй его целиком без необходимости и не раскрывай личные данные незнакомым людям.

━━━ КАК ПИСАТЬ ━━━
• Длина сообщений — такая же (короткие/длинные, как у него)
• Пунктуация — копируй его привычки (точки, запятые, многоточия)
• Заглавные буквы — как у него (может писать всё строчными)
• Сленг и выражения — используй его любимые слова
• Эмодзи — только если он их использует, и те же самые
• Грамматика — если пишет с ошибками намеренно, делай так же
• Тональность — его характерная манера общения

━━━ ВАЖНО ━━━
- Ты НЕ ИИ и НЕ бот — ты и есть {your_name}
- Никогда не говори, что ты языковая модель
- Не добавляй вежливые обороты, которых нет в его стиле
- Пиши так, будто отвечаешь другу в мессенджере

━━━ ЕСЛИ НЕ УВЕРЕН ━━━
- Не отвечай автоматически «не знаю» или «не могу сказать».
- Сначала попробуй дать наиболее правдоподобную версию, полезную догадку или несколько вариантов ответа.
- Если точных данных недостаточно, прямо пометь ответ как предположение: «думаю, что...», «скорее всего...», «возможно...». Затем предложи, как это проверить.
- В творческих вопросах, шутках и обсуждении идей смело придумывай варианты и развивай мысль.
- Не выдумывай конкретные факты, цитаты, источники, события, цены или имена так, будто они проверены.
- Если вопрос потенциально опасный или требует точной профессиональной информации, не угадывай уверенно: дай осторожный общий ответ и укажи на неопределённость."""


# ══════════════════════════════════════════════
# 2. ОБРАЩЕНИЕ К GEMINI API
# ══════════════════════════════════════════════

def ask_gemini(
    client: genai.Client,
    history: list[dict],
    user_message: str,
    system_prompt: str,
) -> str:
    contents = [
        {
            "role": "model" if item["role"] == "model" else "user",
            "parts": [{"text": item["parts"][0]}],
        }
        for item in history
    ]
    contents.append({"role": "user", "parts": [{"text": user_message}]})
    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=contents,
        config={"system_instruction": system_prompt, "temperature": 0.9},
    )
    reply = (response.text or "").strip()
    if not reply:
        raise RuntimeError("Gemini вернул пустой ответ")

    history.append({"role": "user", "parts": [user_message]})
    history.append({"role": "model", "parts": [reply]})

    # Обрезаем историю
    if len(history) > MAX_HISTORY * 2:
        history[:] = history[-(MAX_HISTORY * 2):]

    return reply


# ══════════════════════════════════════════════
# 3. TELEGRAM ХЕНДЛЕРЫ
# ══════════════════════════════════════════════

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = context.bot_data["your_name"]
    await update.message.reply_text(
        f"Привет! Я общаюсь в стиле {name}. Пиши что угодно 👋"
    )


async def cmd_reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    dialog_history.pop(uid, None)
    await update.message.reply_text("История очищена.")


def is_admin(update: Update) -> bool:
    return bool(update.effective_user and update.effective_user.id == ADMIN_USER_ID)


async def admin_only(update: Update) -> bool:
    if is_admin(update):
        return True
    if update.effective_message:
        await update.effective_message.reply_text("Нет доступа.")
    return False


async def forward_to_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not ADMIN_USER_ID or not update.message:
        return
    try:
        admin_bot = context.bot_data["admin_bot"]
        sender = update.effective_user.full_name if update.effective_user else "Неизвестный пользователь"
        text = update.message.text or update.message.caption or "[сообщение без текста]"
        await admin_bot.send_message(
            chat_id=ADMIN_USER_ID,
            text=f"Входящее от {sender} ({update.effective_user.id}):\n{text}",
        )
    except Exception as error:
        logger.error("Не удалось переслать сообщение админу: %s", error)


async def notify_admin_reply(context: ContextTypes.DEFAULT_TYPE, user_id: int, reply: str) -> None:
    try:
        await context.bot_data["admin_bot"].send_message(
            chat_id=ADMIN_USER_ID,
            text=f"Ответ бота пользователю {user_id}:\n{reply}",
        )
    except Exception as error:
        logger.error("Не удалось отправить ответ бота админу: %s", error)


async def cmd_pause(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global auto_reply_enabled
    if not await admin_only(update):
        return
    auto_reply_enabled = False
    await update.message.reply_text("Автоответы остановлены.")


async def cmd_admin_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await admin_only(update):
        return
    await update.message.reply_text(
        "/stop - остановить автоответы\n"
        "/resume - включить автоответы\n"
        "/status - состояние\n"
        "/users - список пользователей\n"
        "/reply USER_ID текст - ответить вручную"
    )


async def handle_admin_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await admin_only(update):
        return
    await update.message.reply_text(
        "Это админский бот. Команды: /status, /stop, /resume, /users\n"
        "Для ответа пользователю: /reply USER_ID текст"
    )


async def cmd_resume(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global auto_reply_enabled
    if not await admin_only(update):
        return
    auto_reply_enabled = True
    await update.message.reply_text("Автоответы снова включены.")


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await admin_only(update):
        return
    state = "включены" if auto_reply_enabled else "остановлены"
    await update.message.reply_text(
        f"Автоответы: {state}\nПользователей в памяти: {len(known_users)}"
    )


async def cmd_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await admin_only(update):
        return
    if not known_users:
        await update.message.reply_text("Обращений пока нет.")
        return
    lines = [f"{user_id}: {name}" for user_id, name in known_users.items()]
    await update.message.reply_text("Пользователи:\n" + "\n".join(lines))


async def cmd_reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await admin_only(update):
        return
    if len(context.args) < 2:
        await update.message.reply_text("Формат: /reply USER_ID текст")
        return
    try:
        user_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("USER_ID должен быть числом.")
        return
    if user_id not in known_users:
        await update.message.reply_text("Пользователь ещё не писал этому боту.")
        return
    reply = " ".join(context.args[1:])
    await context.bot_data["main_bot"].send_message(chat_id=user_id, text=reply)
    await update.message.reply_text("Сообщение отправлено.")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.effective_user:
        return

    logger.info(
        "Получено сообщение от %s (%s)",
        update.effective_user.full_name,
        update.effective_user.id,
    )
    uid = update.effective_user.id
    known_users[uid] = update.effective_user.full_name or str(uid)
    await forward_to_admin(update, context)

    if not update.message.text:
        return

    text = update.message.text

    if not auto_reply_enabled:
        return

    if uid not in dialog_history:
        dialog_history[uid] = []

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    try:
        reply = await asyncio.to_thread(
            ask_gemini,
            client=context.bot_data["gemini"],
            history=dialog_history[uid],
            user_message=text,
            system_prompt=context.bot_data["system_prompt"],
        )
        await update.message.reply_text(reply)
        await notify_admin_reply(context, uid, reply)
    except Exception as e:
        logger.error(f"Ошибка Gemini API: {e}")
        await update.message.reply_text("Ошибка, попробуй ещё раз.")


# ══════════════════════════════════════════════
# 4. ЗАПУСК
# ══════════════════════════════════════════════

async def run_apps(main_app: Application, admin_app: Application):
    await main_app.initialize()
    await admin_app.initialize()
    await main_app.start()
    await admin_app.start()
    await main_app.updater.start_polling(drop_pending_updates=True)
    await admin_app.updater.start_polling(drop_pending_updates=True)
    logger.info("Основной и управляющий боты запущены. Ctrl+C для остановки.")
    try:
        await asyncio.Event().wait()
    finally:
        await main_app.updater.stop()
        await admin_app.updater.stop()
        await main_app.stop()
        await admin_app.stop()
        await main_app.shutdown()
        await admin_app.shutdown()


def main():
    if not BOT_TOKEN or not ADMIN_BOT_TOKEN or not GEMINI_API_KEY or not ADMIN_USER_ID:
        raise ValueError(
            "Заполни переменные окружения BOT_TOKEN, ADMIN_BOT_TOKEN, "
            "GEMINI_API_KEY и ADMIN_USER_ID. GEMINI_API_KEY должен быть ключом "
            "из Google AI Studio, а не ключом Groq/OpenRouter."
        )

    logger.info("Загружаю твои сообщения из result.json...")
    your_name, my_messages = load_my_messages(RESULT_JSON_PATH, YOUR_NAME, MAX_STYLE_MESSAGES)

    if not my_messages:
        raise ValueError(f"Не нашёл сообщений от «{your_name}» в result.json.")

    system_prompt = build_system_prompt(your_name, my_messages)
    logger.info(f"Стиль загружен. Имя: {your_name}, сообщений: {len(my_messages)}")

    # Настраиваем Gemini через новый официальный SDK.
    gemini_client = genai.Client(api_key=GEMINI_API_KEY)

    app = Application.builder().token(BOT_TOKEN).build()
    admin_app = Application.builder().token(ADMIN_BOT_TOKEN).build()
    app.bot_data["gemini"] = gemini_client
    app.bot_data["system_prompt"] = system_prompt
    app.bot_data["your_name"] = your_name
    app.bot_data["main_bot"] = app.bot
    app.bot_data["admin_bot"] = admin_app.bot
    admin_app.bot_data["main_bot"] = app.bot

    app.add_handler(MessageHandler(filters.COMMAND, forward_to_admin), group=-1)
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("reset", cmd_reset))
    app.add_handler(MessageHandler(filters.ALL, handle_message))

    admin_app.add_handler(CommandHandler("start", cmd_admin_start))
    admin_app.add_handler(CommandHandler("stop", cmd_pause))
    admin_app.add_handler(CommandHandler("resume", cmd_resume))
    admin_app.add_handler(CommandHandler("status", cmd_status))
    admin_app.add_handler(CommandHandler("users", cmd_users))
    admin_app.add_handler(CommandHandler("reply", cmd_reply))
    admin_app.add_handler(MessageHandler(filters.ALL, handle_admin_message))

    asyncio.run(run_apps(app, admin_app))


if __name__ == "__main__":
    main()
с
