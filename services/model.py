from config import MIN_EDGE, MIN_ODDS, MAX_ODDS

def estimate_probabilities(
    home_strength: float,
    away_strength: float,
    home_form: float = 1.0,
    away_form: float = 1.0,
    home_avg_goals: float = 1.4,
    away_avg_goals: float = 1.1,
    home_avg_conceded: float = 1.1,
    away_avg_conceded: float = 1.4
) -> dict:
    home_advantage = 1.16

    home_attack = home_strength * home_form * home_avg_goals
    away_attack = away_strength * away_form * away_avg_goals
    home_defense = 1 / max(home_avg_conceded, 0.6)
    away_defense = 1 / max(away_avg_conceded, 0.6)

    home_xg = home_attack * away_defense * home_advantage
    away_xg = away_attack * home_defense
    total_xg = home_xg + away_xg

    if total_xg < 0.8:
        home_prob = 0.38
        draw_prob = 0.32
        away_prob = 0.30
    else:
        home_ratio = home_xg / total_xg
        away_ratio = away_xg / total_xg
        draw_prob = 0.26 - abs(home_ratio - away_ratio) * 0.12
        draw_prob = max(0.18, min(0.34, draw_prob))
        remaining = 1 - draw_prob
        home_prob = home_ratio * remaining
        away_prob = away_ratio * remaining

    return {
        "home": round(home_prob, 4),
        "draw": round(draw_prob, 4),
        "away": round(away_prob, 4),
        "home_xg": round(home_xg, 2),
        "away_xg": round(away_xg, 2)
    }


def estimate_totals(
    home_strength: float,
    away_strength: float,
    home_form: float = 1.0,
    away_form: float = 1.0,
    home_avg_goals: float = 1.4,
    away_avg_goals: float = 1.1,
    home_avg_conceded: float = 1.1,
    away_avg_conceded: float = 1.4
) -> dict:
    home_advantage = 1.16

    home_attack = home_strength * home_form * home_avg_goals
    away_attack = away_strength * away_form * away_avg_goals
    home_defense = 1 / max(home_avg_conceded, 0.6)
    away_defense = 1 / max(away_avg_conceded, 0.6)

    home_xg = home_attack * away_defense * home_advantage
    away_xg = away_attack * home_defense
    total_xg = home_xg + away_xg

    over_15 = 1 - (0.45 ** total_xg)
    over_25 = 1 - (0.28 ** total_xg)
    over_35 = 1 - (0.15 ** total_xg)

    over_15 = max(0.62, min(0.88, over_15 * 0.95))
    over_25 = max(0.40, min(0.72, over_25 * 0.92))
    over_35 = max(0.20, min(0.50, over_35 * 0.90))

    return {
        "over_15": round(over_15, 4),
        "under_15": round(1 - over_15, 4),
        "over_25": round(over_25, 4),
        "under_25": round(1 - over_25, 4),
        "over_35": round(over_35, 4),
        "under_35": round(1 - over_35, 4),
        "total_xg": round(total_xg, 2)
    }


def check_value(odds: float, our_prob: float):
    if not (MIN_ODDS <= odds <= MAX_ODDS):
        return False, 0.0
    implied = 1 / odds
    edge = our_prob - implied
    return edge >= MIN_EDGE, round(edge, 4)
