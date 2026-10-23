CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE users (
  id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  email              text NOT NULL,
  password_hash      text NOT NULL,
  role               text NOT NULL DEFAULT 'user' CHECK (role IN ('user','admin')),
  credits            integer NOT NULL DEFAULT 0 CHECK (credits >= 0),
  stripe_customer_id text UNIQUE,
  tos_accepted_at    timestamptz NOT NULL,
  created_at         timestamptz NOT NULL DEFAULT now(),
  deleted_at         timestamptz
);
CREATE UNIQUE INDEX users_email_uq ON users (lower(email)) WHERE deleted_at IS NULL;

CREATE TABLE refresh_tokens (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id     uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  token_hash  text NOT NULL UNIQUE,
  expires_at  timestamptz NOT NULL,
  revoked_at  timestamptz,
  created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX refresh_tokens_user_idx ON refresh_tokens (user_id);

CREATE TABLE password_resets (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id     uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  token_hash  text NOT NULL UNIQUE,
  expires_at  timestamptz NOT NULL,
  used_at     timestamptz,
  created_at  timestamptz NOT NULL DEFAULT now()
);

-- Every credit movement. (reason, ref_id) is unique => idempotent grants/spends.
CREATE TABLE credit_ledger (
  id            bigserial PRIMARY KEY,
  user_id       uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  delta         integer NOT NULL CHECK (delta <> 0),
  balance_after integer NOT NULL,
  reason        text NOT NULL,
  ref_id        text NOT NULL,
  created_at    timestamptz NOT NULL DEFAULT now(),
  UNIQUE (reason, ref_id)
);
CREATE INDEX credit_ledger_user_idx ON credit_ledger (user_id, id DESC);

CREATE TABLE subscriptions (
  id                     uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id                uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  stripe_subscription_id text NOT NULL UNIQUE,
  plan                   text NOT NULL,
  status                 text NOT NULL,
  current_period_end     timestamptz,
  cancel_at_period_end   boolean NOT NULL DEFAULT false,
  created_at             timestamptz NOT NULL DEFAULT now(),
  updated_at             timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX subscriptions_user_idx ON subscriptions (user_id);

CREATE TABLE webhook_events (
  id           text PRIMARY KEY,
  type         text NOT NULL,
  processed_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE uploads (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id     uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  storage_key text NOT NULL,
  mime        text NOT NULL,
  size        integer NOT NULL,
  sha256      text NOT NULL,
  created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE searches (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id      uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  status       text NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','running','done','failed')),
  query        jsonb NOT NULL,
  photo_id     uuid REFERENCES uploads(id) ON DELETE SET NULL,
  result_count integer NOT NULL DEFAULT 0,
  error        text,
  unlocked_at  timestamptz,
  created_at   timestamptz NOT NULL DEFAULT now(),
  started_at   timestamptz,
  finished_at  timestamptz
);
CREATE INDEX searches_user_idx ON searches (user_id, created_at DESC);

CREATE TABLE search_results (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  search_id     uuid NOT NULL REFERENCES searches(id) ON DELETE CASCADE,
  platform      text NOT NULL,
  profile_url   text NOT NULL,
  display_name  text NOT NULL,
  age           integer,
  location      text,
  photo_url     text,
  confidence    real NOT NULL CHECK (confidence BETWEEN 0 AND 1),
  last_active_at timestamptz,
  raw           jsonb
);
CREATE INDEX search_results_search_idx ON search_results (search_id);

-- Postgres-backed queue (FOR UPDATE SKIP LOCKED)
CREATE TABLE jobs (
  id           bigserial PRIMARY KEY,
  type         text NOT NULL,
  payload      jsonb NOT NULL DEFAULT '{}',
  status       text NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','running','done','failed')),
  run_at       timestamptz NOT NULL DEFAULT now(),
  attempts     integer NOT NULL DEFAULT 0,
  max_attempts integer NOT NULL DEFAULT 5,
  locked_at    timestamptz,
  last_error   text,
  created_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX jobs_pick_idx ON jobs (run_at) WHERE status = 'queued';

-- Right-to-be-forgotten list. Only hashes are stored.
CREATE TABLE opt_outs (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  kind          text NOT NULL CHECK (kind IN ('email','phone','username')),
  value_hash    text NOT NULL,
  token_hash    text,
  confirmed_at  timestamptz,
  created_at    timestamptz NOT NULL DEFAULT now(),
  UNIQUE (kind, value_hash)
);
