import asyncio
import base64
import os
import random

import httpx
from telegram import InlineKeyboardButton as Btn, InlineKeyboardMarkup as Kb, Update
from telegram.constants import ChatAction
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# Налаштування беруться зі змінних середовища (Render → Environment)
TOKEN = os.environ["BOT_TOKEN"]
PORT = int(os.environ.get("PORT", "10000"))
BASE_URL = os.environ.get("RENDER_EXTERNAL_URL") or os.environ.get("WEBHOOK_URL")

API_KEY = os.environ["GEMINI_API_KEY"]
MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash-image")
GEMINI_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent"

# назва кнопки, співвідношення сторін
FORMATS = {
    "square": ("⬜ Квадрат", "1:1"),
    "landscape": ("🖼 Горизонтальний", "16:9"),
    "portrait": ("📱 Вертикальний", "9:16"),
}
ORDER = list(FORMATS)
MAX_PROMPT = 500

HELP = (
    "Привіт! Я малюю картинки за описом (через Gemini).\n\n"
    "Просто напиши, що намалювати, наприклад:\n"
    "«кіт-астронавт на Марсі, цифровий арт»\n\n"
    "Або командою: /img опис\n"
    "Чим детальніший опис, тим краще результат."
)


def result_kb():
    return Kb([[Btn("🔄 Ще варіант", callback_data="again"), Btn("📐 Формат", callback_data="fmt")]])


async def fetch_image(prompt: str, fmt: str):
    """Повертає (байти картинки, None) або (None, текст помилки для користувача)."""
    ratio = FORMATS[fmt][1]
    headers = {"x-goog-api-key": API_KEY, "Content-Type": "application/json"}

    def body(with_ratio: bool):
        cfg = {"responseModalities": ["TEXT", "IMAGE"]}
        if with_ratio:
            cfg["imageConfig"] = {"aspectRatio": ratio}
        return {"contents": [{"parts": [{"text": f"Generate an image: {prompt}"}]}], "generationConfig": cfg}

    with_ratio = True
    async with httpx.AsyncClient(timeout=120) as client:
        for attempt in range(3):
            try:
                r = await client.post(GEMINI_URL, headers=headers, json=body(with_ratio))
            except httpx.HTTPError as e:
                print("HTTP error:", repr(e), flush=True)
                await asyncio.sleep(3)
                continue

            if r.status_code == 200:
                data = r.json()
                cand = (data.get("candidates") or [{}])[0]
                for part in cand.get("content", {}).get("parts", []):
                    inline = part.get("inlineData") or part.get("inline_data")
                    if inline and inline.get("data"):
                        return base64.b64decode(inline["data"]), None
                print("No image in response:", str(data)[:500], flush=True)
                return None, "Gemini не повернув картинку (можливо, спрацював фільтр безпеки). Спробуй змінити опис."

            print("Gemini error", r.status_code, r.text[:500], flush=True)
            if r.status_code == 400 and with_ratio:
                with_ratio = False  # модель не прийняла формат, пробуємо без нього
                continue
            if r.status_code == 429:
                if attempt < 2:
                    await asyncio.sleep(6)
                    continue
                return None, "Ліміт запитів до Gemini вичерпано. Спробуй пізніше (або перевір, що для ключа ввімкнена генерація картинок)."
            if r.status_code in (400, 401, 403, 404):
                return None, "Gemini відхилив запит. Перевір GEMINI_API_KEY і GEMINI_MODEL в Render, дивись логи."
            await asyncio.sleep(3)
    return None, "Gemini зараз не відповідає. Спробуй ще раз за хвилинку."


async def generate(chat, ctx: ContextTypes.DEFAULT_TYPE, prompt: str):
    fmt = ctx.user_data.get("fmt", "square")
    ctx.user_data["prompt"] = prompt
    status = await chat.send_message("🎨 Малюю, це може зайняти до хвилини…")
    await chat.send_action(ChatAction.UPLOAD_PHOTO)
    img, err = await fetch_image(prompt, fmt)
    try:
        await status.delete()
    except Exception:
        pass
    if img is None:
        await chat.send_message(err or "Не вийшло намалювати.", reply_markup=result_kb())
        return
    caption = prompt if len(prompt) <= 200 else prompt[:197] + "…"
    await chat.send_photo(photo=img, caption=caption, reply_markup=result_kb())


async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(HELP)


async def img_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    prompt = " ".join(ctx.args).strip()
    if not prompt:
        await update.message.reply_text("Напиши опис після команди, наприклад:\n/img червоний дракон над морем")
        return
    await generate(update.effective_chat, ctx, prompt[:MAX_PROMPT])


async def on_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    prompt = update.message.text.strip()
    if prompt:
        await generate(update.effective_chat, ctx, prompt[:MAX_PROMPT])


async def on_button(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    prompt = ctx.user_data.get("prompt")
    if not prompt:
        await query.answer("Напиши, що намалювати 🙂", show_alert=True)
        return
    if query.data == "fmt":
        cur = ctx.user_data.get("fmt", "square")
        ctx.user_data["fmt"] = ORDER[(ORDER.index(cur) + 1) % len(ORDER)]
        await query.answer(f"Формат: {FORMATS[ctx.user_data['fmt']][0]}")
    else:
        await query.answer()
    await generate(query.message.chat, ctx, prompt)


def main():
    app = (
        Application.builder()
        .token(TOKEN)
        .concurrent_updates(True)  # поки одна картинка малюється, бот відповідає іншим
        .connect_timeout(30)
        .read_timeout(60)
        .write_timeout(60)
        .pool_timeout(30)
        .build()
    )
    app.add_handler(CommandHandler(["start", "help"], start))
    app.add_handler(CommandHandler("img", img_cmd))
    app.add_handler(CallbackQueryHandler(on_button))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))

    if BASE_URL:
        app.run_webhook(
            listen="0.0.0.0",
            port=PORT,
            url_path=TOKEN,
            webhook_url=f"{BASE_URL.rstrip('/')}/{TOKEN}",
            drop_pending_updates=True,
        )
    else:
        app.run_polling()


if __name__ == "__main__":
    main()
