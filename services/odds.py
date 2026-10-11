import aiohttp
from config import ODDS_API_KEY, REGIONS

async def get_soccer_odds():
    url = "https://api.the-odds-api.com/v4/sports/soccer/odds"
    params = {
        "apiKey": ODDS_API_KEY,
        "regions": REGIONS,
        "markets": "h2h,totals",
        "oddsFormat": "decimal",
        "dateFormat": "iso"
    }
    async with aiohttp.ClientSession() as session:
        async with session.get(url, params=params) as resp:
            if resp.status != 200:
                print(f"Odds API error: {resp.status}")
                return []
            return await resp.json()


def parse_matches(raw_data: list) -> list:
    matches = []
    for event in raw_data:
        try:
            home = event["home_team"]
            away = event["away_team"]
            league = event.get("sport_title", "Soccer")
            commence = event.get("commence_time", "")[:16].replace("T", " ")

            bookmakers = event.get("bookmakers", [])
            if not bookmakers:
                continue

            bm = bookmakers[0]
            markets = {m["key"]: m for m in bm.get("markets", [])}

            odds = {}
            totals = markets.get("totals")

            if totals:
                for o in totals["outcomes"]:
                    point = o.get("point")
                    name = o["name"]
                    price = o["price"]

                    if name == "Over":
                        if point == 1.5:
                            odds["over_15"] = price
                        elif point == 2.5:
                            odds["over_25"] = price
                        elif point == 3.5:
                            odds["over_35"] = price
                    elif name == "Under":
                        if point == 1.5:
                            odds["under_15"] = price
                        elif point == 2.5:
                            odds["under_25"] = price
                        elif point == 3.5:
                            odds["under_35"] = price

            if odds:
                matches.append({
                    "home": home,
                    "away": away,
                    "league": league,
                    "time": commence,
                    "odds": odds,
                    "home_strength": 1.5,
                    "away_strength": 1.4
                })
        except Exception as e:
            print(f"Parse error: {e}")
            continue

    return matches
