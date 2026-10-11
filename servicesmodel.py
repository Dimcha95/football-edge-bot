from config import MIN_EDGE, MIN_ODDS, MAX_ODDS

def estimate_probabilities(home_strength: float, away_strength: float) -> dict:
    home_advantage = 1.15
    h = home_strength * home_advantage
    a = away_strength

    raw_home = h / (h + a)
    raw_away = a / (h + a)

    strength_diff = abs(home_strength - away_strength)
    draw_base = 0.28 - (strength_diff * 0.04)
    draw_base = max(0.18, min(0.32, draw_base))

    remaining = 1 - draw_base
    home = raw_home * remaining
    away = raw_away * remaining

    return {
        "home": round(home, 4),
        "draw": round(draw_base, 4),
        "away": round(away, 4)
    }

def check_value(odds: float, our_prob: float):
    if not (MIN_ODDS <= odds <= MAX_ODDS):
        return False, 0.0
    implied = 1 / odds
    edge = our_prob - implied
    return edge >= MIN_EDGE, round(edge, 4)