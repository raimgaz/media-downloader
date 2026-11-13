"""Общий модуль скачивания для сайта и Telegram-бота (обёртка над yt-dlp)."""

import os
import re
import shutil
import time
import uuid
from pathlib import Path
from typing import Callable, Optional

import yt_dlp

ROOT = Path(__file__).resolve().parent.parent
DOWNLOAD_DIR = Path(os.getenv("DOWNLOAD_DIR", ROOT / "downloads"))
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

# Сколько живут скачанные файлы на диске (секунды)
FILE_TTL = int(os.getenv("FILE_TTL", 3600))

URL_RE = re.compile(r"https?://[^\s<>\"']+")

VIDEO_QUALITIES = {"best", "1080", "720", "480", "360"}


def extract_urls(text: str) -> list[str]:
    """Достаёт все ссылки из текста, без дублей, в исходном порядке."""
    seen, result = set(), []
    for url in URL_RE.findall(text or ""):
        url = url.rstrip(".,;)")
        if url not in seen:
            seen.add(url)
            result.append(url)
    return result


def _base_opts() -> dict:
    opts = {
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "noplaylist": True,
        "restrictfilenames": False,
        "windowsfilenames": True,
        "concurrent_fragment_downloads": 4,
        "socket_timeout": 30,
    }
    # YouTube требует JS-движок; используем Node.js, если он установлен
    if shutil.which("deno"):
        opts["js_runtimes"] = {"deno": {}}
    elif shutil.which("node"):
        opts["js_runtimes"] = {"node": {}}
    cookies = os.getenv("COOKIES_FILE")
    if cookies and Path(cookies).exists():
        opts["cookiefile"] = cookies
    return opts


def _video_format(quality: str) -> str:
    if quality not in VIDEO_QUALITIES or quality == "best":
        return "bv*[ext=mp4]+ba[ext=m4a]/bv*+ba/b"
    h = int(quality)
    return (
        f"bv*[height<={h}][ext=mp4]+ba[ext=m4a]/"
        f"bv*[height<={h}]+ba/b[height<={h}]/b"
    )


def new_job_dir() -> tuple[str, Path]:
    job_id = uuid.uuid4().hex[:12]
    path = DOWNLOAD_DIR / job_id
    path.mkdir(parents=True, exist_ok=True)
    return job_id, path


def find_job_file(job_id: str) -> Optional[Path]:
    """Готовый файл задачи (общий для сайта и бота: лежит в downloads/<id>/)."""
    if not re.fullmatch(r"[0-9a-f]{12}", job_id or ""):
        return None
    folder = DOWNLOAD_DIR / job_id
    if not folder.is_dir():
        return None
    files = [f for f in folder.iterdir() if f.is_file() and not f.name.endswith((".part", ".ytdl"))]
    return max(files, key=lambda f: f.stat().st_size) if files else None


def download(
    url: str,
    mode: str = "video",
    quality: str = "best",
    out_dir: Optional[Path] = None,
    progress: Optional[Callable[[float], None]] = None,
) -> dict:
    """Скачивает видео или аудио (mp3). Возвращает {title, path, duration, ...}."""
    if out_dir is None:
        _, out_dir = new_job_dir()
    out_dir = Path(out_dir)

    def hook(d):
        if progress and d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            if total:
                progress(min(d.get("downloaded_bytes", 0) / total * 100, 99.0))

    opts = _base_opts()
    opts.update({
        "outtmpl": str(out_dir / "%(title).150B.%(ext)s"),
        "progress_hooks": [hook],
    })
    if mode == "audio":
        opts.update({
            "outtmpl": str(out_dir / "%(artist&{} - |)s%(track,title).150B.%(ext)s"),
            "format": "ba/b",
            "writethumbnail": True,
            "postprocessors": [
                {"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"},
                {"key": "FFmpegThumbnailsConvertor", "format": "jpg", "when": "before_dl"},
                {"key": "FFmpegMetadata"},
                {"key": "EmbedThumbnail"},
            ],
        })
    else:
        opts.update({
            "format": _video_format(quality),
            "merge_output_format": "mp4",
        })

    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        if info.get("entries"):
            info = next(e for e in info["entries"] if e)

    files = [f for f in out_dir.iterdir() if f.is_file() and not f.name.endswith((".part", ".ytdl"))]
    if not files:
        raise RuntimeError("Файл не найден после скачивания")
    path = max(files, key=lambda f: f.stat().st_mtime)
    if progress:
        progress(100.0)
    return {
        "title": info.get("title") or path.stem,
        "uploader": info.get("uploader") or info.get("artist"),
        "track": info.get("track"),
        "artist": info.get("artist") or ", ".join(info.get("artists") or []) or None,
        "duration": info.get("duration"),
        "thumbnail": info.get("thumbnail"),
        "width": info.get("width"),
        "height": info.get("height"),
        "path": path,
        "size": path.stat().st_size,
    }


def is_youtube(url: str) -> bool:
    from urllib.parse import urlparse

    h = (urlparse(url).hostname or "").lower()
    return h == "youtu.be" or h == "youtube.com" or h.endswith(".youtube.com")


def is_blocked_error(e: Exception) -> bool:
    low = str(e).lower()
    return any(s in low for s in ("not a bot", "sign in", "login", "cookies", "429", "too many requests"))


def youtube_meta(url: str) -> dict:
    """Название и автор видео через oEmbed — работает даже когда YouTube блокирует скачивание."""
    import json
    import urllib.parse
    import urllib.request

    api = "https://www.youtube.com/oembed?format=json&url=" + urllib.parse.quote(url, safe="")
    with urllib.request.urlopen(api, timeout=15) as r:
        data = json.load(r)
    return {"title": data.get("title"), "uploader": data.get("author_name")}


def _clean_title(s: str) -> str:
    s = re.sub(r"[\(\[][^)\]]*(official|video|audio|lyrics?|клип|текст|hd|4k|mv)[^)\]]*[\)\]]", "", s, flags=re.I)
    return re.sub(r"\s+", " ", s).strip(" -–—")


def find_on_soundcloud(title: str, artist: Optional[str], duration: Optional[float]) -> list[dict]:
    """Кандидаты на SoundCloud, лучшие первыми (пустой список — ничего похожего)."""
    artist = (artist or "").removesuffix(" - Topic").removesuffix("VEVO").strip()
    title = _clean_title(title or "")
    # «Исполнитель - Песня» в названии уже есть — имя канала не добавляем
    if " - " in title or (artist and artist.lower() in title.lower()):
        query = title
    else:
        query = f"{artist} {title}".strip()
    if not query:
        return []
    candidates = []
    # поиск SoundCloud иногда отвечает 500 — пробуем ещё раз и запрос без дефиса
    for q in (query, query, re.sub(r"\s+[-–—|]\s+", " ", query)):
        try:
            candidates = _search_ytdlp("soundcloud", q, 10)
            break
        except Exception:  # noqa: BLE001
            time.sleep(1)
    if not candidates:
        return []

    bad_words = ("remix", "cover", "mashup", "sped up", "slowed", "nightcore", "karaoke",
                 "instrumental", "live", "edit", "кавер", "ремикс", "минус", "8d", "bass boosted")
    q_low = query.lower()

    def score(c: dict) -> float:
        name = f"{c.get('title') or ''} {c.get('uploader') or ''}".lower()
        s = 0.0
        s -= sum(20 for w in bad_words if w in name and w not in q_low)
        if artist and artist.lower() in name:
            s += 5
        if title and title.lower() in name:
            s += 3
        if " / " in (c.get("title") or ""):  # склейки из нескольких песен
            s -= 8
        if duration and c.get("duration"):
            diff = abs(c["duration"] - duration)
            s += 6 if diff <= 5 else 3 if diff <= 15 else -6 if diff > 40 else 0
        return s

    ranked = sorted(candidates, key=score, reverse=True)
    return [c for c in ranked if score(c) > -5]


def download_smart(
    url: str,
    mode: str = "video",
    quality: str = "best",
    out_dir: Optional[Path] = None,
    progress: Optional[Callable[[float], None]] = None,
    hint: Optional[dict] = None,
) -> dict:
    """Как download(), но если YouTube не отдаёт аудио (блокирует сервер),
    находит тот же трек на SoundCloud и скачивает оттуда."""
    if out_dir is None:
        _, out_dir = new_job_dir()
    try:
        return download(url, mode, quality, out_dir, progress)
    except Exception as e:
        if mode != "audio" or not is_youtube(url) or not is_blocked_error(e):
            raise
        meta = dict(hint or {})
        if not meta.get("title"):
            try:
                meta.update(youtube_meta(url))
            except Exception:  # noqa: BLE001
                raise e from None
        # часть треков на SoundCloud под DRM или только для подписчиков — пробуем следующие
        for alt in find_on_soundcloud(meta.get("title"), meta.get("uploader"), meta.get("duration"))[:4]:
            try:
                info = download(alt["url"], "audio", quality, out_dir, progress)
            except Exception:  # noqa: BLE001
                continue
            info["fallback"] = "soundcloud"
            return info
        raise RuntimeError("YouTube не даёт скачать с сервера, а на SoundCloud этот трек не нашёлся") from e


SOURCES = {
    "ytmusic": "YouTube Music",
    "soundcloud": "SoundCloud",
    "youtube": "YouTube",
}

_ytmusic = None


def _search_ytmusic(query: str, limit: int) -> list[dict]:
    global _ytmusic
    from ytmusicapi import YTMusic

    if _ytmusic is None:
        _ytmusic = YTMusic()
    results = []
    for e in _ytmusic.search(query, filter="songs", limit=limit)[:limit]:
        if not e.get("videoId"):
            continue
        thumbs = e.get("thumbnails") or []
        thumb = thumbs[-1]["url"] if thumbs else None
        if thumb:
            thumb = re.sub(r"=w\d+-h\d+", "=w544-h544", thumb)
        results.append({
            "source": "ytmusic",
            "id": e["videoId"],
            "title": e.get("title"),
            "uploader": ", ".join(a["name"] for a in e.get("artists") or []),
            "album": (e.get("album") or {}).get("name"),
            "duration": e.get("duration_seconds"),
            "url": f"https://music.youtube.com/watch?v={e['videoId']}",
            "thumbnail": thumb,
        })
    return results


def _search_ytdlp(source: str, query: str, limit: int) -> list[dict]:
    prefix = "scsearch" if source == "soundcloud" else "ytsearch"
    opts = _base_opts()
    opts.update({"extract_flat": "in_playlist", "skip_download": True})
    # у SoundCloud часть треков — 30-секундные превью, берём с запасом
    fetch = limit * 2 if source == "soundcloud" else limit
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(f"{prefix}{fetch}:{query}", download=False)
    results = []
    for e in info.get("entries") or []:
        if not e or not e.get("id"):
            continue
        if source == "soundcloud" and (e.get("duration") or 0) <= 31:
            continue
        thumbs = e.get("thumbnails") or []
        if source == "soundcloud":
            url = e.get("webpage_url") or f"https://api.soundcloud.com/tracks/{e['id']}"
        else:
            url = f"https://www.youtube.com/watch?v={e['id']}"
        results.append({
            "source": source,
            "id": str(e["id"]),
            "title": e.get("title"),
            "uploader": e.get("channel") or e.get("uploader"),
            "album": None,
            "duration": e.get("duration"),
            "url": url,
            "thumbnail": thumbs[-1]["url"] if thumbs else None,
        })
    return results[:limit]


def search_source(source: str, query: str, limit: int = 8) -> list[dict]:
    if source == "ytmusic":
        return _search_ytmusic(query, limit)
    if source in ("youtube", "soundcloud"):
        return _search_ytdlp(source, query, limit)
    raise ValueError(f"Неизвестный источник: {source}")


def search_music(query: str, limit: int = 10, source: str = "all") -> list[dict]:
    """Ищет песни по названию.

    source: ytmusic | soundcloud | youtube | all (все источники параллельно).
    Возвращает список {source, id, title, uploader, album, duration, url, thumbnail}.
    """
    if source != "all":
        return search_source(source, query, limit)

    from concurrent.futures import ThreadPoolExecutor

    per_source = {"ytmusic": max(limit // 2, 3), "soundcloud": max(limit // 4, 2), "youtube": max(limit // 4, 2)}
    with ThreadPoolExecutor(len(per_source)) as pool:
        futures = {s: pool.submit(search_source, s, query, n) for s, n in per_source.items()}
    results, errors = [], []
    for s, fut in futures.items():
        try:
            results.extend(fut.result())
        except Exception as e:  # noqa: BLE001 — один упавший источник не ломает поиск
            errors.append(f"{s}: {e}")
    if not results and errors:
        raise RuntimeError("; ".join(errors))
    seen, unique = set(), []
    for r in results:
        key = (r["source"] if r["source"] == "soundcloud" else "yt", r["id"])
        if key not in seen:
            seen.add(key)
            unique.append(r)
    return unique


def friendly_error(e: Exception) -> str:
    """Переводит ошибку yt-dlp в понятное сообщение."""
    msg = str(e)
    low = msg.lower()
    if "unsupported url" in low:
        return "Этот сайт не поддерживается"
    if "не нашёлся" in low:
        return msg
    if "not a bot" in low:
        return "YouTube не даёт скачать видео с сервера (защита от ботов). MP3 по этой ссылке скачать можно"
    if "logged-in" in low or "login" in low or "cookies" in low or "sign in" in low:
        return "Сайт требует входа в аккаунт. Добавь cookies.txt (см. README)"
    if "private" in low:
        return "Это приватное видео"
    if "not available" in low or "unavailable" in low or "removed" in low or "404" in low:
        return "Видео недоступно или удалено"
    if "geo" in low and "restrict" in low:
        return "Видео недоступно в этой стране"
    if "no video formats" in low or "requested format" in low:
        return "По ссылке нет видео, которое можно скачать"
    if "timed out" in low or "connection" in low:
        return "Сайт не отвечает. Попробуй ещё раз"
    first = msg.replace("ERROR: ", "").strip().splitlines()[0] if msg.strip() else ""
    return (first[:200] or "Не получилось скачать")


def format_duration(seconds) -> str:
    if not seconds:
        return ""
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02}:{s:02}" if h else f"{m}:{s:02}"


def cleanup_old(ttl: int = FILE_TTL) -> None:
    """Удаляет папки задач старше ttl секунд."""
    now = time.time()
    for folder in DOWNLOAD_DIR.iterdir():
        if folder.is_dir() and now - folder.stat().st_mtime > ttl:
            shutil.rmtree(folder, ignore_errors=True)
