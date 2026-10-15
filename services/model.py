```python
import math


def poisson_over_probability(expected_goals: float, line: float) -> float:
    """
    Оценивает вероятность тотала больше линии
    через распределение Пуассона.

    Это базовая модель, а не гарантия точности прогноза.
    """
    if expected_goals <= 0 or line < 0:
        return 0.0

    # Для целых линий учитываем возврат отдельно:
    # вероятность выигрыша — строго больше линии.
    threshold = math.floor(line)

    probability_below_or_equal = 0.0
    term = math.exp(-expected_goals)

    for goals in range(threshold + 1):
        if goals == 0:
            probability_below_or_equal += term
        else:
            term *= expected_goals / goals
            probability_below_or_equal += term

    return max(0.0, min(1.0, 1.0 - probability_below_or_equal))


def calculate_over_probability(
    avg_goals_home: float,
    avg_goals_away: float,
    avg_conceded_home: float,
    avg_conceded_away: float,
    form_home: float,
    form_away: float,
    line: float,
    xg_home: float = 1.3,
    xg_away: float = 1.3,
) -> float:
    """Оценивает вероятность тотала по ожидаемым голам и форме команд."""
    attack_home = xg_home if xg_home > 0 else avg_goals_home
    attack_away = xg_away if xg_away > 0 else avg_goals_away

    expected_home = max(0.05, (attack_home + avg_conceded_away) / 2)
    expected_away = max(0.05, (attack_away + avg_conceded_home) / 2)

    form_factor = 1 + (form_home + form_away - 1) * 0.15
    expected_total = max(0.05, (expected_home + expected_away) * form_factor)

    return poisson_over_probability(expected_total, line)


def calculate_edge(our_prob: float, odds: float) -> float:
    """Расчёт ожидаемого преимущества по коэффициенту."""
    if odds <= 1:
        return 0.0
    return our_prob * odds - 1


def is_value_bet(
    our_prob: float,
    odds: float,
    min_edge: float = 0.05,
    min_probability: float = 0.40,
) -> bool:
    """Проверяет минимальную вероятность и преимущество."""
    edge = calculate_edge(our_prob, odds)
    return edge >= min_edge and our_prob >= min_probability
```
