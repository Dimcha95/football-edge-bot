import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_IDS = [int(x.strip()) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()]
ODDS_API_KEY = os.getenv("ODDS_API_KEY")

MIN_EDGE = 0.04
MIN_ODDS = 1.50
MAX_ODDS = 3.50
CHECK_INTERVAL = 12
REGIONS = "eu,uk"