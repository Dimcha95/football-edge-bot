def calculate_over_probability(avg_goals_home: float, avg_goals_away: float, 
                                avg_conceded_home: float, avg_conceded_away: float,
                                line: float) -> float:
    expected_home = (avg_goals_home + avg_conceded_away) / 2
    expected_away = (avg_goals_away + avg_conceded_home) / 2
    expected_total = expected_home + expected_away
    
    if line == 0.5:
        base_prob = min(0.95, 0.5 + (expected_total - 0.5) * 0.15)
    elif line == 1.5:
        base_prob = min(0.92, 0.45 + (expected_total - 1.5) * 0.18)
    elif line == 2.5:
        base_prob = min(0.88, 0.38 + (expected_total - 2.5) * 0.16)
    elif line == 3.5:
        base_prob = min(0.80, 0.28 + (expected_total - 3.5) * 0.14)
    else:
        base_prob = min(0.75, 0.25 + (expected_total - line) * 0.12)
    
    return max(0.15, base_prob)


def calculate_edge(our_prob: float, odds: float) -> float:
    if odds <= 1.0:
        return 0
    return (our_prob * odds) - 1


def is_value_bet(our_prob: float, odds: float, min_edge: float = 0.05) -> bool:
    edge = calculate_edge(our_prob, odds)
    return edge >= min_edge and our_prob >= 0.45
