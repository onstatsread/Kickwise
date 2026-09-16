 """
odds_api_io.py — Odds-API.io integration for Kickwise

Provides:
    get_odds_api_io_fallback(home, away)   -> 1X2 (home/draw/away) odds dict
    get_ou25_api_io_fallback(home, away)   -> Over/Under 2.5 goals odds dict

Both return dicts shaped exactly like the other odds sources already used
in app.py (AnnaBet / The Odds API / API-Football), so they drop straight
into the existing fallback chain with no changes needed elsewhere:

    market_odds = {"home_odds": .., "draw_odds": .., "away_odds": ..,
                    "home_pct": .., "draw_pct": .., "away_pct": ..}
    market_ou25 = {"over_odds": .., "under_odds": ..,
                    "over_pct": .., "under_pct": ..}

On any failure (no match found, API error, rate limit, bad response shape)
both functions return None — same convention as the other fallbacks in
app.py, so predict()/predict_v2() just moves on to the next source.

SETUP
-----
1. pip install odds-api-io
2. Set the environment variable ODDS_API_IO_KEY to your free API key
   (https://odds-api.io/#pricing)

NOTES / ASSUMPTIONS TO VERIFY
------------------------------
The official SDK (odds-api-io on PyPI, github.com/odds-api-io/odds-api-python)
documents these methods but NOT the exact JSON shape returned by
get_event_odds(). The parser below is written defensively — it tries
several common key names for the moneyline market ("h2h", "1x2", "ML",
"moneyline") and totals market ("totals", "over_under", "Totals") and
several shapes for each (list-of-outcomes, dict-of-outcomes, nested
"odds" list). If your key returns something this doesn't recognize,
call debug_raw_event_odds(home, away) below — it dumps the raw,
unparsed response so you can see the real shape and adjust
_parse_moneyline() / _parse_totals_25() accordingly.
"""

import os
import re
import time
import difflib

from odds_api import AsyncOddsAPIClient, OddsAPIError

ODDS_API_IO_KEY = os.environ.get("ODDS_API_IO_KEY", "")

# Free tier = 100 req/hour, 2 bookmakers only — cache aggressively so a
# busy predict loop doesn't burn the whole hourly quota on repeat lookups
# of the same fixture.
_EVENT_CACHE = {}
_ODDS_CACHE = {}
CACHE_TTL = 600  # 10 minutes


def _norm(name):
    return " ".join(str(name or "").lower().split())


def _names_match(a, b):
    a, b = _norm(a), _norm(b)
    if not a or not b:
        return False
    if a == b:
        return True
    if len(a) >= 5 and (a in b or b in a):
        return True
    return difflib.SequenceMatcher(None, a, b).ratio() >= 0.82


async def _find_event(home, away):
    """
    Search Odds-API.io for an upcoming football event matching both
    team names. Returns the raw event dict, or None if nothing matches.
    """
    cache_key = (_norm(home), _norm(away))
    cached = _EVENT_CACHE.get(cache_key)
    if cached and time.time() - cached[0] < CACHE_TTL:
        return cached[1]

    if not ODDS_API_IO_KEY:
        print("Odds-API.io: ODDS_API_IO_KEY not set, skipping")
        return None

    try:
        async with AsyncOddsAPIClient(api_key=ODDS_API_IO_KEY) as client:
            # search_events searches across sports/leagues by keyword —
            # search on the home team name first, since that tends to
            # be the more distinctive of the two in the query string.
            results = await client.search_events(query=home)

            if not results:
                return None

            best = None
            for ev in results:
                ev_home = ev.get("home") or (ev.get("participants") or [{}])[0].get("name", "")
                ev_away = ev.get("away") or (ev.get("participants") or [{}, {}])[1].get("name", "")

                if _names_match(home, ev_home) and _names_match(away, ev_away):
                    best = ev
                    break

            if best:
                _EVENT_CACHE[cache_key] = (time.time(), best)

            return best

    except OddsAPIError as e:
        print(f"Odds-API.io search failed for {home} - {away}: {e}")
        return None
    except Exception as e:
        print(f"Odds-API.io search error for {home} - {away}: {e}")
        return None


async def _get_raw_odds(home, away):
    event = await _find_event(home, away)
    if not event:
        return None

    event_id = event.get("id")
    if event_id is None:
        return None

    cache_key = ("odds", event_id)
    cached = _ODDS_CACHE.get(cache_key)
    if cached and time.time() - cached[0] < CACHE_TTL:
        return cached[1]

    try:
        async with AsyncOddsAPIClient(api_key=ODDS_API_IO_KEY) as client:
            odds_data = await client.get_event_odds(event_id)
            _ODDS_CACHE[cache_key] = (time.time(), odds_data)
            return odds_data
    except OddsAPIError as e:
        print(f"Odds-API.io get_event_odds failed for event {event_id}: {e}")
        return None
    except Exception as e:
        print(f"Odds-API.io odds fetch error for event {event_id}: {e}")
        return None


def _to_float(v):
    try:
        f = float(v)
        return f if f > 1.0 else None
    except (TypeError, ValueError):
        return None


def _iter_bookmaker_markets(odds_data):
    """
    Normalizes the various shapes the SDK might hand back into a flat
    stream of (bookmaker_name, market_name, market_payload) tuples.
    Handles both:
        {"bookmakers": {"Bet365": [{"name": "ML", "odds": [...]}]}}
    and:
        {"bookmakers": [{"name": "Bet365", "markets": [...]}]}
    """
    bookmakers = odds_data.get("bookmakers") if isinstance(odds_data, dict) else None
    if not bookmakers:
        return

    if isinstance(bookmakers, dict):
        for bk_name, markets in bookmakers.items():
            for market in (markets or []):
                yield bk_name, (market.get("name") or market.get("market") or ""), market

    elif isinstance(bookmakers, list):
        for bk in bookmakers:
            bk_name = bk.get("name", "")
            for market in (bk.get("markets") or bk.get("odds") or []):
                yield bk_name, (market.get("name") or market.get("market") or ""), market


_MONEYLINE_NAMES = {"h2h", "1x2", "ml", "moneyline", "match winner", "match odds"}
_TOTALS_NAMES = {"totals", "over/under", "over under", "total goals", "goals over/under"}


def _parse_moneyline(odds_data):
    for bk_name, market_name, market in _iter_bookmaker_markets(odds_data):
        if _norm(market_name) not in _MONEYLINE_NAMES:
            continue

        outcomes = market.get("odds")
        if outcomes is None:
            outcomes = market.get("outcomes")

        # Shape A: [{"home": "2.10", "draw": "3.40", "away": "3.20"}]
        if isinstance(outcomes, list) and outcomes and isinstance(outcomes[0], dict) and (
            "home" in outcomes[0] or "draw" in outcomes[0]
        ):
            row = outcomes[0]
            home_o = _to_float(row.get("home"))
            draw_o = _to_float(row.get("draw"))
            away_o = _to_float(row.get("away"))
            if home_o and away_o:
                return home_o, draw_o, away_o

        # Shape B: [{"name": "Home", "price": "2.10"}, {"name": "Draw", ...}, ...]
        if isinstance(outcomes, list) and outcomes and isinstance(outcomes[0], dict) and "name" in outcomes[0]:
            home_o = draw_o = away_o = None
            for o in outcomes:
                label = _norm(o.get("name", ""))
                price = _to_float(o.get("price") or o.get("odds"))
                if label in ("home", "1"):
                    home_o = price
                elif label in ("draw", "x"):
                    draw_o = price
                elif label in ("away", "2"):
                    away_o = price
            if home_o and away_o:
                return home_o, draw_o, away_o

        # Shape C: market itself has home/draw/away directly
        home_o = _to_float(market.get("home"))
        draw_o = _to_float(market.get("draw"))
        away_o = _to_float(market.get("away"))
        if home_o and away_o:
            return home_o, draw_o, away_o

    return None, None, None


def _parse_totals_25(odds_data):
    for bk_name, market_name, market in _iter_bookmaker_markets(odds_data):
        if _norm(market_name) not in _TOTALS_NAMES:
            continue

        outcomes = market.get("odds")
        if outcomes is None:
            outcomes = market.get("outcomes")

        if not isinstance(outcomes, list):
            continue

        for row in outcomes:
            if not isinstance(row, dict):
                continue

            line = row.get("hdp")
            if line is None:
                line = row.get("point") or row.get("line")

            try:
                line_val = float(line)
            except (TypeError, ValueError):
                continue

            if abs(line_val - 2.5) > 0.01:
                continue

            over_o = _to_float(row.get("over"))
            under_o = _to_float(row.get("under"))

            if over_o and under_o:
                return over_o, under_o

    return None, None


def _add_pct(d, keys):
    vals = [d.get(k) for k in keys]
    if any(v is None or v <= 0 for v in vals):
        return d
    raw = [1 / v for v in vals]
    total = sum(raw)
    if total <= 0:
        return d
    for k, r in zip(keys, raw):
        d[k.replace("_odds", "_pct")] = round(r / total * 100, 1)
    return d


async def get_odds_api_io_fallback(home, away):
    odds_data = await _get_raw_odds(home, away)
    if not odds_data:
        return None

    home_o, draw_o, away_o = _parse_moneyline(odds_data)
    if not home_o or not away_o:
        return None

    result = {"home_odds": home_o, "draw_odds": draw_o, "away_odds": away_o}
    return _add_pct(result, ["home_odds", "draw_odds", "away_odds"])


async def get_ou25_api_io_fallback(home, away):
    odds_data = await _get_raw_odds(home, away)
    if not odds_data:
        return None

    over_o, under_o = _parse_totals_25(odds_data)
    if not over_o or not under_o:
        return None

    result = {"over_odds": over_o, "under_odds": under_o}
    return _add_pct(result, ["over_odds", "under_odds"])


async def debug_raw_event_odds(home, away):
    """
    Not used by /predict — call this manually (e.g. wire up a temporary
    debug endpoint like the AnnaBet/GOAL API ones already in app.py) if
    the parsers above return None and you need to see the real response
    shape to fix _parse_moneyline() / _parse_totals_25().
    """
    event = await _find_event(home, away)
    if not event:
        return {"error": "no matching event found", "home": home, "away": away}

    odds_data = await _get_raw_odds(home, away)
    return {"event": event, "raw_odds": odds_data}
       
