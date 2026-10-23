# Architecture & plan — People Search API

## 1. Product

A people-search SaaS (model: cheaterbuster.com).

1. The user registers (with ToS consent).
2. They start a **search** (name, age, city; optionally email/phone/username/photo).
3. The search runs **asynchronously** (a worker queries the data provider).
4. The user immediately sees a **preview**: how many matches, on which platforms — but the
   data is hidden (masked, no links or photos). This is the paywall.
5. To open the report they spend **1 credit**. Credits come from one-off **packs** or a
   monthly **subscription**.
6. An unlocked report stays open: profiles, photos, match confidence.

## 2. Stack and why

| Layer | Choice | Reason |
|---|---|---|
| Runtime | Python 3.11+ | matches the role (Python automation + API work) |
| HTTP API | FastAPI + Pydantic v2 | async, typed, OpenAPI out of the box |
| DB | PostgreSQL (asyncpg) | transactions, JSONB, `SKIP LOCKED` for the queue |
| Queue | `jobs` table in Postgres | no Redis → less infra, ACID with the data |
| HTTP client | httpx (async) | retries, backoff, rate limits, proxy for all integrations |
| Payments | Stripe REST (no SDK) | shows direct API work; checkout, portal, webhooks |
| Messaging | Telegram Bot API | inbound webhook + outbound notifications |
| Deploy | uvicorn + a worker process | any Python host (VPS, Render, Railway, Fly) |

## 3. Architecture

```
Client ──HTTP/WS──▶ FastAPI ──▶ PostgreSQL ◀── Worker (run_search, send_email,
                      │                                  deliver_webhook, notify_telegram)
                      ├─▶ Stripe (checkout / webhook)
                      ├─▶ Telegram (notify / inbound webhook)
                      ├─▶ Outbound signed webhooks ─▶ customer endpoint
                      └─▶ Storage (photos), Mailer
```

Every outbound call goes through one async `HttpClient` with retries, exponential backoff,
`Retry-After` handling and an optional egress proxy.

Principles:
- **Money/credits only move through the ledger** (`credit_ledger`); the balance updates in the
  same transaction as the ledger row, and the unique `(reason, ref_id)` key makes grants and
  spends idempotent.
- **Webhooks are idempotent** (the Stripe event id is stored in the same transaction as its
  effects; outbound deliveries are retried by the queue).
- **Data sources sit behind the `SearchProvider` interface**; a `mock` ships in the repo.
- The worker can run in-process (`RUN_WORKER=true`) or as a separate process (`python worker.py`).

## 4. Data model

`users`, `refresh_tokens`, `password_resets`, `credit_ledger`, `subscriptions`,
`webhook_events`, `uploads`, `searches`, `search_results`, `jobs`, `opt_outs`.
SQL: `migrations/001_init.sql`.

## 5. Integrations (the focus of this project)

| Integration | What it demonstrates |
|---|---|
| `HttpClient` | retries, exponential backoff + jitter, `Retry-After`/429 handling, proxy, timeouts |
| Stripe (REST) | bearer auth, form-encoded + bracket params, HMAC-SHA256 webhook verification, idempotency |
| Telegram | messenger API: token-in-path, JSON bodies, secret-token header on inbound webhook |
| Outbound webhooks | signing (`t=…,v1=HMAC`), delivery with retry via the job queue |
| WebSocket | token-authenticated live status stream |

## 6. Security & compliance

- Passwords: scrypt with a per-user salt; JWT access tokens (15 min) + rotating refresh
  tokens (only the hash is stored; reuse revokes all sessions).
- Rate limiting globally and tighter on auth/privacy; CORS allow-list; secrets kept out of logs.
- All input validated with Pydantic; uploads checked by file signature with a size cap.
- Free searches are capped per day — abuse protection.
- **Privacy by design:** ToS consent at signup; public opt-out with e-mail confirmation;
  data export and account deletion; searches for opted-out identifiers are blocked.

## 7. Next steps for a real launch

1. **Real data provider** (`app/providers/`): choose a legally admissible source (a licensed
   API / data broker, not scraping platforms against their ToS), pass legal review
   (GDPR, CCPA, BIPA for biometrics/faces, platform ToS), implement `SearchProvider`.
2. S3 storage for photos; antivirus/moderation on uploads.
3. Real e-mail provider (Resend/SES) + templates; e-mail verification at signup.
4. Observability (Sentry, Prometheus) and alerts on the queue and webhooks.
5. Anti-fraud: captcha at signup, IP/card limits.
6. CI/CD, staging, database backups.

## 8. Out of scope here

Scraping dating apps and face recognition on third-party photos are **not** implemented:
they are the main legal and reputational risk of this kind of business and are handled
separately (see §7.1). The backend is ready to accept any provider through the interface.
