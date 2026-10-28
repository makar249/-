import asyncio
import base64
import os

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

# ---------- Налаштування (беруться зі змінних середовища Render) ----------
TOKEN = os.environ["BOT_TOKEN"]                         # токен Telegram-бота від @BotFather
CF_ACCOUNT_ID = os.environ["CLOUDFLARE_ACCOUNT_ID"]     # Account ID з Cloudflare
CF_API_TOKEN = os.environ["CLOUDFLARE_API_TOKEN"]       # API-токен Workers AI з Cloudflare
MODEL = os.environ.get("CF_MODEL", "@cf/black-forest-labs/flux-1-schnell")

PORT = int(os.environ.get("PORT", "10000"))
BASE_URL = os.environ.get("RENDER_EXTERNAL_URL") or os.environ.get("WEBHOOK_URL")

CF_URL = f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT_ID}/ai/run/{MODEL}"
MAX_PROMPT = 500

HELP = (
    "Привіт! Я малюю картинки за описом.\n\n"
    "Просто напиши, що намалювати, наприклад:\n"
    "«кіт-астронавт на Марсі, цифровий арт»\n\n"
    "Або командою: /img опис\n"
    "Описи англійською зазвичай працюють краще."
)


def result_kb():
    return Kb([[Btn("🔄 Ще варіант", callback_data="again")]])


async def fetch_image(prompt: str):
    """Повертає (байти картинки, None) або (None, текст помилки для користувача)."""
    headers = {"Authorization": f"Bearer {CF_API_TOKEN}"}
    async with httpx.AsyncClient(timeout=120) as client:
        for attempt in range(2):
            payload = {"prompt": prompt, "steps": 4}
            try:
                r = await client.post(CF_URL, headers=headers, json=payload)
            except httpx.HTTPError as e:
                print("HTTP error:", repr(e), flush=True)
                await asyncio.sleep(3)
                continue

            if r.status_code == 200:
                if r.headers.get("content-type", "").startswith("image"):
                    return r.content, None
                data = r.json()
                b64 = (data.get("result") or {}).get("image")
                if b64:
                    return base64.b64decode(b64), None
                print("No image in response:", str(data)[:500], flush=True)
                return None, "Модель не повернула картинку. Спробуй змінити опис."

            print("Cloudflare error", r.status_code, r.text[:500], flush=True)
            if r.status_code in (401, 403):
                return None, "Cloudflare не прийняв ключ. Перевір CLOUDFLARE_API_TOKEN і CLOUDFLARE_ACCOUNT_ID в Render."
            if r.status_code == 429:
                return None, "Вичерпано безкоштовний денний ліміт Cloudflare. Він оновлюється щодня о 00:00 UTC."
            await asyncio.sleep(3)
    return None, "Не вийшло намалювати. Спробуй ще раз за хвилинку."


async def generate(chat, ctx: ContextTypes.DEFAULT_TYPE, prompt: str):
    ctx.user_data["prompt"] = prompt
    status = await chat.send_message("🎨 Малюю, зачекай кілька секунд…")
    await chat.send_action(ChatAction.UPLOAD_PHOTO)
    img, err = await fetch_image(prompt)
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
import asyncio
import base64
import os

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

# ---------- Налаштування (беруться зі змінних середовища Render) ----------
TOKEN = os.environ["BOT_TOKEN"]                         # токен Telegram-бота від @BotFather
CF_ACCOUNT_ID = os.environ["CLOUDFLARE_ACCOUNT_ID"]     # Account ID з Cloudflare
CF_API_TOKEN = os.environ["CLOUDFLARE_API_TOKEN"]       # API-токен Workers AI з Cloudflare
MODEL = os.environ.get("CF_MODEL", "@cf/black-forest-labs/flux-1-schnell")

PORT = int(os.environ.get("PORT", "10000"))
BASE_URL = os.environ.get("RENDER_EXTERNAL_URL") or os.environ.get("WEBHOOK_URL")

CF_URL = f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT_ID}/ai/run/{MODEL}"
MAX_PROMPT = 500

HELP = (
    "Привіт! Я малюю картинки за описом.\n\n"
    "Просто напиши, що намалювати, наприклад:\n"
    "«кіт-астронавт на Марсі, цифровий арт»\n\n"
    "Або командою: /img опис\n"
    "Описи англійською зазвичай працюють краще."
)


def result_kb():
    return Kb([[Btn("🔄 Ще варіант", callback_data="again")]])


async def fetch_image(prompt: str):
    """Повертає (байти картинки, None) або (None, текст помилки для користувача)."""
    headers = {"Authorization": f"Bearer {CF_API_TOKEN}"}
    async with httpx.AsyncClient(timeout=120) as client:
        for attempt in range(2):
            payload = {"prompt": prompt, "steps": 4}
            try:
                r = await client.post(CF_URL, headers=headers, json=payload)
            except httpx.HTTPError as e:
                print("HTTP error:", repr(e), flush=True)
                await asyncio.sleep(3)
                continue

            if r.status_code == 200:
                if r.headers.get("content-type", "").startswith("image"):
                    return r.content, None
                data = r.json()
                b64 = (data.get("result") or {}).get("image")
                if b64:
                    return base64.b64decode(b64), None
                print("No image in response:", str(data)[:500], flush=True)
                return None, "Модель не повернула картинку. Спробуй змінити опис."

            print("Cloudflare error", r.status_code, r.text[:500], flush=True)
            if r.status_code in (401, 403):
                return None, "Cloudflare не прийняв ключ. Перевір CLOUDFLARE_API_TOKEN і CLOUDFLARE_ACCOUNT_ID в Render."
            if r.status_code == 429:
                return None, "Вичерпано безкоштовний денний ліміт Cloudflare. Він оновлюється щодня о 00:00 UTC."
            await asyncio.sleep(3)
    return None, "Не вийшло намалювати. Спробуй ще раз за хвилинку."


async def generate(chat, ctx: ContextTypes.DEFAULT_TYPE, prompt: str):
    ctx.user_data["prompt"] = prompt
    status = await chat.send_message("🎨 Малюю, зачекай кілька секунд…")
    await chat.send_action(ChatAction.UPLOAD_PHOTO)
    img, err = await fetch_image(prompt)
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
