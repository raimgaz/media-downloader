FROM python:3.13-slim

# ffmpeg — склейка видео и mp3; deno — JS-движок, без него yt-dlp не скачивает с YouTube
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY --from=denoland/deno:bin-2.9.1 /deno /usr/local/bin/deno

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY core core
COPY server server
COPY bot bot
COPY web web

ENV PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8 \
    DOWNLOAD_DIR=/tmp/downloads \
    RUN_BOT=1 \
    PORT=10000

CMD uvicorn server.app:app --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips='*'
