"""Conservative, uncalibrated Poisson model for football Over totals."""
import math

ALLOWED_LINES = (1.5, 2.5, 3.5)
PRIOR_TOTAL_GOALS = 2.6  # запасная база, если среднее по лиге неизвестно
SHRINKAGE = 0.50  # heuristic; needs backtesting
MAX_TOTAL_GOALS = 4.0
MIN_PRIOR_GOALS = 1.5
MAX_PRIOR_GOALS = 4.0


def _resolve_prior(prior_total):
    """Среднее число голов в лиге, если оно корректно, иначе общая база."""
    try:
        value = float(prior_total)
    except (TypeError, ValueError):
        return PRIOR_TOTAL_GOALS
    if not math.isfinite(value) or value <= 0:
        return PRIOR_TOTAL_GOALS
    return max(MIN_PRIOR_GOALS, min(MAX_PRIOR_GOALS, value))


def calculate_over_probability(home_expected_goals, away_expected_goals, line,
                               prior_total=None):
    """P(total 90-minute goals > line), shrunk toward a prior.

    prior_total: среднее число голов за матч в лиге (если известно).
    Без него используется фиксированная база PRIOR_TOTAL_GOALS.
    """
    home, away, line = float(home_expected_goals), float(away_expected_goals), float(line)
    if line not in ALLOWED_LINES:
        raise ValueError('Only Over 1.5, 2.5 and 3.5 supported')
    if not all(math.isfinite(v) for v in (home, away)) or home <= 0 or away <= 0:
        raise ValueError('Expected goals must be finite and positive')
    prior = _resolve_prior(prior_total)
    lam = min(MAX_TOTAL_GOALS, SHRINKAGE * (home + away) +
              (1 - SHRINKAGE) * prior)
    k = int(line)
    p_le = math.exp(-lam) * sum(lam ** i / math.factorial(i) for i in range(k + 1))
    return max(0.0, min(1.0, 1 - p_le))


def calculate_edge(probability, odds):
    probability, odds = float(probability), float(odds)
    if not math.isfinite(probability) or not 0 <= probability <= 1:
        raise ValueError('Invalid probability')
    if not math.isfinite(odds) or odds <= 1:
        raise ValueError('Invalid odds')
    return probability * odds - 1


def estimate_probability(line, home_strength=1.3, away_strength=1.3):
    return calculate_over_probability(home_strength, away_strength, line)
