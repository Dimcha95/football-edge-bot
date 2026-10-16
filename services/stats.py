import aiohttp
from config import API_FOOTBALL_KEY

async def get_team_stats(team_name: str) -> dict:
    """Получает статистику команды включая xG"""
    default = {"avg_goals": 1.3, "avg_conceded": 1.2, "form": 0.5, "xg": 1.3, "xga": 1.2}
    
    if not API_FOOTBALL_KEY:
        return default
    
    headers = {"x-apisports-key": API_FOOTBALL_KEY}
    timeout = aiohttp.ClientTimeout(total=5)
    
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            url = "https://v3.football.api-sports.io/teams"
            params = {"search": team_name}
            async with session.get(url, headers=headers, params=params) as resp:
                if resp.status != 200:
                    return default
                data = await resp.json()
                if not data.get("response"):
                    return default
                team_id = data["response"][0]["team"]["id"]
            
            stats_url = "https://v3.football.api-sports.io/teams/statistics"
            stats_params = {"team": team_id, "season": 2025, "league": 39}
            async with session.get(stats_url, headers=headers, params=stats_params) as stats_resp:
                if stats_resp.status != 200:
                    return default
                stats = await stats_resp.json()
                if not stats.get("response"):
                    return default
                
                s = stats["response"]
                played = s.get("fixtures", {}).get("played", {}).get("total", 1) or 1
                goals_for = s.get("goals", {}).get("for", {}).get("total", {}).get("total", 0) or 0
                goals_against = s.get("goals", {}).get("against", {}).get("total", {}).get("total", 0) or 0
                
                xg_for = s.get("goals", {}).get("for", {}).get("expected", {}).get("total", 0) or goals_for
                xg_against = s.get("goals", {}).get("against", {}).get("expected", {}).get("total", 0) or goals_against
                
                form_str = s.get("form", "") or ""
                wins = form_str.count("W")
                form = wins / max(len(form_str), 1) if form_str else 0.5
                
                return {
                    "avg_goals": round(goals_for / played, 2),
                    "avg_conceded": round(goals_against / played, 2),
                    "form": round(form, 2),
                    "xg": round(xg_for / played, 2),
                    "xga": round(xg_against / played, 2)
                }
    except:
        return default
