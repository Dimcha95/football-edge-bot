import logging
import os

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)


def _env_float(name, default, low=None, high=None):
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw.replace(",", "."))
    except ValueError:
        raise RuntimeError(f"{name} должно быть числом, получено: {raw!r}")
    if (low is not None and value < low) or (high is not None and value > high):
        raise RuntimeError(f"{name}={value} вне допустимого диапазона [{low}, {high}]")
    return value


def _env_int(name, default, low=None, high=None):
    return int(_env_float(name, default, low, high))


def _env_int_list(name):
    result = []
    for part in os.getenv(name, "").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            result.append(int(part))
        except ValueError:
            logger.warning("%s: пропущено некорректное значение %r", name, part)
    return result


BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_IDS = _env_int_list("ADMIN_IDS")

BSD_API_KEY = os.getenv("BSD_API_KEY", "")

# Устаревшие настройки (оставлены для совместимости с другими файлами).
ODDS_API_KEY = os.getenv("ODDS_API_KEY")
API_FOOTBALL_KEY = os.getenv("API_FOOTBALL_KEY", "")
REGIONS = os.getenv("REGIONS", "eu")
CHECK_INTERVAL = _env_int("CHECK_INTERVAL", 10, low=1)

# Минимальное расчётное преимущество (0.03 = 3%).
MIN_EDGE = _env_float("MIN_EDGE", 0.03, low=0.0, high=1.0)

# Минимальная вероятность исхода. При 0.52 ТБ 3.5 почти никогда не проходит;
# для всех трёх линий поставьте около 0.30.
MIN_PROBABILITY = _env_float("MIN_PROBABILITY", 0.52, low=0.0, high=1.0)

MAX_SIGNALS_PER_DAY = _env_int("MAX_SIGNALS_PER_DAY", 15, low=1)
