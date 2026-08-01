"""
Kickwise — Odds-API.io Fallback Odds Module
---------------------------------------------
Alternative fallback odds source. Free tier: 100 requests/hour (up to
500/day), 2 recreational bookmakers, no credit card required — chosen
because their marketing claims broader lower-division league coverage
than API-Football (which is currently suspended on our account).

HOW TO USE:
1. Save this as `odds_api_io.py` next to app.py, odds.py, api_football.py.
2. Sign up free at https://odds-api.io and get your key.
3. Add ODDS_API_IO_KEY as an environment variable on Render.
4. In app.py, import get_odds_api_io_fallback and call it as a fallback
   tier — see the /predict integration.

IMPORTANT — SCHEMA CONFIDENCE NOTE:
This provider's exact JSON field names for events/odds weren't fully
confirmed against real data before writing this (their docs only show
partial code snippets, not full example responses). The parsing below
is DEFENSIVE — it tries several likely field name variants — and logs
the raw response shape to Render's logs whenever parsing comes up
empty, so if it doesn't work first try, checking Render's logs for
"[odds_api_io] Could not parse odds" or "WARNING: could not extract
team names" will show the actual shape to fix against, the same way
we diagnosed api_football.py's issues.
"""

import os
import time
import httpx
from difflib import SequenceMatcher

ODDS_API_IO_KEY = os.environ.get("ODDS_API_IO_KEY", "")
ODDS_API_IO_BASE = "https://api.odds-api.io/v3"

# Cache: one entry holding today's football events list
_events_cache: dict[str, tuple[float, list]] = {}
EVENTS_CACHE_TTL = 2 * 60 * 60  # 2 hours — generous 100/hr rate limit allows more frequent refresh than API-Football's 12h cache

# Cache: per-event odds lookups, so re-checking the same match doesn't spend quota twice
_odds_cache: dict[str, dict] = {}


def _similar(a: str, b: str) -> float:
    """Same abbreviation-aware fuzzy matcher used in odds.py and api_football.py."""
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


def _extract_team_names(event: dict) -> tuple:
    """Defensive extraction — tries several likely field name variants
    since the exact schema wasn't confirmed ahead of deployment."""
    home = (
        event.get("home_team") or event.get("homeTeam") or
        event.get("home") or event.get("team1") or ""
    )
    away = (
        event.get("away_team") or event.get("awayTeam") or
        event.get("away") or event.get("team2") or ""
    )
    if not home and event.get("participants") and len(event["participants"]) >= 2:
        home = event["participants"][0].get("name", "")
        away = event["participants"][1].get("name", "")
    return str(home), str(away)


def _extract_event_id(event: dict):
    return event.get("id") or event.get("eventId") or event.get("event_id")


async def _get_football_events() -> list:
    cached = _events_cache.get("football")
    if cached and time.time() - cached[0] < EVENTS_CACHE_TTL:
        return cached[1]

    if not ODDS_API_IO_KEY:
        print("  [odds_api_io] No ODDS_API_IO_KEY set — skipping fallback")
        return []

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.get(
                f"{ODDS_API_IO_BASE}/events",
                params={"apiKey": ODDS_API_IO_KEY, "sport": "football"},
            )
        if resp.status_code == 401:
            print("  [odds_api_io] Invalid API key (401)")
            return cached[1] if cached else []
        if resp.status_code == 429:
            print("  [odds_api_io] Rate limit exceeded (429) fetching events")
            return cached[1] if cached else []
        if resp.status_code != 200:
            print(f"  [odds_api_io] Events fetch failed: HTTP {resp.status_code} — {resp.text[:200]}")
            return cached[1] if cached else []

        data = resp.json()
        events = data if isinstance(data, list) else (data.get("events") or data.get("data") or [])
        print(f"  [odds_api_io] Fetched {len(events)} football events "
              f"(rate limit remaining: {resp.headers.get('x-ratelimit-remaining', '?')})")

        if events:
            sample_home, sample_away = _extract_team_names(events[0])
            if not sample_home:
                print(f"  [odds_api_io] WARNING: could not extract team names from sample event — "
                      f"raw shape: {str(events[0])[:300]}")

        _events_cache["football"] = (time.time(), events)
        return events
    except Exception as e:
        print(f"  [odds_api_io] Exception fetching events: {e}")
        return cached[1] if cached else []


async def _find_event_id(home_team: str, away_team: str):
    events = await _get_football_events()
    best_id, best_score = None, 0.0

    for ev in events:
        ev_home, ev_away = _extract_team_names(ev)
        if not ev_home or not ev_away:
            continue
        score = _similar(ev_home, home_team) + _similar(ev_away, away_team)
        if score > best_score:
            best_score = score
            best_id = _extract_event_id(ev)

    if best_id is None or best_score < 1.2:
        print(f"  [odds_api_io] No confident event match for '{home_team}' vs '{away_team}' "
              f"(best score: {best_score:.2f})")
        return None
    return best_id


def _extract_hda_from_odds(data: dict):
    """Defensive parsing of the /odds response into home/draw/away — tries
    several likely shapes since exact schema wasn't confirmed ahead of time."""
    home_odds, draw_odds, away_odds = [], [], []

    bookmakers = data.get("bookmakers") or data.get("odds") or []
    if isinstance(bookmakers, dict):
        bookmakers = [bookmakers]

    for bm in bookmakers:
        markets = bm.get("markets") if isinstance(bm, dict) else None
        if not markets:
            continue
        market = markets.get("moneyline") or markets.get("h2h") or markets.get("1x2") or {}

        if isinstance(market, dict):
            h = market.get("home") or market.get("1")
            d = market.get("draw") or market.get("x")
            a = market.get("away") or market.get("2")
            try:
                if h: home_odds.append(float(h))
                if d: draw_odds.append(float(d))
                if a: away_odds.append(float(a))
            except (TypeError, ValueError):
                pass
        elif isinstance(market, list):
            for outcome in market:
                name = str(outcome.get("name", "")).lower()
                price = outcome.get("price") or outcome.get("odd")
                try:
                    price = float(price)
                except (TypeError, ValueError):
                    continue
                if "home" in name or name == "1":
                    home_odds.append(price)
                elif "draw" in name or name == "x":
                    draw_odds.append(price)
                elif "away" in name or name == "2":
                    away_odds.append(price)

    def avg(lst):
        return round(sum(lst) / len(lst), 2) if lst else None

    h_odd, d_odd, a_odd = avg(home_odds), avg(draw_odds), avg(away_odds)
    if not h_odd:
        print(f"  [odds_api_io] Could not parse odds from response — raw shape: {str(data)[:400]}")
        return None

    def pct(o):
        return round(100 / o, 1) if o else None

    return {
        "home_odds": h_odd, "draw_odds": d_odd, "away_odds": a_odd,
        "home_pct": pct(h_odd), "draw_pct": pct(d_odd), "away_pct": pct(a_odd),
    }


async def get_odds_api_io_fallback(home_team: str, away_team: str):
    """
    Returns odds in the SAME shape as odds.py's get_odds_for_card() and
    api_football.py's get_fallback_odds():
    { home_odds, draw_odds, away_odds, home_pct, draw_pct, away_pct }
    Fails safe to None on any error, missing key, or no coverage — safe
    to call unconditionally as a fallback tier.
    """
    if not ODDS_API_IO_KEY:
        return None

    event_id = await _find_event_id(home_team, away_team)
    if not event_id:
        return None

    cache_key = str(event_id)
    if cache_key in _odds_cache:
        return _odds_cache[cache_key]

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                f"{ODDS_API_IO_BASE}/odds",
                params={"apiKey": ODDS_API_IO_KEY, "eventId": event_id, "bookmakers": "bet365"},
            )
        if resp.status_code == 401:
            print("  [odds_api_io] Invalid API key (401) fetching odds")
            return None
        if resp.status_code == 429:
            print(f"  [odds_api_io] Rate limit exceeded (429) fetching odds for event {event_id}")
            return None
        if resp.status_code == 404:
            print(f"  [odds_api_io] Event {event_id} not found when fetching odds")
            return None
        if resp.status_code != 200:
            print(f"  [odds_api_io] Odds fetch failed: HTTP {resp.status_code} — {resp.text[:200]}")
            return None

        data = resp.json()
        result = _extract_hda_from_odds(data)
        if result:
            _odds_cache[cache_key] = result
            print(f"  [odds_api_io] Fallback odds found for event {event_id} "
                  f"(rate limit remaining: {resp.headers.get('x-ratelimit-remaining', '?')})")
        return result

    except Exception as e:
        print(f"  [odds_api_io] Exception fetching odds for event {event_id}: {e}")
        return None
