# grab. — сайт и Telegram-бот для скачивания видео и музыки

- Скачивание видео и аудио (mp3) из YouTube, TikTok, Instagram, VK, X, Reddit, SoundCloud и [1000+ сайтов](https://github.com/yt-dlp/yt-dlp/blob/master/supportedsites.md)
- Несколько ссылок за раз (каждая с новой строки)
- Поиск песен по названию и скачивание в mp3

## Что нужно

- Python 3.10+
- [ffmpeg](https://ffmpeg.org/) в PATH (склейка видео и конвертация в mp3)
- Node.js или Deno (yt-dlp нужен JS-движок для YouTube)

## Запуск

```
pip install -r requirements.txt
copy .env.example .env
```

**Сайт** — `run_site.bat` или:

```
python -m uvicorn server.app:app --host 0.0.0.0 --port 8000
```

Откроется на http://localhost:8000

**Бот** — получи токен у [@BotFather](https://t.me/BotFather), впиши в `.env` (`BOT_TOKEN=...`, `BOT_USERNAME=...`), затем `run_bot.bat` или:

```
python bot/bot.py
```

Как пользоваться ботом:
- прислать ссылки → кнопки «Видео» / «Аудио mp3»
- прислать название песни → список найденных треков → нажать на нужный

## Структура

```
core/downloader.py   общий модуль: скачивание, поиск, очистка (yt-dlp)
server/app.py        FastAPI: /api/jobs, /api/files/{id}, /api/search
web/                 сайт (index.html, style.css, app.js)
bot/bot.py           Telegram-бот (aiogram 3)
downloads/           временные файлы, удаляются через FILE_TTL (час)
```

## Ограничения и советы

- **50 МБ в Telegram.** Обычный бот не может отправить файл больше 50 МБ. Если задать `PUBLIC_URL` (адрес сайта, доступный из интернета) и запустить сайт, бот вместо файла пришлёт ссылку на скачивание. Можно поднять свой [Telegram Bot API сервер](https://github.com/tdlib/telegram-bot-api) и указать `BOT_API_URL` — тогда лимит 2 ГБ.
- **Instagram, Vimeo, закрытые видео** иногда требуют вход в аккаунт. Экспортируй куки браузера в `cookies.txt` (формат Netscape, например расширением «Get cookies.txt LOCALLY») и укажи `COOKIES_FILE=cookies.txt`.
- **Обновляй yt-dlp** — сайты часто меняются: `pip install -U "yt-dlp[default]"`.
- Скачивай только то, на что у тебя есть права.
