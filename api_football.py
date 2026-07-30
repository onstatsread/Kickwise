"""
Kickwise — API-Football Fallback Odds Module
----------------------------------------------
Second odds source, used ONLY when The Odds API (odds.py) has no data
for a match. API-Football has broader league coverage but a much
tighter free quota (100 requests/day), so this module is built to
spend that quota carefully:

  - ONE call per day fetches ALL global fixtures for today (cached).
  - Odds are only fetched (1 call each) for matches where the primary
    provider (The Odds API) came up empty — never called blindly.

HOW TO USE:
1. Save this as `api_football.py` next to app.py and odds.py.
2. Sign up free at https://www.api-football.com (or via RapidAPI) and
   get your key.
3. Add API_FOOTBALL_KEY as an environment variable on Render, same way
   you did ODDS_API_KEY.
4. In app.py, import get_fallback_odds and call it when get_odds_for_card
   (from odds.py) returns None — see the /predict integration note below.

IMPORTANT — free tier is 100 requests/day TOTAL:
  - 1 request/day for the fixtures cache (cheap, always worth it)
  - 1 request per fallback odds lookup (only when primary provider misses)
  That means roughly 99 fallback lookups/day available. If your daily
  automation run has more misses than that, later matches in the run
  will just get no fallback odds either — this fails safely (returns
  None), it does not crash the run.
"""

import os
import time
import httpx
from difflib import SequenceMatcher
from datetime import date

API_FOOTBALL_KEY = os.environ.get("API_FOOTBALL_KEY", "")
API_FOOTBALL_BASE = "https://v3.football.api-sports.io"
HEADERS = {"x-apisports-key": API_FOOTBALL_KEY}

# Cache: one entry per date, holding that day's full global fixture list
_fixtures_cache: dict[str, tuple[float, list]] = {}
FIXTURES_CACHE_TTL = 12 * 60 * 60  # 12 hours — fixtures for a day don't change once posted

# Cache: per-fixture-id odds lookups, so re-checking the same match
# within a run doesn't spend quota twice
_odds_cache: dict[int, dict] = {}


def _similar(a: str, b: str) -> float:
    """Fuzzy string match score between 0 and 1, with a boost for
    abbreviated names — e.g. "KuPS Ak." vs "KuPS Akatemia" — where plain
    string similarity scores low but every word in the shorter name is
    clearly a prefix of the corresponding word in the longer name."""
    base_score = SequenceMatcher(None, a.lower().strip(), b.lower().strip()).ratio()

    a_words = a.lower().replace(".", "").split()
    b_words = b.lower().replace(".", "").split()
    shorter, longer = (a_words, b_words) if len(a_words) <= len(b_words) else (b_words, a_words)

    if shorter and len(shorter) == len(longer):
        if all(lw.startswith(sw) for sw, lw in zip(shorter, longer) if len(sw) >= 2):
            base_score = max(base_score, 0.95)

    return base_score


async def _get_todays_fixtures(date_str: str = None) -> list:
    """
    Fetch ALL global fixtures for a given date (YYYY-MM-DD) in ONE call.
    Cached for 12 hours so repeated matches in the same automation run
    don't cost extra quota.
    """
    if not date_str:
        date_str = date.today().isoformat()

    cached = _fixtures_cache.get(date_str)
    if cached and time.time() - cached[0] < FIXTURES_CACHE_TTL:
        return cached[1]

    if not API_FOOTBALL_KEY:
        return []

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.get(
                f"{API_FOOTBALL_BASE}/fixtures",
                headers=HEADERS,
                params={"date": date_str},
            )
        if resp.status_code != 200:
            return cached[1] if cached else []

        data = resp.json().get("response", [])
        _fixtures_cache[date_str] = (time.time(), data)
        return data
    except Exception:
        return cached[1] if cached else []


async def _find_fixture_id(home_team: str, away_team: str, date_str: str = None) -> int | None:
    fixtures = await _get_todays_fixtures(date_str)
    best_id, best_score = None, 0.0

    for fx in fixtures:
        teams = fx.get("teams", {})
        fx_home = teams.get("home", {}).get("name", "")
        fx_away = teams.get("away", {}).get("name", "")
        score = _similar(fx_home, home_team) + _similar(fx_away, away_team)
        if score > best_score:
            best_score = score
            best_id = fx.get("fixture", {}).get("id")

    return best_id if best_score >= 1.2 else None  # require a fairly strong match on both names


async def get_fallback_odds(home_team: str, away_team: str, date_str: str = None) -> dict | None:
    """
    Returns odds in the SAME shape as odds.py's get_odds_for_card():
    { home_odds, draw_odds, away_odds, home_pct, draw_pct, away_pct }
    Returns None if no fixture/odds found, quota exhausted, or key missing
    — always fails safe, never raises, so it's safe to call unconditionally
    as a fallback.
    """
    if not API_FOOTBALL_KEY:
        return None

    fixture_id = await _find_fixture_id(home_team, away_team, date_str)
    if not fixture_id:
        return None

    if fixture_id in _odds_cache:
        return _odds_cache[fixture_id]

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                f"{API_FOOTBALL_BASE}/odds",
                headers=HEADERS,
                params={"fixture": fixture_id, "bet": 1},  # bet=1 is "Match Winner" (1X2)
            )
        if resp.status_code != 200:
            return None

        response_data = resp.json().get("response", [])
        if not response_data:
            return None

        home_odds, draw_odds, away_odds = [], [], []
        for entry in response_data:
            for bookmaker in entry.get("bookmakers", []):
                for bet in bookmaker.get("bets", []):
                    if bet.get("id") != 1:
                        continue
                    for val in bet.get("values", []):
                        name = (val.get("value") or "").lower()
                        try:
                            odd = float(val.get("odd"))
                        except (TypeError, ValueError):
                            continue
                        if name == "home":
                            home_odds.append(odd)
                        elif name == "draw":
                            draw_odds.append(odd)
                        elif name == "away":
                            away_odds.append(odd)

        def avg(lst):
            return round(sum(lst) / len(lst), 2) if lst else None

        h_odd, d_odd, a_odd = avg(home_odds), avg(draw_odds), avg(away_odds)
        if not h_odd:
            return None

        def pct(o):
            return round(100 / o, 1) if o else None

        result = {
            "home_odds": h_odd, "draw_odds": d_odd, "away_odds": a_odd,
            "home_pct": pct(h_odd), "draw_pct": pct(d_odd), "away_pct": pct(a_odd),
        }
        _odds_cache[fixture_id] = result
        return result

    except Exception:
        return None
