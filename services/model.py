
def calculate_over_probability(
    avg_goals_home,
    avg_goals_away,
    avg_conceded_home,
    avg_conceded_away,
    form_home,
    form_away,
    line,
    xg_home=1.3,
    xg_away=1.3,
):
    """Оценка вероятности тотала на основе входных показателей."""
    attack_home = xg_home if xg_home > 0 else avg_goals_home
    attack_away = xg_away if xg_away > 0 else avg_goals_away

    expected_home = (attack_home + avg_conceded_away) / 2
    expected_away = (attack_away + avg_conceded_home) / 2

    form_factor = 1 + (form_home + form_away - 1) * 0.15
    expected_total = (expected_home + expected_away) * form_factor

    if line <= 0.5:
        base_prob = 0.82
    elif line <= 1.5:
        base_prob = 0.68
    elif line <= 2.5:
        base_prob = 0.52
    elif line <= 3.5:
        base_prob = 0.38
    else:
        base_prob = 0.22

    if expected_total > line + 0.4:
        base_prob += 0.12
    elif expected_total < line - 0.4:
        base_prob -= 0.12

    return max(0.15, min(0.88, base_prob))


def calculate_edge(our_prob: float, odds: float) -> float:
    """Расчёт ожидаемого преимущества по коэффициенту."""
    if odds <= 1:
        return 0.0

    return (our_prob * odds) - 1


def is_value_bet(
    our_prob: float,
    odds: float,
    min_edge: float = 0.05,
) -> bool:
    """Проверка вероятности и минимального edge."""
    edge = calculate_edge(our_prob, odds)
    return edge >= min_edge and our_prob >= 0.40
