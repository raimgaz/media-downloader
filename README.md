# grab. — сайт и Telegram-бот для скачивания видео и музыки

- Скачивание видео и аудио (mp3) из YouTube, TikTok, Instagram, VK, X, Reddit, SoundCloud и [1000+ сайтов](https://github.com/yt-dlp/yt-dlp/blob/master/supportedsites.md)
- Несколько ссылок за раз (каждая с новой строки)
- Поиск песен по названию сразу в YouTube Music, SoundCloud и YouTube, скачивание в mp3 с обложкой

Бот: [@raim_musicbot](https://t.me/raim_musicbot) · Сайт: https://raim-music.onrender.com

## Хостинг на Render

Сервис `raim-music` (Docker, бесплатный тариф, Франкфурт) собирается из ветки `main` при каждом пуше.
Сайт и бот работают в одном процессе (`RUN_BOT=1`).

Переменные окружения на Render: `BOT_TOKEN`, `BOT_USERNAME`, `RUN_BOT=1`.
`RENDER_EXTERNAL_URL` Render задаёт сам — по нему бот даёт ссылки на большие файлы
и раз в 10 минут обращается к `/healthz`, чтобы бесплатный сервис не засыпал.

> Не запускай бота локально, пока он работает на Render: два процесса с одним токеном мешают друг другу.

## Локальный запуск

Нужны Python 3.10+, [ffmpeg](https://ffmpeg.org/) в PATH и Node.js или Deno (для YouTube).

```
pip install -r requirements.txt
copy .env.example .env
```

- Сайт: `run_site.bat` → http://localhost:8000
- Бот: впиши `BOT_TOKEN` в `.env`, запусти `run_bot.bat`
- Или всё вместе: `set RUN_BOT=1` и `run_site.bat`

## Структура

```
core/downloader.py   скачивание, поиск по источникам, очистка (yt-dlp, ytmusicapi)
server/app.py        FastAPI: /api/jobs, /api/files/{id}, /api/search, /healthz; запускает бота
web/                 сайт (index.html, style.css, app.js)
bot/bot.py           Telegram-бот (aiogram 3)
Dockerfile           образ для Render: Python + ffmpeg + deno
```

## Ограничения

- **50 МБ в Telegram.** Файлы больше бот отдаёт ссылкой на сайт (живёт час).
- **YouTube с серверов** иногда просит «подтвердить, что вы не бот». Помогает `cookies.txt` (экспорт кук браузера в формате Netscape) и переменная `COOKIES_FILE`.
- **Instagram, Vimeo, закрытые видео** могут требовать вход — тоже через `cookies.txt`.
- **Музыка VK** закрыта для сторонних приложений: поиск по ней требует логина в аккаунт VK и нарушает правила VK, поэтому его нет. Видео из VK по ссылке скачиваются.
- **Обновляй yt-dlp** — сайты часто меняются: `pip install -U "yt-dlp[default]"`.
