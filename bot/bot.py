"""Telegram-бот: скачивание по ссылкам (несколько сразу) и поиск песен по названию.

Запуск отдельно: python bot/bot.py
На сервере бот стартует вместе с сайтом (см. server/app.py).
"""

import asyncio
import html
import logging
import os
import shutil
import sys
import uuid
from collections import OrderedDict
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
load_dotenv()

from aiogram import Bot, Dispatcher, F  # noqa: E402
from aiogram.client.default import DefaultBotProperties  # noqa: E402
from aiogram.client.session.aiohttp import AiohttpSession  # noqa: E402
from aiogram.client.telegram import TelegramAPIServer  # noqa: E402
from aiogram.enums import ChatAction  # noqa: E402
from aiogram.filters import Command, CommandStart  # noqa: E402
from aiogram.types import (  # noqa: E402
    BotCommand,
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LinkPreviewOptions,
    Message,
)

from core import downloader as dl  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("bot")

BOT_TOKEN = os.getenv("BOT_TOKEN")
PUBLIC_URL = (os.getenv("PUBLIC_URL") or os.getenv("RENDER_EXTERNAL_URL") or "").rstrip("/")
BOT_API_URL = os.getenv("BOT_API_URL", "").rstrip("/")
# Обычный Bot API принимает файлы до 50 МБ; локальный Bot API сервер — до 2 ГБ
MAX_UPLOAD = int(os.getenv("MAX_UPLOAD_MB", 2000 if BOT_API_URL else 50)) * 1024 * 1024
MAX_LINKS = 10
SEARCH_LIMIT = 10

dp = Dispatcher()
download_slots = asyncio.Semaphore(int(os.getenv("BOT_PARALLEL", 2)))
NO_PREVIEW = LinkPreviewOptions(is_disabled=True)


class LRU(OrderedDict):
    """Маленький кэш: ссылки и результаты поиска для кнопок (callback_data ≤ 64 байт)."""

    def __init__(self, size=500):
        super().__init__()
        self.size = size

    def put(self, value) -> str:
        key = uuid.uuid4().hex[:8]
        self[key] = value
        while len(self) > self.size:
            self.popitem(last=False)
        return key


pending_links = LRU()
searches = LRU()

SOURCE_ICONS = {"ytmusic": "🎵", "soundcloud": "☁️", "youtube": "▶️"}
SOURCE_SHORT = {"all": "Все", "ytmusic": "YT Music", "soundcloud": "SoundCloud", "youtube": "YouTube"}
SITE_ICONS = {
    "youtube.com": "▶️", "youtu.be": "▶️", "music.youtube.com": "🎵",
    "tiktok.com": "🎵", "instagram.com": "📸", "vk.com": "💙", "vkvideo.ru": "💙",
    "x.com": "✖️", "twitter.com": "✖️", "soundcloud.com": "☁️", "reddit.com": "👽",
    "twitch.tv": "💜", "pinterest.com": "📌", "facebook.com": "📘", "ok.ru": "🟠",
}


# ---------- оформление ----------

def esc(s) -> str:
    return html.escape(str(s or ""))


def host(url: str) -> str:
    h = urlparse(url).hostname or url
    return h.removeprefix("www.").removeprefix("m.")


def site_icon(url: str) -> str:
    h = host(url)
    for domain, icon in SITE_ICONS.items():
        if h == domain or h.endswith("." + domain):
            return icon
    return "🌐"


def progress_bar(p: float, width: int = 10) -> str:
    filled = round(p / 100 * width)
    return "▰" * filled + "▱" * (width - filled)


def plural(n: int, one: str, few: str, many: str) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def signature() -> str:
    return "\n\n<i>via @{}</i>".format(BOT_USERNAME) if BOT_USERNAME else ""


BOT_USERNAME = os.getenv("BOT_USERNAME", "").lstrip("@")

WELCOME = (
    "👋 <b>Привет! Я помогу скачать видео и музыку.</b>\n\n"
    "📥 <b>Скачать по ссылке</b>\n"
    "Пришли одну или сразу несколько ссылок — каждую с новой строки. "
    "Выберешь, что нужно: видео или MP3.\n"
    "<i>YouTube, TikTok, Instagram, VK, X, Reddit, SoundCloud и ещё 1000+ сайтов</i>\n\n"
    "🔎 <b>Найти песню</b>\n"
    "Просто напиши название или исполнителя. Ищу в YouTube Music, SoundCloud и YouTube.\n"
    "<i>Например: Кино Группа крови</i>"
)


def welcome_kb() -> InlineKeyboardMarkup | None:
    rows = [[InlineKeyboardButton(text="🔥 Попробовать поиск", callback_data="demo")]]
    if PUBLIC_URL.startswith("https://"):
        rows.append([InlineKeyboardButton(text="🌐 Открыть сайт", url=PUBLIC_URL)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ---------- команды ----------

@dp.message(CommandStart())
@dp.message(Command("help"))
async def cmd_start(message: Message):
    await message.answer(WELCOME, reply_markup=welcome_kb(), link_preview_options=NO_PREVIEW)


@dp.callback_query(F.data == "demo")
async def on_demo(cb: CallbackQuery):
    await cb.answer()
    await search_songs(cb.message, "Imagine Dragons Believer")


@dp.callback_query(F.data == "cancel")
async def on_cancel(cb: CallbackQuery):
    await cb.answer("Отменено")
    await cb.message.delete()


# ---------- текст: ссылки или поиск ----------

@dp.message(F.text)
async def on_text(message: Message):
    urls = dl.extract_urls(message.text)
    if not urls:
        await search_songs(message, message.text.strip())
        return

    extra = ""
    if len(urls) > MAX_LINKS:
        extra = f"\n<i>Возьму первые {MAX_LINKS} из {len(urls)}.</i>"
        urls = urls[:MAX_LINKS]
    key = pending_links.put(urls)
    n = len(urls)
    lines = "\n".join(f"{site_icon(u)} {esc(host(u))}" for u in urls)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🎬 Видео", callback_data=f"m:{key}:video"),
            InlineKeyboardButton(text="🎧 MP3", callback_data=f"m:{key}:audio"),
        ],
        [InlineKeyboardButton(text="✖️ Отмена", callback_data="cancel")],
    ])
    await message.reply(
        f"🔗 <b>{n} {plural(n, 'ссылка', 'ссылки', 'ссылок')}</b>\n{lines}{extra}\n\nЧто скачать?",
        reply_markup=kb,
        link_preview_options=NO_PREVIEW,
    )


# ---------- поиск песен ----------

def results_text(query: str, source: str, results: list[dict]) -> str:
    head = f"🔎 <b>{esc(query)}</b>"
    if source != "all":
        head += f"  ·  {SOURCE_SHORT[source]}"
    if not results:
        return head + "\n\n😕 Ничего не нашёл. Попробуй другой источник или напиши по-другому."
    lines = []
    for i, r in enumerate(results, 1):
        dur = dl.format_duration(r.get("duration"))
        meta = " · ".join(x for x in [esc(r.get("uploader")), dur] if x)
        lines.append(f"<b>{i}.</b> {SOURCE_ICONS[r['source']]} {esc(r['title'])}\n      <i>{meta}</i>")
    return head + "\n\n" + "\n".join(lines) + "\n\nНажми на номер, чтобы скачать MP3"


def results_kb(key: str, source: str, results: list[dict]) -> InlineKeyboardMarkup:
    rows, row = [], []
    for i in range(len(results)):
        row.append(InlineKeyboardButton(text=str(i + 1), callback_data=f"s:{key}:{source}:{i}"))
        if len(row) == 5:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([
        InlineKeyboardButton(
            text=("• " if s == source else "") + SOURCE_SHORT[s],
            callback_data=f"q:{key}:{s}",
        )
        for s in SOURCE_SHORT
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def get_results(key: str, source: str) -> list[dict]:
    entry = searches[key]
    if source not in entry["results"]:
        entry["results"][source] = await asyncio.to_thread(
            dl.search_music, entry["query"], SEARCH_LIMIT, source
        )
    return entry["results"][source]


async def search_songs(message: Message, query: str):
    if len(query) < 2:
        await message.answer("✍️ Напиши название песни или пришли ссылку.")
        return
    query = query[:100]
    status = await message.answer(f"🔎 Ищу <b>{esc(query)}</b>…")
    await message.bot.send_chat_action(message.chat.id, ChatAction.TYPING)
    key = searches.put({"query": query, "results": {}})
    try:
        results = await get_results(key, "all")
    except Exception as e:  # noqa: BLE001
        log.warning("search failed %r: %s", query, e)
        await status.edit_text("😕 Поиск сейчас не работает. Попробуй чуть позже.")
        return
    await status.edit_text(
        results_text(query, "all", results),
        reply_markup=results_kb(key, "all", results),
        link_preview_options=NO_PREVIEW,
    )


@dp.callback_query(F.data.startswith("q:"))
async def on_source(cb: CallbackQuery):
    _, key, source = cb.data.split(":")
    if key not in searches:
        await cb.answer("Поиск устарел — напиши название ещё раз", show_alert=True)
        return
    await cb.answer(f"Ищу в {SOURCE_SHORT[source]}…")
    try:
        results = await get_results(key, source)
    except Exception as e:  # noqa: BLE001
        log.warning("search %s failed: %s", source, e)
        results = []
    try:
        await cb.message.edit_text(
            results_text(searches[key]["query"], source, results),
            reply_markup=results_kb(key, source, results),
            link_preview_options=NO_PREVIEW,
        )
    except Exception:  # noqa: BLE001 — «message is not modified»
        pass


@dp.callback_query(F.data.startswith("s:"))
async def on_song(cb: CallbackQuery):
    _, key, source, idx = cb.data.split(":")
    try:
        track = searches[key]["results"][source][int(idx)]
    except (KeyError, IndexError, ValueError):
        await cb.answer("Поиск устарел — напиши название ещё раз", show_alert=True)
        return
    await cb.answer(f"⬇️ {track['title']}"[:190])
    await process(cb.message, track["url"], "audio", hint=track)


# ---------- скачивание ----------

@dp.callback_query(F.data.startswith("m:"))
async def on_mode(cb: CallbackQuery):
    _, key, mode = cb.data.split(":", 2)
    urls = pending_links.pop(key, None)
    if not urls:
        await cb.answer("Кнопка устарела — пришли ссылки ещё раз", show_alert=True)
        return
    await cb.answer()
    what = "🎬 видео" if mode == "video" else "🎧 MP3"
    await cb.message.edit_text(
        f"⬇️ Скачиваю {what}: {len(urls)} шт.",
        link_preview_options=NO_PREVIEW,
    )
    for i, url in enumerate(urls, 1):
        prefix = f"<b>[{i}/{len(urls)}]</b> " if len(urls) > 1 else ""
        await process(cb.message, url, mode, prefix)


async def process(chat_msg: Message, url: str, mode: str, prefix: str = "", hint: dict | None = None):
    name = esc(hint["title"]) if hint else f"{site_icon(url)} {esc(host(url))}"
    status = await chat_msg.answer(
        f"{prefix}⏳ {name}\n<code>{progress_bar(0)}</code> в очереди",
        link_preview_options=NO_PREVIEW,
    )
    loop = asyncio.get_running_loop()
    last = {"p": -100.0}

    def on_progress(p: float):
        if p - last["p"] >= 15 and p < 100:
            last["p"] = p
            asyncio.run_coroutine_threadsafe(
                safe_edit(status, f"{prefix}⬇️ {name}\n<code>{progress_bar(p)}</code> {int(p)}%"), loop
            )

    job_id, out_dir = dl.new_job_dir()
    try:
        async with download_slots:
            await safe_edit(status, f"{prefix}⬇️ {name}\n<code>{progress_bar(0)}</code> начинаю")
            info = await asyncio.to_thread(dl.download, url, mode, "720", out_dir, on_progress)
    except Exception as e:  # noqa: BLE001
        log.warning("download failed %s: %s", url, e)
        shutil.rmtree(out_dir, ignore_errors=True)
        await safe_edit(status, f"{prefix}😕 <b>Не получилось</b>\n{esc(dl.friendly_error(e))}\n<i>{esc(url)}</i>")
        return

    path: Path = info["path"]
    title = info.get("track") or info["title"]
    artist = info.get("artist") or (hint or {}).get("uploader") or info.get("uploader")
    size_mb = info["size"] / 1048576

    if info["size"] > MAX_UPLOAD:
        if PUBLIC_URL:
            link = f"{PUBLIC_URL}/api/files/{job_id}"
            kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="📥 Скачать файл", url=link)]])
            await safe_edit(
                status,
                f"{prefix}📦 <b>{esc(title)}</b>\n"
                f"Файл {size_mb:.0f} МБ — Telegram не пропустит больше 50 МБ.\n"
                "Забери по кнопке, ссылка живёт час.",
                kb,
            )
        else:
            shutil.rmtree(out_dir, ignore_errors=True)
            await safe_edit(
                status,
                f"{prefix}📦 <b>{esc(title)}</b>\nФайл {size_mb:.0f} МБ — больше лимита Telegram (50 МБ). "
                "Попробуй скачать MP3.",
            )
        return

    await safe_edit(status, f"{prefix}📤 {esc(title)}\n<code>{progress_bar(100)}</code> отправляю")
    try:
        file = FSInputFile(path, filename=path.name)
        duration = int(info["duration"]) if info.get("duration") else None
        if mode == "audio":
            await chat_msg.bot.send_chat_action(chat_msg.chat.id, ChatAction.UPLOAD_VOICE)
            album = (hint or {}).get("album")
            meta = " · ".join(x for x in [esc(artist), esc(album), dl.format_duration(duration)] if x)
            await chat_msg.answer_audio(
                file,
                title=title[:64],
                performer=(artist or "")[:64] or None,
                duration=duration,
                caption=f"🎧 <b>{esc(title)}</b>\n<i>{meta}</i>{signature()}",
            )
        elif path.suffix.lower() in (".mp4", ".mov", ".m4v"):
            await chat_msg.bot.send_chat_action(chat_msg.chat.id, ChatAction.UPLOAD_VIDEO)
            await chat_msg.answer_video(
                file,
                caption=f"🎬 <b>{esc(title)}</b>\n<i>{site_icon(url)} {esc(host(url))} · {size_mb:.1f} МБ</i>{signature()}",
                duration=duration,
                width=info.get("width"),
                height=info.get("height"),
                supports_streaming=True,
            )
        else:
            await chat_msg.answer_document(file, caption=f"📄 <b>{esc(title)}</b>{signature()}")
        await status.delete()
    except Exception as e:  # noqa: BLE001
        log.warning("send failed %s: %s", url, e)
        await safe_edit(status, f"{prefix}😕 Не удалось отправить: {esc(dl.friendly_error(e))}")
    finally:
        shutil.rmtree(out_dir, ignore_errors=True)


async def safe_edit(msg: Message, text: str, kb: InlineKeyboardMarkup | None = None):
    try:
        await msg.edit_text(text, reply_markup=kb, link_preview_options=NO_PREVIEW)
    except Exception:  # noqa: BLE001
        pass


# ---------- запуск ----------

def create_bot() -> Bot:
    session = None
    if BOT_API_URL:
        session = AiohttpSession(api=TelegramAPIServer.from_base(BOT_API_URL, is_local=True))
    return Bot(BOT_TOKEN, session=session, default=DefaultBotProperties(parse_mode="HTML"))


async def setup_profile(bot: Bot):
    global BOT_USERNAME
    try:
        me = await bot.get_me()
        BOT_USERNAME = me.username
        await bot.set_my_commands([BotCommand(command="start", description="🏠 Что умеет бот")])
        await bot.set_my_short_description(
            "🎧 Скачиваю видео и музыку из соцсетей. Ищу песни в YouTube Music, SoundCloud и YouTube."
        )
        await bot.set_my_description(
            "Привет! Я скачиваю видео и музыку.\n\n"
            "📥 Пришли ссылки (YouTube, TikTok, Instagram, VK, X…) — скачаю видео или MP3, можно несколько сразу.\n"
            "🔎 Напиши название песни — найду и пришлю MP3.\n\n"
            "Нажми «Запустить» 👇"
        )
        log.info("Бот запущен: @%s", me.username)
    except Exception as e:  # noqa: BLE001
        log.warning("не удалось настроить профиль бота: %s", e)


async def run_polling():
    """Запускает бота (используется и отдельно, и внутри сервера)."""
    bot = create_bot()
    await setup_profile(bot)
    try:
        await dp.start_polling(bot, handle_signals=False)
    finally:
        await bot.session.close()


async def cleanup_loop():
    while True:
        await asyncio.to_thread(dl.cleanup_old)
        await asyncio.sleep(600)


async def main():
    if not BOT_TOKEN:
        sys.exit("Нет BOT_TOKEN. Получи токен у @BotFather и впиши его в файл .env")
    asyncio.create_task(cleanup_loop())
    await run_polling()


if __name__ == "__main__":
    asyncio.run(main())
