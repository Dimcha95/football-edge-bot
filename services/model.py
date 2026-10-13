def calculate_over_probability(avg_goals_home, avg_goals_away, avg_conceded_home, avg_conceded_away, form_home, form_away, line):
    """Улучшенная модель с учётом атаки и обороны"""
    expected_home = (avg_goals_home + avg_conceded_away) / 2
    expected_away = (avg_goals_away + avg_conceded_home) / 2
    
    form_factor = 1 + (form_home + form_away - 1) * 0.2
    expected_total = (expected_home + expected_away) * form_factor
    
    if line <= 0.5:
        base_prob = 0.85
    elif line <= 1.5:
        base_prob = 0.70
    elif line <= 2.5:
        base_prob = 0.55
    elif line <= 3.5:
        base_prob = 0.40
    else:
        base_prob = 0.25
    
    if expected_total > line + 0.5:
        base_prob += 0.15
    elif expected_total < line - 0.5:
        base_prob -= 0.15
    
    return max(0.15, min(0.90, base_prob))

def calculate_edge(our_prob: float, odds: float) -> float:
    """Считает edge"""
    if odds <= 1:
        return 0
    return (our_prob * odds) - 1

def is_value_bet(our_prob: float, odds: float, min_edge: float = 0.03) -> bool:
    """Проверяет, является ли ставка value"""
    edge = calculate_edge(our_prob, odds)
    return edge >= min_edge and our_prob >= 0.40
