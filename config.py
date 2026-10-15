import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()]

ODDS_API_KEY = os.getenv("ODDS_API_KEY")
API_FOOTBALL_KEY = os.getenv("API_FOOTBALL_KEY", "")
REGIONS = os.getenv("REGIONS", "eu")
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", 10))

MIN_EDGE = float(os.getenv("MIN_EDGE", 0.03))
MIN_PROBABILITY = float(os.getenv("MIN_PROBABILITY", 0.52))
MAX_SIGNALS_PER_DAY = int(os.getenv("MAX_SIGNALS_PER_DAY", 15))
