"""
Kickwise — Odds Integration Module
-----------------------------------
Pulls live H/D/A (1X2) odds from The Odds API and merges them with
your existing Poisson-based predictions.

HOW TO USE:
1. Paste this whole file into your repo as a new file, e.g. `odds.py`,
   sitting next to your `app.py`.
2. In `app.py`, add:
       from odds import router as odds_router
       app.include_router(odds_router)
3. Set your API key as an environment variable on Render:
       ODDS_API_KEY = your_key_here
   (Render dashboard -> your service -> Environment -> Add Environment Variable)
4. Push both files with full-file replacement as usual, redeploy.

ENDPOINTS THIS ADDS:
   GET /odds/{sport_key}                -> raw list of matches with avg H/D/A odds
   GET /odds/{sport_key}/{home}/{away}   -> odds for one specific match (fuzzy match on team names)

SPORT KEYS (most relevant to you):
   soccer_epl                 England - Premier League
   soccer_england_league1     England - League One
   soccer_spain_la_liga       Spain - La Liga
   soccer_italy_serie_a       Italy - Serie A
   soccer_germany_bundesliga  Germany - Bundesliga
   soccer_france_ligue_one    France - Ligue 1
Full list: GET https://api.the-odds-api.com/v4/sports/?apiKey=YOUR_KEY
"""

import os
import time
import httpx
from fastapi import APIRouter, HTTPException
from difflib import SequenceMatcher

router = APIRouter(prefix="/odds", tags=["odds"])

ODDS_API_KEY = os.environ.get("ODDS_API_KEY", "")
ODDS_API_BASE = "https://api.the-odds-api.com/v4"

# ── Simple in-memory cache: one API call per league per CACHE_TTL_SECONDS ──
# Since a single call returns ALL matches for that league, this means your
# daily automation run costs one API call per league with fixtures that day
# — not one call per match.
_odds_cache: dict[str, tuple[float, list]] = {}
CACHE_TTL_SECONDS = 4 * 60 * 60  # 4 hours; odds don't move fast enough to need less


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


def _extract_hda(match_data: dict) -> dict:
    """Average H/D/A odds across all bookmakers for one match."""
    home_team = match_data.get("home_team", "")
    away_team = match_data.get("away_team", "")
    home_odds, draw_odds, away_odds = [], [], []

    for bookmaker in match_data.get("bookmakers", []):
        for market in bookmaker.get("markets", []):
            if market.get("key") != "h2h":
                continue
            for outcome in market.get("outcomes", []):
                name = outcome.get("name", "")
                price = outcome.get("price")
                if price is None:
                    continue
                if name == home_team:
                    home_odds.append(price)
                elif name == away_team:
                    away_odds.append(price)
                else:
                    draw_odds.append(price)

    def avg(lst):
        return round(sum(lst) / len(lst), 2) if lst else None

    return {
        "home_team": home_team,
        "away_team": away_team,
        "commence_time": match_data.get("commence_time"),
        "home_odds": avg(home_odds),
        "draw_odds": avg(draw_odds),
        "away_odds": avg(away_odds),
        "num_bookmakers": len(match_data.get("bookmakers", [])),
    }


async def _fetch_odds(sport_key: str, force_refresh: bool = False) -> list:
    # Serve from cache if fresh
    cached = _odds_cache.get(sport_key)
    if cached and not force_refresh:
        fetched_at, data = cached
        if time.time() - fetched_at < CACHE_TTL_SECONDS:
            return data

    if not ODDS_API_KEY:
        raise HTTPException(
            status_code=500,
            detail="ODDS_API_KEY is not set. Add it as an environment variable on Render.",
        )

    url = f"{ODDS_API_BASE}/sports/{sport_key}/odds/"
    params = {
        "apiKey": ODDS_API_KEY,
        "regions": "uk,eu",
        "markets": "h2h",
        "oddsFormat": "decimal",
    }

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(url, params=params)

    if resp.status_code != 200:
        # If the API call fails but we have a stale cache, fall back to it
        # rather than breaking the whole automation run over one bad request.
        if cached:
            return cached[1]
        raise HTTPException(
            status_code=resp.status_code,
            detail=f"Odds API error: {resp.text}",
        )

    data = resp.json()
    _odds_cache[sport_key] = (time.time(), data)
    return data


async def prefetch_odds_for_leagues(sport_keys: list[str]) -> None:
    """
    Call this ONCE at the start of your daily automation run, passing only
    the sport_keys for leagues that actually have fixtures today. This
    warms the cache with one API call per league, so every subsequent
    get_odds_for_card() call for that league's matches is free (served
    from memory) for the rest of the run.

    Example (inside your automation script, before the prediction loop):
        todays_sport_keys = {league_to_sport_key[lg] for lg in leagues_with_fixtures}
        await prefetch_odds_for_leagues(list(todays_sport_keys))
    """
    for sport_key in sport_keys:
        try:
            await _fetch_odds(sport_key)
        except HTTPException:
            # Skip leagues the odds provider doesn't cover or errors on —
            # don't let one bad league stop the rest of the automation run.
            continue


@router.get("/{sport_key}")
async def get_league_odds(sport_key: str):
    """Return H/D/A odds for every upcoming match in a league."""
    raw_matches = await _fetch_odds(sport_key)
    return [_extract_hda(m) for m in raw_matches]


@router.get("/{sport_key}/{home_team}/{away_team}")
async def get_match_odds(sport_key: str, home_team: str, away_team: str):
    """
    Return H/D/A odds for one specific match. Uses fuzzy matching on
    team names since spellings often differ slightly between SoccerStats
    and the odds provider (e.g. 'Man Utd' vs 'Manchester United').
    """
    raw_matches = await _fetch_odds(sport_key)

    best_match = None
    best_score = 0.0

    for m in raw_matches:
        score = _similar(m.get("home_team", ""), home_team) + _similar(
            m.get("away_team", ""), away_team
        )
        if score > best_score:
            best_score = score
            best_match = m

    if not best_match or best_score < 1.0:  # each side should match reasonably well
        raise HTTPException(
            status_code=404,
            detail=f"No close match found for {home_team} vs {away_team} in {sport_key}",
        )

    return _extract_hda(best_match)


async def get_odds_for_card(sport_key: str, home_team: str, away_team: str) -> dict | None:
    """
    Returns odds in the exact shape the Kickwise frontend expects:
    { home_odds, draw_odds, away_odds, home_pct, draw_pct, away_pct }
    Returns None if no odds found — the frontend already renders nothing
    in that case (see the `if (oddsData && oddsData.home_odds)` check in
    index.html), so leagues without coverage just show no odds, no crash.

    Because _fetch_odds() is cached per league (see prefetch_odds_for_leagues),
    calling this once per match costs nothing extra as long as
    prefetch_odds_for_leagues() was called first for that match's league.
    """
    try:
        raw_matches = await _fetch_odds(sport_key)
    except HTTPException:
        return None  # don't break predictions if odds API is down/quota hit

    best_match, best_score = None, 0.0
    for m in raw_matches:
        score = _similar(m.get("home_team", ""), home_team) + _similar(m.get("away_team", ""), away_team)
        if score > best_score:
            best_score, best_match = score, m

    if not best_match or best_score < 1.0:
        return None

    odds = _extract_hda(best_match)
    if not odds["home_odds"]:
        return None

    def pct(o):
        return round(100 / o, 1) if o else None

    return {
        "home_odds": odds["home_odds"],
        "draw_odds": odds["draw_odds"],
        "away_odds": odds["away_odds"],
        "home_pct": pct(odds["home_odds"]),
        "draw_pct": pct(odds["draw_odds"]),
        "away_pct": pct(odds["away_odds"]),
    }


def merge_with_prediction(prediction: dict, odds: dict) -> dict:
    """
    Helper to merge your existing Poisson-based prediction dict with
    live odds, for use inside your /predict endpoint.

    Example:
        pred = run_poisson_prediction(home, away)
        odds = await get_match_odds(sport_key, home, away)
        combined = merge_with_prediction(pred, odds)
    """
    combined = dict(prediction)
    combined["market_odds"] = {
        "home": odds.get("home_odds"),
        "draw": odds.get("draw_odds"),
        "away": odds.get("away_odds"),
        "num_bookmakers": odds.get("num_bookmakers"),
    }

    # Flag value bets: where your model's implied probability beats the market's
    for side, odds_key, prob_key in [
        ("home", "home_odds", "home_win_prob"),
        ("draw", "draw_odds", "draw_prob"),
        ("away", "away_odds", "away_win_prob"),
    ]:
        market_price = odds.get(odds_key)
        model_prob = prediction.get(prob_key)
        if market_price and model_prob:
            implied_market_prob = 1 / market_price
            edge = model_prob - implied_market_prob
            combined.setdefault("value_edges", {})[side] = round(edge * 100, 1)  # % edge

    return combined
