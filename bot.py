import os
import json
import logging
import signal

from telegram import (
    Update,
    ReplyKeyboardMarkup,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    CommandHandler,
    ConversationHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
)

# ——————————————————————————————————————————————
#           Настройка логирования
# ——————————————————————————————————————————————
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

# ——————————————————————————————————————————————
#           Константы
# ——————————————————————————————————————————————
TOKEN = os.getenv("BOT_TOKEN")
STATE_LEVEL, STATE_TOPIC, STATE_QUIZ = range(3)
LEVELS = ["A1", "A2", "B1", "B2"]

# ——————————————————————————————————————————————
#           Хендлеры
# ——————————————————————————————————————————————
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Привет! Я — тренажёр по итальянскому.\n"
        "Напиши /quiz, чтобы начать и выбрать уровень."
    )

async def quiz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = [[InlineKeyboardButton(lvl, callback_data=f"level|{lvl}")] for lvl in LEVELS]
    await update.message.reply_text("Выберите уровень:", reply_markup=InlineKeyboardMarkup(kb))
    return STATE_LEVEL

async def on_level_select(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    level = query.data.split("|", 1)[1]
    context.user_data["level"] = level

    folder = os.path.join("content", level)
    if not os.path.isdir(folder):
        await query.edit_message_text(f"❌ Нет упражнений для уровня {level}.")
        return STATE_LEVEL

    files = sorted(f for f in os.listdir(folder) if f.endswith(".json"))
    kb = []
    for fn in files:
        try:
            with open(os.path.join(folder, fn), encoding="utf-8") as f:
                data = json.load(f)
            name = data.get("topic_name", fn[:-5])
        except Exception:
            continue
        kb.append([InlineKeyboardButton(name, callback_data=f"topic|{fn}")])

    if not kb:
        await query.edit_message_text(f"❌ Для уровня {level} нет корректных тем.")
        return STATE_LEVEL

    await query.edit_message_text(
        f"📂 Уровень *{level}* выбран.\nВыберите тему:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(kb),
    )
    return STATE_TOPIC

async def on_topic_select(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    fn = query.data.split("|", 1)[1]
    level = context.user_data["level"]
    path = os.path.join("content", level, fn)

    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        exercises = data.get("exercises", [])
        topic_name = data.get("topic_name", fn[:-5])
    except Exception:
        await query.edit_message_text("❌ Ошибка при загрузке упражнений.")
        return STATE_TOPIC

    if not exercises:
        await query.edit_message_text("❌ Упражнения пусты.")
        return STATE_TOPIC

    context.user_data.update({
        "topic_name": topic_name,
        "exercises": exercises,
        "index": 0,
    })
    await query.edit_message_text(f"🚀 Тема *{topic_name}* выбрана!", parse_mode="Markdown")
    return await send_question(update, context)

async def send_question(update: Update, context: ContextTypes.DEFAULT_TYPE):
    idx = context.user_data["index"]
    exercises = context.user_data["exercises"]
    if idx >= len(exercises):
        return await _reply(update, "🎉 Все упражнения пройдены! Напиши /quiz чтобы начать заново.")

    ex = exercises[idx]
    kb = ReplyKeyboardMarkup([[opt] for opt in ex["options"]],
                             resize_keyboard=True, one_time_keyboard=True)
    return await _reply(
        update,
        f"❓ Упражнение {idx+1}:\n{ex['question']}",
        reply_markup=kb
    )

async def handle_answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    idx = context.user_data["index"]
    ex = context.user_data["exercises"][idx]
    user = update.message.text.strip()
    correct = ex["answer"]

    if user.lower() == correct.lower():
        await update.message.reply_text(f"✅ Верно!\n{ex.get('explanation','')}")
    else:
        await update.message.reply_text(
            f"❌ Неверно.\nПравильный ответ: {correct}\n{ex.get('explanation','')}"
        )

    context.user_data["index"] += 1
    return await send_question(update, context)

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ Викторина отменена. Напиши /quiz чтобы начать заново.")
    return ConversationHandler.END

async def _reply(update: Update, text: str, **kw):
    if update.callback_query:
        await update.callback_query.message.reply_text(text, **kw)
    else:
        await update.message.reply_text(text, **kw)

# ——————————————————————————————————————————————
# Удаление старого webhook перед polling
# ——————————————————————————————————————————————
async def delete_webhook_on_startup(app):
    """
    Вызывается сразу после инициализации Application.
    Удаляет предыдущий webhook и сбрасывает очередь обновлений.
    """
    await app.bot.delete_webhook(drop_pending_updates=True)
    logging.info("🔄 Webhook удалён, очередь сброшена.")

# ——————————————————————————————————————————————
# Синхронная точка входа
# ——————————————————————————————————————————————
def main():
    if not TOKEN:
        logging.error("❌ BOT_TOKEN не задан.")
        return

    # 1) Создаём Application и регистрируем удаление webhook
    app = (
        ApplicationBuilder()
        .token(TOKEN)
        .post_init(delete_webhook_on_startup)
        .build()
    )

    # 2) Graceful shutdown: ловим SIGTERM/SIGINT и корректно останавливаем polling
    def shutdown(signum, frame):
        logging.info("🔴 Остановка polling…")
        app.stop()

    import signal
    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    # 3) Регистрируем хендлеры
    app.add_handler(CommandHandler("start", start))
    conv = ConversationHandler(
        entry_points=[CommandHandler("quiz", quiz)],
        states={
            STATE_LEVEL: [CallbackQueryHandler(on_level_select, pattern=r"^level\|")],
            STATE_TOPIC: [CallbackQueryHandler(on_topic_select, pattern=r"^topic\|")],
            STATE_QUIZ:  [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_answer)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
    )
    app.add_handler(conv)

    # 4) Запускаем единый polling
    logging.info("✅ Запускаем polling…")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
