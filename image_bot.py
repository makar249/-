import asyncio
import base64
import io
import os
import re

import httpx
from PIL import Image
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

# ---------- Налаштування (змінні середовища Render) ----------
TOKEN = os.environ["BOT_TOKEN"]
CF_ACCOUNT_ID = os.environ["CLOUDFLARE_ACCOUNT_ID"]
CF_API_TOKEN = os.environ["CLOUDFLARE_API_TOKEN"]
# Ця модель вміє приймати фото-референси (обличчя)
MODEL = os.environ.get("CF_MODEL", "@cf/black-forest-labs/flux-2-klein-4b")
REF_MAX = int(os.environ.get("REF_MAX", "512"))  # максимальний розмір референс-фото, px
MAX_REFS = 3                                      # скільки фото зберігаємо одночасно
MAX_PROMPT = 500

PORT = int(os.environ.get("PORT", "10000"))
BASE_URL = os.environ.get("RENDER_EXTERNAL_URL") or os.environ.get("WEBHOOK_URL")
CF_URL = f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT_ID}/ai/run/{MODEL}"

FORMATS = {
    "square": ("⬜ Квадрат", 1024, 1024),
    "landscape": ("🖼 Горизонтальний", 1280, 720),
    "portrait": ("📱 Вертикальний", 720, 1280),
}
ORDER = list(FORMATS)

# Проста перевірка: з фото реальних людей не робимо відвертих зображень
BAD_WORDS = re.compile(
    r"nude|naked|nsfw|porn|erotic|undress|topless|lingerie|sex|"
    r"гол(ая|ий|ую|а\b)|обнаж|порно|секс|эроти|еротич|разде|роздя|без одягу|без одежды",
    re.IGNORECASE,
)

HELP = (
    "Привіт! Я малюю картинки за описом і вмію використовувати обличчя з твоїх фото.\n\n"
    "🖼 Просто опис: напиши, що намалювати.\n"
    "📸 З обличчям: надішли фото (до 3) з підписом, що намалювати, наприклад:\n"
    "«ця людина як космонавт на Марсі». Або надішли фото, а опис напиши наступним повідомленням.\n\n"
    "Фото зберігаються, поки бот не засне або поки ти не напишеш /clear.\n\n"
    "⚠️ Використовуй лише свої фото або фото людей, які погодилися. "
    "Відверті, принизливі зображення та підробки з реальними людьми я не роблю."
)


def result_kb():
    return Kb([
        [Btn("🔄 Ще варіант", callback_data="again"), Btn("📐 Формат", callback_data="fmt")],
        [Btn("🗑 Скинути фото", callback_data="clear")],
    ])


def shrink(data: bytes) -> bytes:
    """Зменшуємо фото: менше навантаження й менше витрат безкоштовного ліміту."""
    im = Image.open(io.BytesIO(data)).convert("RGB")
    im.thumbnail((REF_MAX, REF_MAX))
    out = io.BytesIO()
    im.save(out, format="JPEG", quality=90)
    return out.getvalue()


async def fetch_image(prompt: str, fmt: str, refs: list):
    """Повертає (байти картинки, None) або (None, текст помилки для користувача)."""
    _, w, h = FORMATS[fmt]
    if refs:
        who = "people" if len(refs) > 1 else "person"
        prompt = f"{prompt}. Preserve the exact face and identity of the {who} from the reference image."
    headers = {"Authorization": f"Bearer {CF_API_TOKEN}"}

    async with httpx.AsyncClient(timeout=180) as client:
        for attempt in range(2):
            # Модель приймає лише multipart/form-data, навіть якщо тільки текст
            files = {
                "prompt": (None, prompt),
                "width": (None, str(w)),
                "height": (None, str(h)),
            }
            for i, img in enumerate(refs):
                files[f"input_image_{i}"] = (f"ref{i}.jpg", img, "image/jpeg")
            try:
                r = await client.post(CF_URL, headers=headers, files=files)
            except httpx.HTTPError as e:
                print("HTTP error:", repr(e), flush=True)
                await asyncio.sleep(3)
                continue

            if r.status_code == 200:
                if r.headers.get("content-type", "").startswith("image"):
                    return r.content, None
                data = r.json()
                b64 = (data.get("result") or {}).get("image") or data.get("image")
                if b64:
                    return base64.b64decode(b64), None
                print("No image in response:", str(data)[:500], flush=True)
                return None, "Модель не повернула картинку. Спробуй змінити опис або фото."

            print("Cloudflare error", r.status_code, r.text[:500], flush=True)
            if r.status_code in (401, 403):
                return None, "Cloudflare не прийняв ключ. Перевір CLOUDFLARE_API_TOKEN і CLOUDFLARE_ACCOUNT_ID в Render."
            if r.status_code == 429:
                return None, "Вичерпано безкоштовний денний ліміт Cloudflare. Він оновлюється щодня о 00:00 UTC."
            if r.status_code == 400 and attempt == 0 and refs:
                # можливо, фото завелике або не підходить — пробуємо ще раз
                await asyncio.sleep(2)
                continue
            await asyncio.sleep(3)
    return None, "Не вийшло намалювати. Спробуй ще раз або інше фото/опис."


async def generate(chat, ctx: ContextTypes.DEFAULT_TYPE, prompt: str):
    refs = ctx.user_data.get("refs", [])
    if refs and BAD_WORDS.search(prompt):
        await chat.send_message(
            "З фото реальних людей я не створюю відверті зображення. Опиши щось інше 🙂",
            reply_markup=result_kb(),
        )
        return
    fmt = ctx.user_data.get("fmt", "square")
    ctx.user_data["prompt"] = prompt
    status = await chat.send_message("🎨 Малюю, це може зайняти до хвилини…")
    await chat.send_action(ChatAction.UPLOAD_PHOTO)
    img, err = await fetch_image(prompt, fmt, refs)
    try:
        await status.delete()
    except Exception:
        pass
    if img is None:
        await chat.send_message(err or "Не вийшло намалювати.", reply_markup=result_kb())
        return
    note = f" · 📸 фото: {len(refs)}" if refs else ""
    caption = (prompt if len(prompt) <= 200 else prompt[:197] + "…") + note
    await chat.send_photo(photo=img, caption=caption, reply_markup=result_kb())


async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(HELP)


async def clear_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["refs"] = []
    await update.message.reply_text("Фото скинуто. Тепер малюю без облич.")


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


async def on_photo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    tg_file = await msg.photo[-1].get_file()
    data = bytes(await tg_file.download_as_bytearray())
    try:
        small = shrink(data)
    except Exception as e:
        print("bad photo:", repr(e), flush=True)
        await msg.reply_text("Не вдалося прочитати фото. Спробуй інше.")
        return
    refs = ctx.user_data.setdefault("refs", [])
    refs.append(small)
    del refs[:-MAX_REFS]  # лишаємо останні MAX_REFS фото

    caption = (msg.caption or "").strip()
    if caption and not msg.media_group_id:
        await generate(update.effective_chat, ctx, caption[:MAX_PROMPT])
        return
    # для альбому відповідаємо лише один раз
    if msg.media_group_id and ctx.user_data.get("last_group") == msg.media_group_id:
        return
    ctx.user_data["last_group"] = msg.media_group_id
    await msg.reply_text(
        f"📸 Фото збережено ({len(refs)} з {MAX_REFS}). Тепер напиши, що намалювати, "
        "наприклад: «ця людина як космонавт на Марсі». /clear скидає фото."
    )


async def on_button(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.data == "clear":
        ctx.user_data["refs"] = []
        await query.answer("Фото скинуто")
        return
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
        .concurrent_updates(True)
        .connect_timeout(30)
        .read_timeout(60)
        .write_timeout(60)
        .pool_timeout(30)
        .build()
    )
    app.add_handler(CommandHandler(["start", "help"], start))
    app.add_handler(CommandHandler("clear", clear_cmd))
    app.add_handler(CommandHandler("img", img_cmd))
    app.add_handler(CallbackQueryHandler(on_button))
    app.add_handler(MessageHandler(filters.PHOTO, on_photo))
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
                        
