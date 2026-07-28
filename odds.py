"""
Kickwise — Odds Integration Module
-----------------------------------
Pulls live H/D/A (1X2) odds from The Odds API and merges them with
your existing Poisson-based predictions.
"""

import os
import time
import httpx
from fastapi import APIRouter, HTTPException
from difflib import SequenceMatcher

router = APIRouter(prefix="/odds", tags=["odds"])

ODDS_API_KEY = os.environ.get("ODDS_API_KEY", "")
ODDS_API_BASE = "https://api.the-odds-api.com/v4"

_odds_cache: dict[str, tuple[float, list]] = {}
CACHE_TTL_SECONDS = 4 * 60 * 60  # 4 hours


def _similar(a: str, b: str) -> float:
    return SequenceMatcher(None, a.lower().strip(), b.lower().strip()).ratio()


def _extract_hda(match_data: dict) -> dict:
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
    for sport_key in sport_keys:
        try:
            await _fetch_odds(sport_key)
        except HTTPException:
            continue


@router.get("/{sport_key}")
async def get_league_odds(sport_key: str):
    raw_matches = await _fetch_odds(sport_key)
    return [_extract_hda(m) for m in raw_matches]


@router.get("/{sport_key}/{home_team}/{away_team}")
async def get_match_odds(sport_key: str, home_team: str, away_team: str):
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

    if not best_match or best_score < 1.0:
        raise HTTPException(
            status_code=404,
            detail=f"No close match found for {home_team} vs {away_team} in {sport_key}",
        )

    return _extract_hda(best_match)


async def get_odds_for_card(sport_key: str, home_team: str, away_team: str) -> dict | None:
    try:
        raw_matches = await _fetch_odds(sport_key)
    except HTTPException:
        return None

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
    combined = dict(prediction)
    combined["market_odds"] = {
        "home": odds.get("home_odds"),
        "draw": odds.get("draw_odds"),
        "away": odds.get("away_odds"),
        "num_bookmakers": odds.get("num_bookmakers"),
    }

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
            combined.setdefault("value_edges", {})[side] = round(edge * 100, 1)

    return combined
