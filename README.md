# Briifbot

[@getbriifbot](https://t.me/getbriifbot) — Personalized AI & tech tool launch alerts on Telegram.

Briifbot monitors AI and tech tool launches globally, scores each one against your personal workflow profile using GPT-4.1-mini, and sends targeted Telegram alerts. Users get a 7-day free trial with full personalized alerts, then 1 generic alert/day unless subscribed.

## Stack

| Layer | Technology |
|-------|-----------|
| Bot framework | aiogram 3.x + FSM |
| Database | Supabase (PostgreSQL) |
| AI scoring | OpenAI GPT-4.1-mini |
| Scheduler | APScheduler (15-min cron) |
| Webhook server | aiohttp |
| Payments | InviteMember + Stripe |
| Deployment | Railway |

## Project structure

```
briifbot/
├── main.py                  # Entry point — starts bot + webhook server + scheduler
├── bot.py                   # aiogram handlers, FSM onboarding, all /commands
├── webhook_server.py        # aiohttp server for InviteMember webhooks
├── requirements.txt
├── .env.example
├── railway.toml
├── config/
│   └── settings.py          # All env vars + business constants
├── db/
│   ├── client.py            # Supabase CRUD operations
│   └── schema.sql           # Reference schema (tables already exist)
└── workers/
    ├── ingestion_worker.py  # RSS, GitHub Trending, Product Hunt
    ├── filter_worker.py     # GPT-4.1-mini tool classification
    ├── scoring_engine.py    # GPT-4.1-mini per-user scoring (0-100)
    ├── alert_engine.py      # Alert formatting + dispatch logic
    ├── feedback_engine.py   # ✅/❌ feedback → category weight updates
    ├── referral_engine.py   # Referral conversion + reward
    └── scheduler.py         # APScheduler job definitions
```

## Environment variables

Copy `.env.example` to `.env` and fill in:

| Variable | Description |
|----------|-------------|
| `TELEGRAM_BOT_TOKEN` | From [@BotFather](https://t.me/BotFather) |
| `SUPABASE_URL` | Your Supabase project URL |
| `SUPABASE_SERVICE_KEY` | Supabase service role key (Settings → API) |
| `OPENAI_API_KEY` | OpenAI API key with GPT-4.1-mini access |
| `STRIPE_SECRET_KEY` | Stripe secret key (live or test) |
| `INVITEMEMBER_WEBHOOK_SECRET` | From InviteMember dashboard → Webhooks |
| `WEBHOOK_PORT` | Port for aiohttp webhook server (default: 8080) |
| `PRODUCT_HUNT_API_KEY` | Optional — Product Hunt API v2 token |

## Local development

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Fill in .env values
python main.py
```

## Railway deployment

1. Connect this GitHub repo to a new Railway project
2. Add all environment variables in Railway → Variables
3. Railway auto-detects `railway.toml` and runs `python main.py`
4. Expose port 8080 (Railway → Settings → Networking → Add port)
5. Copy the Railway public URL and set it as your InviteMember webhook:
   `https://your-app.railway.app/webhook/invitemember`

## Alert scoring

| Score | Type | Delivery |
|-------|------|----------|
| ≥ 90 | 🚨 Priority | Immediate |
| 70–89 | 🔧 Standard | 9am UTC batch |
| < 70 | Suppressed | — |
| Free tier | 📋 Generic | 9am UTC, 1/day |

## Subscription tiers

| Tier | Alerts/day | Price |
|------|-----------|-------|
| Trial (7 days) | 3 personalized | Free |
| Free (post-trial) | 1 generic | Free |
| Monthly | 3 personalized | $29/mo |
| Annual | 3 personalized | $199/yr |

Referrals: 1 free month per successful conversion.

## Ingestion sources

- Hacker News Show HN
- Ben's Bites
- The Rundown AI
- TechCrunch AI
- Anthropic Blog
- OpenAI Blog
- GitHub Trending (AI repos only)
- Product Hunt (optional)

## Health check

`GET /health` → `{"status": "ok", "service": "briifbot"}`
