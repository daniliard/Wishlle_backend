# Wishlle Backend

Власний бекенд-сервіс для застосунку Wishlle на FastAPI. Складається з трьох
модулів: `parser` (парсинг метаданих за URL), `auth` (верифікація Telegram
initData та Google id_token), `notifier` (планувальник щоденних нагадувань).

Сервіс **не** має власної БД. Усі дані зберігаються у Directus (headless CMS
поверх PostgreSQL); бекенд взаємодіє з ним через REST API.

## Установка

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                # відредагуй під свої значення
```

## Запуск

```bash
uvicorn app.main:app --reload --port 8000
```

Документація OpenAPI буде доступна за `http://localhost:8000/docs`.

## Структура

```
app/
├── main.py              # точка входу, lifespan з планувальником
├── core/
│   ├── config.py        # Settings (pydantic-settings)
│   └── directus.py      # async HTTP-клієнт до Directus REST API
├── parser/              # POST /api/parse-url
├── auth/                # POST /api/auth/telegram, /api/auth/google
└── notifier/            # AsyncIOScheduler, cron 09:00 Europe/Kyiv
```

## Інтеграція з Directus

Авторизація — статичний service token. У Directus адмінці:

1. Створити роль (наприклад, "Backend Service") з потрібними правами на
   колекції `users`, `events`, `wishes`.
2. Створити користувача з цією роллю.
3. У профілі користувача згенерувати Static Token, скопіювати у
   `DIRECTUS_TOKEN` в `.env`.

Очікувана схема:

- `users` — поля `id`, `telegram_id`, `google_sub`, `username`, `full_name`,
  `locale`, `email`, `avatar_url`
- `events` — поля `id`, `title`, `event_date` (Date), `owner` (M2O → users)
- `wishes` — поля `id`, `title`, `owner` (M2O → users), `date_created` (auto)
- `notifications` — поля `id`, `user` (M2O → users), `event` (M2O → events),
  `days_before` (Integer), `date_created` (auto). Використовується для
  запобігання повторному надсиланню одного й того ж нагадування.

Якщо у твоїй схемі поля називаються інакше — поправ змінні `DIRECTUS_*_FIELD`
у `.env`, код тоді не чіпай.

## Endpoint'и

- `POST /api/parse-url` — отримати title/description/image/price за URL
- `POST /api/auth/telegram` — авторизація через Telegram Mini App initData
- `POST /api/auth/google` — авторизація через Google OAuth id_token
- `GET /health` — health check

## Що робить Notifier

Щодня о 09:00 (Europe/Kyiv) сервіс:

1. Витягує з Directus події, дата яких настає через 7, 3, 1 або 0 днів
   (`NOTIFIER_REMINDER_DAYS`).
2. Для кожної події перевіряє колекцію `notifications` у Directus — чи не
   було вже надіслано нагадування для цієї пари (користувач, подія,
   кількість днів). Якщо було — пропускає, що запобігає повторному
   надсиланню.
3. Надсилає повідомлення власнику через Telegram Bot API і фіксує запис у
   колекції `notifications`.
4. Опціонально (якщо `NOTIFIER_WISHES_ENABLED=true`) сповіщає про бажання,
   що були додані за останні `NOTIFIER_WISHES_LOOKBACK_HOURS` годин.
