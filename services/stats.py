import aiohttp
from config import API_FOOTBALL_KEY

async def get_team_stats(team_name: str) -> dict:
    """Получает статистику команды из API-Football"""
    headers = {"x-apisports-key": API_FOOTBALL_KEY}
    
    # Поиск команды
    url = "https://v3.football.api-sports.io/teams"
    params = {"search": team_name}
    
    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers, params=params) as resp:
            if resp.status != 200:
                return {"avg_goals": 1.3, "avg_conceded": 1.2, "form": 0.5}
            data = await resp.json()
            
            if not data.get("response"):
                return {"avg_goals": 1.3, "avg_conceded": 1.2, "form": 0.5}
            
            team_id = data["response"][0]["team"]["id"]
            
            # Получаем статистику сезона
            stats_url = "https://v3.football.api-sports.io/teams/statistics"
            stats_params = {"team": team_id, "season": 2025, "league": 39}  # Пример: АПЛ
            
            async with session.get(stats_url, headers=headers, params=stats_params) as stats_resp:
                if stats_resp.status != 200:
                    return {"avg_goals": 1.3, "avg_conceded": 1.2, "form": 0.5}
                stats = await stats_resp.json()
                
                if not stats.get("response"):
                    return {"avg_goals": 1.3, "avg_conceded": 1.2, "form": 0.5}
                
                s = stats["response"]
                played = s.get("fixtures", {}).get("played", {}).get("total", 1) or 1
                goals_for = s.get("goals", {}).get("for", {}).get("total", {}).get("total", 0) or 0
                goals_against = s.get("goals", {}).get("against", {}).get("total", {}).get("total", 0) or 0
                
                # Форма из последних матчей
                form_str = s.get("form", "") or ""
                wins = form_str.count("W")
                form = wins / max(len(form_str), 1)
                
                return {
                    "avg_goals": goals_for / played,
                    "avg_conceded": goals_against / played,
                    "form": form
                }
