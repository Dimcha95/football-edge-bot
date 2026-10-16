
import math


def poisson_probability(expected_goals: float, goals: int) -> float:
    """
    Вероятность ровно goals голов
    при распределении Пуассона.
    """
    if expected_goals <= 0 or goals < 0:
        return 0.0

    return (
        math.exp(-expected_goals)
        * expected_goals ** goals
        / math.factorial(goals)
    )


def poisson_over_probability(
    expected_goals: float,
    line: float,
) -> float:
    """
    Вероятность тотала больше заданной линии.

    Примеры:
    1.5 -> минимум 2 гола
    2.5 -> минимум 3 гола
    3.5 -> минимум 4 гола
    """
    if expected_goals <= 0:
        return 0.0

    max_goals_under = math.floor(line)

    probability_under = sum(
        poisson_probability(expected_goals, goals)
        for goals in range(max_goals_under + 1)
    )

    probability_over = 1.0 - probability_under

    return max(0.0, min(1.0, probability_over))


def calculate_over_probability(
    home_expected_goals: float,
    away_expected_goals: float,
    line: float,
) -> float:
    """
    Рассчитывает вероятность тотала больше
    на основе ожидаемых голов двух команд.
    """
    expected_total = (
        home_expected_goals + away_expected_goals
    )

    return poisson_over_probability(
        expected_total,
        line,
    )


def estimate_probability(
    line: float,
    home_strength: float = 1.5,
    away_strength: float = 1.4,
) -> float:
    """
    Совместимость с вызовами оценки вероятности.

    ВАЖНО:
    home_strength и away_strength должны
    представлять ожидаемые голы команд,
    а не произвольные рейтинги силы.
    """
    return calculate_over_probability(
        home_strength,
        away_strength,
        line,
    )


def calculate_edge(
    our_prob: float,
    odds: float,
) -> float:
    """
    Edge = вероятность * коэффициент - 1.

    Положительное значение означает
    математическое преимущество только
    при корректной оценке вероятности.
    """
    if odds <= 1:
        return -1.0

    return our_prob * odds - 1.0


def is_value_bet(
    our_prob: float,
    odds: float,
    min_edge: float = 0.03,
    min_probability: float = 0.52,
) -> bool:
    """
    Проверка соответствия ставки фильтрам.
    """
    if our_prob < min_probability:
        return False

    return calculate_edge(
        our_prob,
        odds,
    ) >= min_edge
