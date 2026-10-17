"""Conservative, uncalibrated Poisson model for football Over totals."""
import math

ALLOWED_LINES = (1.5, 2.5, 3.5)
PRIOR_TOTAL_GOALS = 2.6
SHRINKAGE = 0.50  # heuristic; needs backtesting
MAX_TOTAL_GOALS = 4.0


def calculate_over_probability(home_expected_goals, away_expected_goals, line):
    """P(total 90-minute goals > line), shrunk toward a neutral prior."""
    home, away, line = float(home_expected_goals), float(away_expected_goals), float(line)
    if line not in ALLOWED_LINES:
        raise ValueError('Only Over 1.5, 2.5 and 3.5 supported')
    if not all(math.isfinite(v) for v in (home, away)) or home <= 0 or away <= 0:
        raise ValueError('Expected goals must be finite and positive')
    lam = min(MAX_TOTAL_GOALS, SHRINKAGE * (home + away) +
              (1 - SHRINKAGE) * PRIOR_TOTAL_GOALS)
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
