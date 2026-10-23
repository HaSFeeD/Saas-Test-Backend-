# План: быстрый SaaS-бэкенд (по образцу cheaterbuster.com)

Цель: за один небольшой проект показать, как быстро поднимается production-ready бэкенд
для SaaS «поиск по человеку → отчёт за деньги», и оставить основу, на которой можно
запускать следующие проекты (меняется доменная часть, а каркас остаётся).

## 1. Что делает продукт (модель CheaterBuster)

1. Пользователь регистрируется (с согласием на ToS).
2. Запускает **поиск** (имя, возраст, город, опционально email/телефон/ник/фото).
3. Поиск выполняется **асинхронно** (воркер опрашивает внешние источники).
4. Пользователь сразу видит **превью-отчёт**: сколько совпадений, на каких платформах,
   но данные скрыты (маска, без ссылок и фото). Это paywall.
5. Чтобы открыть отчёт — тратит **1 кредит**. Кредиты покупаются пакетами
   (разово) или приходят по **подписке** (ежемесячно).
6. Отчёт открыт навсегда: профили, фото, уверенность совпадения.

## 2. Стек и почему

| Слой | Выбор | Причина |
|---|---|---|
| Рантайм | Node 22 + TypeScript | быстрый старт, один язык с фронтом |
| HTTP | Fastify 5 + zod | схемы = валидация + типы + OpenAPI из коробки |
| БД | PostgreSQL | транзакции, JSONB, `SKIP LOCKED` для очереди |
| Очередь | таблица `jobs` в Postgres | без Redis → меньше инфраструктуры, ACID вместе с данными |
| Платежи | Stripe (Checkout + Webhooks + Portal) | стандарт для SaaS, подписки и пакеты |
| Почта | интерфейс `Mailer` (console / Resend) | подмена без правок кода |
| Файлы | интерфейс `Storage` (local / позже S3) | то же |
| Деплой | `npm run build && npm start` + отдельный процесс воркера | любой Node-хостинг (VPS, Render, Railway, Fly) |

## 3. Архитектура

```
Client ──HTTP──▶ Fastify API ──▶ PostgreSQL ◀── Worker (jobs: run_search, send_email)
                    │                                    │
                    ├─▶ Stripe (checkout / webhook)       └─▶ SearchProvider (интерфейс)
                    └─▶ Storage (фото), Mailer
```

Модули (`src/modules/*`): `auth`, `me` (профиль/экспорт/удаление), `billing`, `searches`,
`uploads`, `privacy` (opt-out), `admin`. Каждый — плагин Fastify со своими роутами.

Принципы:
- **Деньги/кредиты только через леджер** (`credit_ledger`), баланс обновляется в одной
  транзакции с записью; уникальный ключ `(reason, ref_id)` даёт идемпотентность.
- **Webhooks идемпотентны** (таблица `webhook_events`).
- **Провайдеры источников — за интерфейсом** `SearchProvider`; в репо есть `mock`.
- Воркер можно запускать в том же процессе (`RUN_WORKER=true`) или отдельно (`npm run worker`).

## 4. Модель данных

`users`, `refresh_tokens`, `password_resets`, `credit_ledger`, `subscriptions`,
`webhook_events`, `uploads`, `searches`, `search_results`, `jobs`, `opt_outs`.
SQL: `migrations/001_init.sql`.

## 5. API (v1)

| Область | Эндпоинты |
|---|---|
| Auth | `POST /auth/register`, `/auth/login`, `/auth/refresh`, `/auth/logout`, `/auth/forgot-password`, `/auth/reset-password` |
| Me | `GET /me`, `GET /me/export`, `DELETE /me` |
| Billing | `GET /billing/products`, `POST /billing/checkout`, `POST /billing/portal`, `POST /billing/webhook`, `GET /billing/ledger` |
| Uploads | `POST /uploads` (multipart, фото), `GET /uploads/:id` |
| Searches | `POST /searches`, `GET /searches`, `GET /searches/:id`, `POST /searches/:id/unlock` |
| Privacy | `POST /privacy/opt-out`, `GET /privacy/opt-out/confirm` |
| Admin | `GET /admin/stats`, `GET /admin/users`, `POST /admin/users/:id/credits` |
| Ops | `GET /healthz`, `GET /readyz`, `GET /docs` (Swagger UI) |

## 6. Безопасность и соответствие

- Пароли: scrypt + соль; JWT access (15 мин) + ротируемые refresh-токены (хранится только хеш).
- Rate limit глобально и жёстче на auth; helmet; CORS по whitelist; redaction секретов в логах.
- Валидация всех входов zod; загрузки — проверка MIME по сигнатуре и лимит размера.
- Бесплатные поиски ограничены (по умолчанию 3 / 24 ч) — защита от злоупотреблений.
- **Privacy by design** (для такого типа продукта обязательно):
  - согласие с ToS при регистрации;
  - публичный **opt-out** («удалите меня из результатов») с подтверждением по email;
  - экспорт и удаление аккаунта (GDPR/CCPA);
  - запросы по людям из opt-out блокируются.

## 7. Этапы и оценка (как делаем «быстро»)

| Этап | Содержимое | Статус |
|---|---|---|
| 0 | Каркас: конфиг, БД, миграции, ошибки, логи | ✅ |
| 1 | Auth + me + privacy-эндпоинты | ✅ |
| 2 | Кредиты, Stripe, подписки, webhooks | ✅ |
| 3 | Очередь, воркер, поиск, paywall-отчёты, загрузка фото | ✅ |
| 4 | Admin, OpenAPI, README | ✅ |
| 5 | **Дальше (не входит в тестовый проект)** | ⏳ |

Этап 5 для боевого запуска:
1. **Реальный провайдер данных** (`src/providers/`) — выбрать юридически допустимый источник
   (лицензированный API/брокер данных, а не обход ToS площадок), пройти юр. проверку
   (GDPR, CCPA, BIPA для биометрии/лиц, ToS площадок), реализовать `SearchProvider`.
2. S3-хранилище для фото, антивирус/модерация загрузок.
3. Email-провайдер (Resend/SES) + шаблоны, подтверждение email при регистрации.
4. Sentry/метрики (Prometheus), алерты на очередь и webhooks.
5. Антифрод: капча на регистрации, fingerprint, лимиты по IP/карте.
6. CI/CD, staging, бэкапы БД.

## 8. Что мы **не** делаем в этом проекте

Скрапинг дейтинг-приложений и распознавание лиц по чужим фото не реализованы:
это главный юридический и репутационный риск такого бизнеса и решается отдельно
(см. п. 7.1). Бэкенд готов принять любой провайдер через интерфейс.
