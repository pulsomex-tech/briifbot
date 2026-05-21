import os
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_BOT_TOKEN: str = os.environ["TELEGRAM_BOT_TOKEN"]
SUPABASE_URL: str = os.environ.get("SUPABASE_URL", "https://rwvoxeuptrxohotprxpv.supabase.co")
SUPABASE_SERVICE_KEY: str = os.environ["SUPABASE_SERVICE_KEY"]
OPENAI_API_KEY: str = os.environ["OPENAI_API_KEY"]
STRIPE_SECRET_KEY: str = os.environ.get("STRIPE_SECRET_KEY", "")
INVITEMEMBER_WEBHOOK_SECRET: str = os.environ.get("INVITEMEMBER_WEBHOOK_SECRET", "")
WEBHOOK_PORT: int = int(os.environ.get("PORT") or os.environ.get("WEBHOOK_PORT", "8080"))
PRODUCT_HUNT_API_KEY: str | None = os.environ.get("PRODUCT_HUNT_API_KEY")

# Business rules
TRIAL_DAYS: int = 7
FREE_ALERTS_PER_DAY: int = 1
PAID_ALERTS_PER_DAY: int = 3
PRIORITY_SCORE_THRESHOLD: int = 90
STANDARD_SCORE_THRESHOLD: int = 70
MONTHLY_PRICE_USD: float = 29.0
ANNUAL_PRICE_USD: float = 199.0
REFERRAL_CODE_LENGTH: int = 8

# Scheduling
INGESTION_INTERVAL_MINUTES: int = 15
BATCH_ALERT_HOUR_UTC: int = 9
TRIAL_EXPIRY_CHECK_HOUR_UTC: int = 9

RSS_FEEDS = [
    {"name": "Hacker News Show HN", "url": "https://hnrss.org/show"},
    {"name": "Ben's Bites", "url": "https://bensbites.beehiiv.com/feed"},
    {"name": "The Rundown AI", "url": "https://www.therundown.ai/feed"},
    {"name": "TechCrunch AI", "url": "https://techcrunch.com/category/artificial-intelligence/feed/"},
    {"name": "Anthropic Blog", "url": "https://www.anthropic.com/rss.xml"},
    {"name": "OpenAI Blog", "url": "https://openai.com/blog/rss.xml"},
]

GITHUB_TRENDING_URL = "https://github.com/trending?since=daily&spoken_language_code=en"

CATEGORIES = [
    "AI Models",
    "Automation",
    "Video/Image AI",
    "Dev Tools",
    "Marketing",
    "Sales",
    "Analytics",
    "No-code",
    "Voice/Audio",
    "Productivity",
]

INVITEMEMBER_MONTHLY_URL = "https://invitemember.com/briifbot/monthly"
INVITEMEMBER_YEARLY_URL = "https://invitemember.com/briifbot/yearly"
BOT_USERNAME = "getbriifbot"
