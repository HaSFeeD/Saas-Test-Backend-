# SaaS backend — «поиск по человеку → платный отчёт»

Тестовое задание: бэкенд для SaaS по модели cheaterbuster.com.
Пользователь запускает поиск по человеку, сразу видит превью результатов, а полный отчёт открывает за кредит.
Кредиты покупаются разовыми пакетами или приходят по подписке через Stripe.

План, архитектура и дальнейшие шаги описаны в **[PLAN.md](PLAN.md)**.

**Стек:** Node.js 22 · TypeScript · Fastify 5 · zod · PostgreSQL · Stripe.
Очередь фоновых задач хранится в самом PostgreSQL, Redis не нужен.

## Что реализовано

- **Аутентификация:** регистрация с согласием на ToS, вход, JWT access-токены и ротация refresh-токенов (при повторном использовании старого токена закрываются все сессии), сброс пароля по email.
- **Поиск:** асинхронный, выполняется воркером через очередь с ретраями. Бесплатно 3 поиска в сутки, для подписчиков 50.
- **Paywall:** пока отчёт закрыт, видны число совпадений, платформы и замаскированные имена. Открытие стоит 1 кредит, повторное открытие ничего не списывает, отчёт без совпадений открывается бесплатно.
- **Биллинг:** Stripe Checkout для пакетов (1, 5, 15 отчётов) и подписки Pro, вебхуки с проверкой подписи и защитой от повторной доставки, портал управления подпиской.
- **Кредиты:** все движения записываются в журнал (`credit_ledger`). Уйти в минус или списать кредит дважды при параллельных запросах нельзя.
- **Загрузка фото:** JPEG/PNG/WebP, тип проверяется по содержимому файла, фото видит только владелец.
- **Приватность:** публичный запрос «исключите меня из поиска» с подтверждением по email, экспорт данных и удаление аккаунта (GDPR).
- **Админка:** статистика, список пользователей, ручное начисление кредитов.
- **Инфраструктура:** валидация конфигурации, миграции, rate limiting, helmet, CORS, единый формат ошибок, health-checks, OpenAPI/Swagger.

## Запуск

Нужны Node.js 22+ и PostgreSQL 14+.

```bash
createdb saas
cp .env.example .env     # задайте JWT_SECRET (не короче 32 символов) и DATABASE_URL
npm install
npm run dev              # миграции применяются при старте, воркер работает в том же процессе
```

После запуска откройте Swagger UI: http://localhost:3000/docs

Без `STRIPE_SECRET_KEY` включается dev-режим оплаты: checkout сразу начисляет кредиты.
Поэтому весь сценарий можно пройти без аккаунта Stripe. В production без ключа сервис не запустится.

### Команды

| Команда | Что делает |
|---|---|
| `npm run dev` | API и воркер с hot reload |
| `npm run worker` | воркер отдельным процессом |
| `npm run migrate` | применить миграции |
| `npm run make-admin -- me@example.com` | выдать пользователю роль admin |
| `npm run typecheck` | проверка типов |
| `npm run build && npm start` | production-сборка и запуск |

В production API и воркер удобно запускать отдельными процессами: API с `RUN_WORKER=false` и `node dist/worker.js`.

## Сценарий через curl

```bash
B=http://localhost:3000
TOKEN=$(curl -s -X POST $B/v1/auth/register -H 'content-type: application/json' \
  -d '{"email":"demo@example.com","password":"demo-password-1","acceptTerms":true}' | jq -r .accessToken)
H="authorization: Bearer $TOKEN"

# 1. Запустить поиск
ID=$(curl -s -X POST $B/v1/searches -H "$H" -H 'content-type: application/json' \
  -d '{"firstName":"Jessica","lastName":"Smith","city":"Austin","age":29}' | jq -r .id)

# 2. Превью: совпадения замаскированы
curl -s $B/v1/searches/$ID -H "$H" | jq

# 3. Купить пакет (в dev-режиме кредиты начисляются сразу)
curl -s -X POST $B/v1/billing/checkout -H "$H" -H 'content-type: application/json' -d '{"productId":"pack_1"}'

# 4. Открыть полный отчёт за 1 кредит
curl -s -X POST $B/v1/searches/$ID/unlock -H "$H" | jq
```

## API (`/v1`)

| Раздел | Эндпоинты |
|---|---|
| **auth** | `POST register · login · refresh · logout · forgot-password · reset-password` |
| **me** | `GET /me` · `GET /me/export` · `DELETE /me` |
| **billing** | `GET products` · `POST checkout` · `POST portal` · `GET ledger` · `POST webhook` |
| **uploads** | `POST /uploads` (multipart, поле `file`) · `GET /uploads/:id` |
| **searches** | `POST /` · `GET /` · `GET /:id` · `POST /:id/unlock` |
| **privacy** | `POST /privacy/opt-out` · `GET /privacy/opt-out/confirm` |
| **admin** | `GET stats` · `GET users` · `POST users/:id/credits` |

Ошибки всегда приходят в формате `{ "error": { "code", "message", "details?" } }`.
Полное описание запросов и ответов — в Swagger (`/docs`).

## Подключение Stripe

1. Создайте в Stripe цены для четырёх продуктов и пропишите `STRIPE_SECRET_KEY` и `STRIPE_PRICE_*` в `.env`.
2. Локально пробросьте вебхуки: `stripe listen --forward-to localhost:3000/v1/billing/webhook` и укажите выданный секрет в `STRIPE_WEBHOOK_SECRET`.
3. Обрабатываемые события: `checkout.session.completed`, `checkout.session.async_payment_succeeded`, `invoice.paid`, `customer.subscription.created|updated|deleted`.

## Структура

```
src/
  app.ts                сборка приложения; все зависимости передаются через Ctx
  server.ts, worker.ts  точки входа API и воркера
  config.ts             валидация переменных окружения
  db.ts                 пул соединений, транзакции, миграции
  jobs/                 очередь на PostgreSQL (SKIP LOCKED, ретраи с backoff) и обработчики задач
  lib/                  журнал кредитов, криптография, ошибки, почта, хранилище файлов, хеширование PII
  modules/<name>/       роуты и сервисы по доменам
  providers/            интерфейс источника данных и mock-реализация
migrations/             SQL-миграции
```

## Источник данных

Сейчас результаты поиска выдаёт детерминированный mock-провайдер: одинаковый запрос даёт одинаковый ответ, реальных данных о людях нет.
Чтобы подключить реальный источник, нужно реализовать интерфейс `SearchProvider` (`src/providers/types.ts`),
зарегистрировать его в `src/providers/index.ts` и выставить `SEARCH_PROVIDER=<name>`. В production mock запустить нельзя.
Перед подключением источник нужно проверить юридически (GDPR, CCPA, BIPA, условия площадок) — подробнее в PLAN.md, разделы 7–8.
