-- Reference schema — tables already exist in Supabase. Do NOT run this.
-- This file documents the expected structure only.

-- CREATE TABLE users (
--   id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
--   telegram_id BIGINT UNIQUE NOT NULL,
--   username TEXT,
--   first_name TEXT,
--   referral_code VARCHAR(8) UNIQUE NOT NULL,
--   subscription_status TEXT NOT NULL DEFAULT 'trial', -- trial | paid | free
--   trial_ends_at TIMESTAMPTZ,
--   paid_until TIMESTAMPTZ,
--   alerts_paused BOOLEAN NOT NULL DEFAULT FALSE,
--   is_active BOOLEAN NOT NULL DEFAULT TRUE,
--   created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
-- );

-- CREATE TABLE user_profiles (
--   id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
--   telegram_id BIGINT UNIQUE NOT NULL REFERENCES users(telegram_id),
--   role TEXT NOT NULL,
--   stack TEXT NOT NULL,
--   categories TEXT[] NOT NULL DEFAULT '{}',
--   updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
-- );

-- CREATE TABLE category_weights (
--   id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
--   telegram_id BIGINT NOT NULL REFERENCES users(telegram_id),
--   category TEXT NOT NULL,
--   weight NUMERIC NOT NULL DEFAULT 1.0,
--   UNIQUE(telegram_id, category)
-- );

-- CREATE TABLE tools (
--   id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
--   title TEXT NOT NULL,
--   description TEXT,
--   source_url TEXT UNIQUE NOT NULL,
--   source TEXT NOT NULL,
--   is_tool BOOLEAN,
--   is_processed BOOLEAN NOT NULL DEFAULT FALSE,
--   categories TEXT[] NOT NULL DEFAULT '{}',
--   tags TEXT[] NOT NULL DEFAULT '{}',
--   published_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
-- );

-- CREATE TABLE alerts (
--   id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
--   telegram_id BIGINT NOT NULL REFERENCES users(telegram_id),
--   tool_id UUID NOT NULL REFERENCES tools(id),
--   score INTEGER NOT NULL DEFAULT 0,
--   reason TEXT,
--   alert_type TEXT NOT NULL, -- priority | standard | generic
--   sent_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
-- );

-- CREATE TABLE alert_feedback (
--   id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
--   alert_id UUID NOT NULL REFERENCES alerts(id),
--   telegram_id BIGINT NOT NULL REFERENCES users(telegram_id),
--   feedback TEXT NOT NULL, -- useful | not_relevant
--   created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
--   UNIQUE(alert_id, telegram_id)
-- );

-- CREATE TABLE referrals (
--   id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
--   referrer_telegram_id BIGINT NOT NULL REFERENCES users(telegram_id),
--   referred_telegram_id BIGINT NOT NULL REFERENCES users(telegram_id),
--   status TEXT NOT NULL DEFAULT 'pending', -- pending | converted
--   created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
--   converted_at TIMESTAMPTZ
-- );

-- CREATE TABLE webhook_events (
--   id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
--   event_type TEXT NOT NULL,
--   payload JSONB NOT NULL,
--   received_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
-- );

-- CREATE TABLE daily_stats (
--   id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
--   date DATE NOT NULL,
--   stat_key TEXT NOT NULL,
--   value INTEGER NOT NULL DEFAULT 0,
--   UNIQUE(date, stat_key)
-- );
