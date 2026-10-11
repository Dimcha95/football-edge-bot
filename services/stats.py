import aiohttp
import os
from datetime import datetime, timedelta

API_KEY = os.getenv("API_FOOTBALL_KEY")
BASE_URL = "https://v3.football.api-sports.io"

async def get_team_stats(team_name: str, league_id: int = None):
    """Получает статистику команды: xG, форма, средние голы"""
    if not API_KEY:
        return None

    headers = {"x-apisports-key": API_KEY}
    
    # Ищем команду
    async with aiohttp.ClientSession() as session:
        params = {"search": team_name}
        async with session.get(f"{BASE_URL}/teams", headers=headers, params=params) as resp:
            if resp.status != 200:
                return None
            data = await resp.json()
            teams = data.get("response", [])
            if not teams:
                return None
            team_id = teams[0]["team"]["id"]

        # Получаем статистику сезона
        params = {"team": team_id, "season": 2024}
        if league_id:
            params["league"] = league_id
            
        async with session.get(f"{BASE_URL}/teams/statistics", headers=headers, params=params) as resp:
            if resp.status != 200:
                return None
            data = await resp.json()
            stats = data.get("response", {})
            
            if not stats:
                return None

            # Извлекаем данные
            fixtures = stats.get("fixtures", {})
            goals = stats.get("goals", {})
            
            played = fixtures.get("played", {}).get("total", 0) or 1
            goals_for = goals.get("for", {}).get("total", {}).get("total", 0) or 0
            goals_against = goals.get("against", {}).get("total", {}).get("total", 0) or 0
            
            # Форма (последние матчи)
            form = stats.get("form", "")
            form_points = 0
            if form:
                form_points = form.count("W") * 3 + form.count("D")
                form_points = form_points / max(len(form), 1)

            return {
                "team_id": team_id,
                "avg_goals": round(goals_for / played, 2),
                "avg_conceded": round(goals_against / played, 2),
                "form": round(form_points, 2),
                "played": played
            }
