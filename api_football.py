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
    partial/abbreviated names — covers two cases:
    1. Same word count, abbreviated word(s) — "KuPS Ak." vs "KuPS Akatemia"
    2. Fewer words entirely — "Naftan" vs "Naftan Novopolotsk"
    In both cases plain string similarity scores low even though a human
    would clearly recognize them as the same team."""
    base_score = SequenceMatcher(None, a.lower().strip(), b.lower().strip()).ratio()

    a_words = a.lower().replace(".", "").split()
    b_words = b.lower().replace(".", "").split()
    shorter, longer = (a_words, b_words) if len(a_words) <= len(b_words) else (b_words, a_words)

    if shorter and longer:
        prefix_match = all(lw.startswith(sw) for sw, lw in zip(shorter, longer) if len(sw) >= 2)
        if prefix_match:
            coverage = len(shorter) / len(longer)
            base_score = max(base_score, 0.75 + 0.20 * coverage)

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
        print("  [api_football] No API_FOOTBALL_KEY set — skipping fallback")
        return []

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.get(
                f"{API_FOOTBALL_BASE}/fixtures",
                headers=HEADERS,
                params={"date": date_str},
            )
        if resp.status_code == 429:
            print(f"  [api_football] Rate limit / quota exceeded (429) fetching fixtures for {date_str}")
            return cached[1] if cached else []
        if resp.status_code != 200:
            print(f"  [api_football] Fixtures fetch failed: HTTP {resp.status_code} — {resp.text[:200]}")
            return cached[1] if cached else []

        body = resp.json()
        errors = body.get("errors")
        if errors:
            print(f"  [api_football] API returned errors on fixtures fetch: {errors}")
        data = body.get("response", [])
        print(f"  [api_football] Fetched {len(data)} global fixtures for {date_str} "
              f"(requests remaining today: {resp.headers.get('x-ratelimit-requests-remaining', '?')})")
        _fixtures_cache[date_str] = (time.time(), data)
        return data
    except Exception as e:
        print(f"  [api_football] Exception fetching fixtures: {e}")
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

    if best_id is None or best_score < 1.2:
        print(f"  [api_football] No confident fixture match for '{home_team}' vs '{away_team}' "
              f"(best score: {best_score:.2f})")
        return None
    return best_id


async def get_fallback_odds(home_team: str, away_team: str, date_str: str = None) -> dict | None:
    """
    Returns odds in the SAME shape as odds.py's get_odds_for_card():
    { home_odds, draw_odds, away_odds, home_pct, draw_pct, away_pct }
    Returns None if no fixture/odds found, quota exhausted, or key missing
    — always fails safe, never raises, so it's safe to call unconditionally
    as a fallback. Every failure path is logged so Render logs show
    WHY it returned None (quota, no fixture, no odds posted, or a bug)
    instead of leaving it a mystery.
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
        if resp.status_code == 429:
            print(f"  [api_football] Rate limit / quota exceeded (429) fetching odds for fixture {fixture_id}")
            return None
        if resp.status_code != 200:
            print(f"  [api_football] Odds fetch failed: HTTP {resp.status_code} — {resp.text[:200]}")
            return None

        body = resp.json()
        errors = body.get("errors")
        if errors:
            print(f"  [api_football] API returned errors on odds fetch: {errors}")

        remaining = resp.headers.get("x-ratelimit-requests-remaining", "?")
        response_data = body.get("response", [])
        if not response_data:
            print(f"  [api_football] No odds posted yet for fixture {fixture_id} "
                  f"(requests remaining today: {remaining})")
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
            print(f"  [api_football] Fixture {fixture_id} had bookmaker data but no parseable Match Winner odds")
            return None

        def pct(o):
            return round(100 / o, 1) if o else None

        result = {
            "home_odds": h_odd, "draw_odds": d_odd, "away_odds": a_odd,
            "home_pct": pct(h_odd), "draw_pct": pct(d_odd), "away_pct": pct(a_odd),
        }
        _odds_cache[fixture_id] = result
        print(f"  [api_football] Fallback odds found for fixture {fixture_id} "
              f"(requests remaining today: {remaining})")
        return result

    except Exception as e:
        print(f"  [api_football] Exception fetching odds for fixture {fixture_id}: {e}")
        return None
