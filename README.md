# People Search API

A production-grade backend for a people-search SaaS: a user runs a search for a person,
sees a free preview of the matches, and unlocks the full report with credits. Credits are
bought as one-off packs or granted by a monthly subscription through Stripe.

The service is built around **third-party integrations and automation**: a hand-rolled Stripe
REST client with HMAC webhook verification, a Telegram bot (inbound webhook + outbound
notifications), signed outbound webhooks to customer endpoints, live search status over
WebSocket, and a resilient async HTTP client (retries, backoff, rate-limit handling, proxy
support) that every outbound call goes through.

**Stack:** Python 3.11+ · FastAPI · asyncpg (PostgreSQL) · httpx · Pydantic v2 · PyJWT.
Background jobs run on a Postgres-backed queue — no Redis or external broker required.

## Features

- **Authentication** — register (with ToS consent), login, JWT access tokens, rotating
  refresh tokens with reuse detection (a replayed token revokes every session), password
  reset over e-mail.
- **People search** — asynchronous, executed by a worker through a pluggable provider.
  Free tier is rate-limited per day; subscribers get a higher daily cap.
- **Paywall** — a locked report shows only match count, platforms and masked names
  (`J****** S.`). Unlocking costs one credit; unlocking again is free, and a report with no
  matches unlocks for free.
- **Billing** — Stripe Checkout for packs and subscriptions, billing portal, and webhook
  handling with signature verification and idempotent, replay-safe fulfilment.
- **Credits ledger** — every balance change is journaled; balances cannot go negative and
  concurrent spends cannot double-charge.
- **Integrations**
  - *Stripe* over the raw REST API (no SDK): customers, checkout, portal, subscription
    cancel, and manual HMAC-SHA256 webhook verification.
  - *Telegram Bot API*: inbound webhook (secret-token authenticated) and outbound search
    notifications.
  - *Outbound webhooks*: customers register an HTTPS endpoint and receive signed
    `search.completed` events (`X-Webhook-Signature: t=…,v1=HMAC`).
  - *WebSocket*: `ws /v1/ws/searches/{id}` streams live status until the search finishes.
- **Photo uploads** — JPEG/PNG/WebP, validated by file signature (not the declared MIME),
  private to the owner.
- **Privacy** — public opt-out ("remove me") with e-mail confirmation; data export and
  account deletion (GDPR).
- **Operations** — config validation, per-route and global rate limiting, CORS, uniform
  error envelope, health checks, and an OpenAPI schema served at `/docs`.

## Running locally

Requires Python 3.11+ and PostgreSQL 14+.

```bash
createdb saas
cp .env.example .env            # set JWT_SECRET (>= 32 chars) and DATABASE_URL
python -m venv .venv && source .venv/bin/activate
pip install -e .
uvicorn asgi:app --reload       # migrations run automatically on startup
```

Open the interactive API docs at http://localhost:8000/docs

Without `STRIPE_SECRET_KEY` the service uses a **dev billing provider**: checkout completes
instantly and grants the credits, so the whole flow can be exercised without a Stripe account.
In production the service refuses to start without a Stripe key.

### Worker

By default the worker runs in-process (`RUN_WORKER=true`). To scale it separately, set
`RUN_WORKER=false` for the API and run the worker on its own:

```bash
python worker.py
```

### Scripts

| Command | Description |
|---|---|
| `uvicorn asgi:app --reload` | run the API (worker in-process) |
| `python worker.py` | run the job worker standalone |
| `python migrate.py` | apply database migrations |
| `python make_admin.py user@example.com` | grant a user the admin role |
| `ruff check app` | lint |

## Example flow

```bash
B=http://localhost:8000
TOKEN=$(curl -s -X POST $B/v1/auth/register -H 'content-type: application/json' \
  -d '{"email":"demo@example.com","password":"demo-password-1","acceptTerms":true}' | jq -r .accessToken)
H="authorization: Bearer $TOKEN"

# 1. Start a search
ID=$(curl -s -X POST $B/v1/searches -H "$H" -H 'content-type: application/json' \
  -d '{"firstName":"Jessica","lastName":"Smith","city":"Austin","age":29}' | jq -r .id)

# 2. Preview — matches are masked and locked
curl -s $B/v1/searches/$ID -H "$H" | jq

# 3. Buy a pack (dev mode grants credits immediately)
curl -s -X POST $B/v1/billing/checkout -H "$H" -H 'content-type: application/json' -d '{"productId":"pack_1"}'

# 4. Unlock the full report for one credit
curl -s -X POST $B/v1/searches/$ID/unlock -H "$H" | jq
```

## API

All endpoints are under `/v1`. Errors use `{"error": {"code", "message", "details?"}}`.

| Area | Endpoints |
|---|---|
| **auth** | `POST register · login · refresh · logout · forgot-password · reset-password` |
| **me** | `GET /me` · `GET /me/export` · `DELETE /me` · `PUT/DELETE /me/webhook` · `POST /me/telegram/link` |
| **billing** | `GET products` · `POST checkout` · `POST portal` · `GET ledger` · `POST webhook` |
| **uploads** | `POST /uploads` (multipart `file`) · `GET /uploads/{id}` |
| **searches** | `POST /` · `GET /` · `GET /{id}` · `POST /{id}/unlock` |
| **privacy** | `POST /privacy/opt-out` · `GET /privacy/opt-out/confirm` |
| **admin** | `GET stats` · `GET users` · `POST users/{id}/credits` |
| **realtime** | `WS /v1/ws/searches/{id}?token=…` |
| **telegram** | `POST /telegram/webhook` |

See the full request/response schema in Swagger at `/docs`.

## Connecting Stripe

1. Create Prices for the four products and set `STRIPE_SECRET_KEY` and `STRIPE_PRICE_*` in `.env`.
2. Forward webhooks locally: `stripe listen --forward-to localhost:8000/v1/billing/webhook`,
   and put the signing secret in `STRIPE_WEBHOOK_SECRET`.
3. Events handled: `checkout.session.completed`, `checkout.session.async_payment_succeeded`,
   `invoice.paid`, `customer.subscription.created|updated|deleted`.

## Connecting Telegram

1. Create a bot with [@BotFather](https://t.me/BotFather) and set `TELEGRAM_BOT_TOKEN`.
2. Choose a `TELEGRAM_WEBHOOK_SECRET` and register the webhook so Telegram calls
   `POST {PUBLIC_URL}/v1/telegram/webhook`.
3. A user sends `/start` to the bot, gets their chat id, and links it via `POST /v1/me/telegram/link`.

## Project layout

```
app/
  main.py              app factory, lifespan, dependency wiring
  config.py            environment configuration (validated)
  db.py                asyncpg pool, transactions, migrations
  security.py          password hashing, tokens, JWT
  deps.py              auth dependencies and rate limiting
  errors.py            error types and handlers
  credits.py           the credit ledger (the only place balances change)
  pii.py               keyed hashing for opt-out identifiers
  search_service.py    search execution, masking, report rendering
  billing_*.py         products, Stripe event handling, fulfilment
  jobs/                Postgres-backed queue, worker and job handlers
  integrations/        http client, stripe, telegram, webhooks, mailer, storage
  providers/           search-provider interface and a mock implementation
  modules/             routers: auth, me, billing, uploads, searches, privacy, admin, ws, telegram
migrations/            SQL migrations
```

## Data source

Search results are produced by a deterministic **mock provider**: the same input always
yields the same output and no real person's data is involved. To plug in a real source,
implement the `SearchProvider` interface in `app/providers`, register it in
`app/providers/registry.py`, and set `SEARCH_PROVIDER`. The mock provider is rejected in
production. See `PLAN.md` (sections 7–8) for the legal review a real data source requires.
