import logging
import math
from datetime import datetime, timezone
from html import escape

from config import ADMIN_IDS, MIN_EDGE, MIN_PROBABILITY, MAX_SIGNALS_PER_DAY
from database import (
    get_active_users, get_user_status, save_signal,
    is_signal_sent, get_today_signals_count,
)
from services.odds import get_odds
from services.model import calculate_edge, calculate_over_probability
from services.team_stats import get_team_statistics, estimate_expected_goals

logger = logging.getLogger(__name__)

# Включать только после проверки данных и результатов на истории.
ENABLE_LIVE_SIGNALS = False


def is_future_match(commence_time):
    if not commence_time:
        return False
    try:
        kickoff = datetime.fromisoformat(str(commence_time).replace('Z', '+00:00'))
        if kickoff.tzinfo is None:
            kickoff = kickoff.replace(tzinfo=timezone.utc)
        return kickoff > datetime.now(timezone.utc)
    except (ValueError, TypeError, OverflowError):
        return False


async def get_eligible_users():
    active_users = await get_active_users()
    eligible_users = []
    for user_id in active_users:
        try:
            status, days_left = await get_user_status(user_id)
            if status == 'premium' or (
                status == 'trial' and days_left is not None and days_left > 0
            ):
                eligible_users.append(user_id)
        except Exception:
            logger.exception('Ошибка проверки доступа пользователя %s', user_id)
    return eligible_users


def attach_expected_goals(match, stats, league_totals):
    """Добавляет оценку голов только при наличии достаточной статистики."""
    try:
        home_id = int(match['home_id'])
        away_id = int(match['away_id'])
        league_id = int(match['league_id'])
    except (KeyError, ValueError, TypeError):
        return None

    estimate = estimate_expected_goals(
        home_id, away_id, league_id, stats, league_totals
    )
    if estimate is None:
        return None
    return {**match, **estimate}


def evaluate_match(match):
    """Вероятность тотала по Пуассону и фильтры математического преимущества."""
    try:
        line = float(match['line'])
        odds = float(match['odds'])
        home_xg = float(match['home_expected_goals'])
        away_xg = float(match['away_expected_goals'])
    except (KeyError, ValueError, TypeError):
        return None

    if not all(math.isfinite(v) for v in (line, odds, home_xg, away_xg)):
        return None
    if line not in (1.5, 2.5, 3.5) or odds <= 1 or home_xg <= 0 or away_xg <= 0:
        return None

    probability = calculate_over_probability(home_xg, away_xg, line)
    edge = calculate_edge(probability, odds)
    return {
        'line': line,
        'odds': odds,
        'probability': probability,
        'edge': edge,
        'passes_filters': probability >= MIN_PROBABILITY and edge >= MIN_EDGE,
    }


def format_signal(match, evaluation, test=False):
    home = escape(str(match.get('home', '')))
    away = escape(str(match.get('away', '')))
    league = escape(str(match.get('league', 'Футбол')))
    commence_time = escape(str(match.get('commence_time', '')))
    line = evaluation['line']
    odds = evaluation['odds']
    probability = evaluation['probability']
    edge = evaluation['edge']
    prefix = '🧪 <b>ТЕСТОВЫЙ СИГНАЛ</b>\n' if test else '⚽️ <b>FOOTBALL EDGE BOT</b>\n'
    return (
        f'{prefix}\n'
        f'⚽️ <b>{home} — {away}</b>\n'
        f'🏆 {league}\n'
        f'🕒 {commence_time}\n\n'
        f'🎯 ТБ {line:g} @ {odds:.2f}\n'
        f'📊 Оценка вероятности: {probability * 100:.1f}%\n'
        f'📈 Расчётный Edge: {edge * 100:+.1f}%\n\n'
        '⚠️ Оценка основана на статистике голов и модели Пуассона; '
        'точность прогнозов пока не подтверждена историческим тестированием.'
    )


async def load_evaluated_matches():
    """Один набор статистики на весь цикл, а не API-запрос на каждую ставку."""
    matches = await get_odds()
    if not matches:
        logger.info('Нет доступных коэффициентов')
        return []

    stats, league_totals = await get_team_statistics()
    if not stats or not league_totals:
        logger.warning('Исторической статистики нет; сигналы пропущены')
        return []

    prepared = []
    skipped = 0
    for match in matches:
        if not is_future_match(match.get('commence_time')):
            continue
        enriched = attach_expected_goals(match, stats, league_totals)
        if enriched is None:
            skipped += 1
            continue
        evaluation = evaluate_match(enriched)
        if evaluation is not None:
            prepared.append((enriched, evaluation))
    logger.info('Оценено матчей: %s, без достаточной статистики: %s', len(prepared), skipped)
    return prepared


async def test_signal(bot, admin_id=None):
    """Один тестовый сигнал только администратору; в БД не записывается."""
    if not ADMIN_IDS:
        logger.error('ADMIN_IDS не настроен')
        return False
    if admin_id is None:
        admin_id = ADMIN_IDS[0]
    if admin_id not in ADMIN_IDS:
        logger.error('Тест разрешён только администратору')
        return False

    for match, evaluation in await load_evaluated_matches():
        if not evaluation['passes_filters']:
            continue
        try:
            await bot.send_message(
                chat_id=admin_id,
                text=format_signal(match, evaluation, test=True),
                parse_mode='HTML',
            )
            logger.info('Тестовый сигнал отправлен %s', admin_id)
            return True
        except Exception:
            logger.exception('Ошибка тестовой отправки')
            return False
    logger.info('Подходящих тестовых сигналов нет')
    return False


async def check_and_send(bot):
    """Автоматическая рассылка отключена до проверки модели на истории."""
    if not ENABLE_LIVE_SIGNALS:
        logger.info('Автоматическая рассылка отключена: модель ещё не проверена')
        return

    today_count = await get_today_signals_count()
    if today_count >= MAX_SIGNALS_PER_DAY:
        logger.info('Достигнут дневной лимит сигналов')
        return

    users = await get_eligible_users()
    if not users:
        logger.info('Нет активных пользователей с доступом')
        return

    evaluated_matches = await load_evaluated_matches()
    new_signals = 0
    for match, evaluation in evaluated_matches:
        if today_count + new_signals >= MAX_SIGNALS_PER_DAY:
            break
        if not evaluation['passes_filters']:
            continue

        event_id = match.get('event_id')
        home = match.get('home')
        away = match.get('away')
        if not event_id or not home or not away:
            continue
        line = evaluation['line']
        match_name = f'{home} — {away}'
        market_name = f'ТБ {line:g}'
        if await is_signal_sent(match_name, market_name, event_id=str(event_id)):
            continue

        text = format_signal(match, evaluation)
        delivered = 0
        for user_id in users:
            try:
                await bot.send_message(chat_id=user_id, text=text, parse_mode='HTML')
                delivered += 1
            except Exception:
                logger.exception('Ошибка отправки пользователю %s', user_id)
        if not delivered:
            logger.warning('Сигнал никому не доставлен: %s', match_name)
            continue

        fixture_id = match.get('fixture_id')
        try:
            saved = await save_signal(
                match_name=match_name,
                league=match.get('league', 'Футбол'),
                market=market_name,
                odds=evaluation['odds'],
                our_prob=evaluation['probability'],
                edge=evaluation['edge'],
                event_id=str(event_id),
                fixture_id=int(fixture_id) if fixture_id is not None else None,
                commence_time=match.get('commence_time'),
            )
            if saved:
                new_signals += 1
        except Exception:
            logger.exception('Ошибка сохранения сигнала')
    logger.info('Новых сохранённых сигналов: %s', new_signals)
