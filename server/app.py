"""Веб-сервер: API для скачивания и сам сайт."""

import asyncio
import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI, HTTPException, Query  # noqa: E402
from fastapi.responses import FileResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402

from core import downloader as dl  # noqa: E402

STATIC_DIR = Path(__file__).resolve().parent.parent / "web"
MAX_URLS = 20
log = logging.getLogger("server")

executor = ThreadPoolExecutor(max_workers=3)
jobs: dict[str, dict] = {}
jobs_lock = threading.Lock()


async def cleanup_loop():
    while True:
        dl.cleanup_old()
        cutoff = time.time() - dl.FILE_TTL
        with jobs_lock:
            for job_id in [k for k, v in jobs.items() if v["created"] < cutoff]:
                jobs.pop(job_id, None)
        await asyncio.sleep(600)


async def keepalive_loop(url: str):
    """Бесплатный хостинг (Render free) усыпляет сервис через 15 минут без запросов.
    Раз в 10 минут стучимся сами себе через публичный адрес — сервис не засыпает."""
    import urllib.request

    while True:
        await asyncio.sleep(600)
        try:
            await asyncio.to_thread(urllib.request.urlopen, f"{url}/healthz", timeout=30)
        except Exception as e:  # noqa: BLE001
            log.warning("keepalive не удался: %s", e)


@asynccontextmanager
async def lifespan(_app):
    tasks = [asyncio.create_task(cleanup_loop())]
    keepalive_url = (os.getenv("KEEPALIVE_URL") or os.getenv("RENDER_EXTERNAL_URL") or "").rstrip("/")
    if keepalive_url:
        tasks.append(asyncio.create_task(keepalive_loop(keepalive_url)))
    if os.getenv("RUN_BOT") == "1" and os.getenv("BOT_TOKEN"):
        from bot.bot import run_polling

        tasks.append(asyncio.create_task(run_polling()))
    yield
    for task in tasks:
        task.cancel()


app = FastAPI(title="grab.", lifespan=lifespan)


class JobRequest(BaseModel):
    urls: list[str] = Field(min_length=1)
    mode: str = "video"
    quality: str = "best"


def _public(job: dict) -> dict:
    return {k: v for k, v in job.items() if k not in ("path", "created")}


def _run(job_id: str, out_dir: Path):
    job = jobs[job_id]
    job["status"] = "downloading"

    def on_progress(p):
        job["progress"] = round(p, 1)

    try:
        info = dl.download_smart(job["url"], job["mode"], job["quality"], out_dir, on_progress)
        job.update(
            status="done",
            progress=100,
            title=info["title"],
            size=info["size"],
            filename=info["path"].name,
            path=str(info["path"]),
        )
    except Exception as e:  # noqa: BLE001
        log.warning("download failed %s: %s", job["url"], e)
        job.update(status="error", error=dl.friendly_error(e))


@app.post("/api/jobs")
def create_jobs(req: JobRequest):
    urls = []
    for raw in req.urls:
        urls.extend(dl.extract_urls(raw) or [])
    urls = list(dict.fromkeys(urls))[:MAX_URLS]
    if not urls:
        raise HTTPException(400, "Не нашли ни одной ссылки")
    mode = "audio" if req.mode == "audio" else "video"
    created = []
    for url in urls:
        job_id, out_dir = dl.new_job_dir()
        job = {
            "id": job_id,
            "url": url,
            "mode": mode,
            "quality": req.quality,
            "status": "queued",
            "progress": 0,
            "title": None,
            "created": time.time(),
        }
        with jobs_lock:
            jobs[job_id] = job
        executor.submit(_run, job_id, out_dir)
        created.append(_public(job))
    return {"jobs": created}


@app.get("/api/jobs")
def get_jobs(ids: str = Query(...)):
    result = []
    for job_id in ids.split(","):
        job = jobs.get(job_id)
        if job:
            result.append(_public(job))
    return {"jobs": result}


@app.get("/api/files/{job_id}")
def get_file(job_id: str):
    path = dl.find_job_file(job_id)
    if not path:
        raise HTTPException(404, "Файл не найден или уже удалён")
    return FileResponse(path, filename=path.name)


@app.get("/api/config")
def config():
    return {"bot_username": os.getenv("BOT_USERNAME", "").lstrip("@"), "sources": dl.SOURCES}


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.get("/api/search")
def search(
    q: str = Query(..., min_length=1, max_length=200),
    limit: int = 10,
    source: str = "all",
):
    if source != "all" and source not in dl.SOURCES:
        raise HTTPException(400, "Неизвестный источник")
    try:
        return {"results": dl.search_music(q, min(max(limit, 1), 20), source)}
    except Exception as e:  # noqa: BLE001
        log.warning("search failed: %s", e)
        raise HTTPException(502, "Поиск сейчас не работает. Попробуй позже") from e


app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="web")
