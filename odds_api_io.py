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

# Cache: the account's valid bookmaker list (free tier = 2 recreational books)
_bookmakers_cache: dict = {"data": None, "fetched_at": 0}
BOOKMAKERS_CACHE_TTL = 24 * 60 * 60  # 24 hours — this rarely changes


# Well-known recreational (non-sharp, non-exchange) bookmakers, in preference
# order. The free plan explicitly excludes sharp/exchange books (confirmed by
# a live 403 error naming "10BET" as one such excluded book), so rather than
# blindly grabbing the first N names from the full 265+ catalog, we match
# against known-recreational names and use whichever the account's real list
# actually contains.
PREFERRED_RECREATIONAL_BOOKMAKERS = [
    "bet365", "unibet", "betway", "william hill", "1xbet",
    "bwin", "betmgm", "draftkings", "betsson", "888sport",
]


def _pick_recreational_bookmakers(all_bookmakers: list, n: int = 2) -> list:
    lower_map = {str(b).lower(): b for b in all_bookmakers}
    picked = []
    for pref in PREFERRED_RECREATIONAL_BOOKMAKERS:
        if pref in lower_map and lower_map[pref] not in picked:
            picked.append(lower_map[pref])
        if len(picked) >= n:
            break
    return picked


async def _get_valid_bookmakers() -> list:
    """
    Fetch the account's actual valid bookmaker identifiers from /v3/bookmakers
    instead of guessing a name — their API rejected 'bet365' as invalid on
    first try, so this asks them directly what IS valid for this account
    (free tier = 2 specific recreational books) and uses that.
    """
    if _bookmakers_cache["data"] and time.time() - _bookmakers_cache["fetched_at"] < BOOKMAKERS_CACHE_TTL:
        return _bookmakers_cache["data"]

    if not ODDS_API_IO_KEY:
        return []

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                f"{ODDS_API_IO_BASE}/bookmakers",
                params={"apiKey": ODDS_API_IO_KEY},
            )
        if resp.status_code != 200:
            print(f"  [odds_api_io] Bookmakers fetch failed: HTTP {resp.status_code} — {resp.text[:200]}")
            return []

        data = resp.json()
        bookmakers = data if isinstance(data, list) else (data.get("bookmakers") or data.get("data") or [])
        # Bookmakers might be plain strings or objects with a name/id/slug field
        ids = []
        for b in bookmakers:
            if isinstance(b, str):
                ids.append(b)
            elif isinstance(b, dict):
                ids.append(b.get("id") or b.get("slug") or b.get("name") or b.get("key"))
        ids = [i for i in ids if i]
        print(f"  [odds_api_io] Valid bookmakers for this account: {ids}")
        _bookmakers_cache["data"] = ids
        _bookmakers_cache["fetched_at"] = time.time()
        return ids
    except Exception as e:
        print(f"  [odds_api_io] Exception fetching bookmakers: {e}")
        return []


# Cache: raw /odds response per event, shared by both the 1X2 and O/U 2.5
# fallback functions so checking both markets for one match costs a single
# API call, not two.
_raw_odds_cache: dict[str, dict] = {}


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


# Age-group/reserve qualifiers that, if present in a matched event but NOT
# in the original query, signal a cross-match into the wrong competition
# (e.g. matching "Sutherland S." to "Sutherland Sharks FC U20" instead of
# the intended senior team) — reject those matches rather than accept them.
_SUSPICIOUS_QUALIFIERS = ["u17", "u18", "u19", "u20", "u21", "u23", "reserve", "reserves", "youth", "academy"]


def _has_unwanted_qualifier(query: str, matched: str) -> bool:
    q, m = f" {query.lower()} ", f" {matched.lower()} "
    return any(qual in m and qual not in q for qual in _SUSPICIOUS_QUALIFIERS)


async def _find_event_id(home_team: str, away_team: str):
    events = await _get_football_events()
    best_id, best_score = None, 0.0
    best_home, best_away = "", ""

    for ev in events:
        ev_home, ev_away = _extract_team_names(ev)
        if not ev_home or not ev_away:
            continue
        score = _similar(ev_home, home_team) + _similar(ev_away, away_team)
        if score > best_score:
            best_score = score
            best_id = _extract_event_id(ev)
            best_home, best_away = ev_home, ev_away

    if best_id is None or best_score < 1.2:
        print(f"  [odds_api_io] No confident event match for '{home_team}' vs '{away_team}' "
              f"(best score: {best_score:.2f})")
        return None

    if _has_unwanted_qualifier(home_team, best_home) or _has_unwanted_qualifier(away_team, best_away):
        print(f"  [odds_api_io] Rejected cross-match into different competition: "
              f"'{home_team}' vs '{away_team}' matched '{best_home}' vs '{best_away}' "
              f"— looks like a different (youth/reserve) competition")
        return None

    return best_id


def _extract_hda_from_odds(data: dict):
    """
    Confirmed real schema (from live Render logs):
    data["bookmakers"] is a DICT keyed by bookmaker name (e.g. "Bet365"),
    each value a LIST of market objects. The market named "ML" (Moneyline)
    has odds: [{"home": "2.000", "draw": "4.000", "away": "2.750"}].
    """
    home_odds, draw_odds, away_odds = [], [], []

    bookmakers = data.get("bookmakers")
    if not isinstance(bookmakers, dict):
        print(f"  [odds_api_io] Unexpected 'bookmakers' shape (not a dict) — raw: {str(data)[:800]}")
        return None

    for bm_name, markets in bookmakers.items():
        if not isinstance(markets, list):
            continue
        for market in markets:
            if market.get("name") != "ML":  # "ML" = Moneyline = 1X2
                continue
            for outcome in market.get("odds") or []:
                try:
                    h, d, a = outcome.get("home"), outcome.get("draw"), outcome.get("away")
                    if h: home_odds.append(float(h))
                    if d: draw_odds.append(float(d))
                    if a: away_odds.append(float(a))
                except (TypeError, ValueError):
                    continue

    def avg(lst):
        return round(sum(lst) / len(lst), 2) if lst else None

    h_odd, d_odd, a_odd = avg(home_odds), avg(draw_odds), avg(away_odds)
    if not h_odd:
        print(f"  [odds_api_io] Could not find ML market odds — raw shape: {str(data)[:2000]}")
        return None

    def pct(o):
        return round(100 / o, 1) if o else None

    return {
        "home_odds": h_odd, "draw_odds": d_odd, "away_odds": a_odd,
        "home_pct": pct(h_odd), "draw_pct": pct(d_odd), "away_pct": pct(a_odd),
    }


def _extract_ou25_from_odds(data: dict):
    """
    Same confirmed schema, but reads the "Goals Over/Under" market instead
    of "ML". That market's odds list holds entries per goal line (hdp),
    e.g. {"hdp": 2.5, "over": "1.333", "under": "3.250"} — only hdp==2.5
    is used here.
    """
    over_odds, under_odds = [], []

    bookmakers = data.get("bookmakers")
    if not isinstance(bookmakers, dict):
        return None

    for bm_name, markets in bookmakers.items():
        if not isinstance(markets, list):
            continue
        for market in markets:
            if market.get("name") != "Goals Over/Under":
                continue
            for outcome in market.get("odds") or []:
                try:
                    if outcome.get("hdp") != 2.5:
                        continue
                    o, u = outcome.get("over"), outcome.get("under")
                    if o: over_odds.append(float(o))
                    if u: under_odds.append(float(u))
                except (TypeError, ValueError):
                    continue

    def avg(lst):
        return round(sum(lst) / len(lst), 2) if lst else None

    o_odd, u_odd = avg(over_odds), avg(under_odds)
    if not o_odd:
        print(f"  [odds_api_io] Could not find Goals Over/Under 2.5 market — raw shape: {str(data)[:2000]}")
        return None

    def pct(o):
        return round(100 / o, 1) if o else None

    return {
        "over_odds": o_odd, "under_odds": u_odd,
        "over_pct": pct(o_odd), "under_pct": pct(u_odd),
    }


async def _fetch_raw_odds_for_event(event_id) -> dict | None:
    """
    Fetches the full raw /odds response for one event and caches it —
    shared by both get_odds_api_io_fallback (1X2) and
    get_ou25_api_io_fallback (O/U 2.5) so checking both markets for the
    same match only costs ONE API call, not two.
    """
    cache_key = str(event_id)
    if cache_key in _raw_odds_cache:
        return _raw_odds_cache[cache_key]

    valid_bookmakers = await _get_valid_bookmakers()
    if not valid_bookmakers:
        print(f"  [odds_api_io] No valid bookmakers available for this account — skipping odds fetch for event {event_id}")
        return None

    recreational_picks = _pick_recreational_bookmakers(valid_bookmakers, n=2)
    if not recreational_picks:
        print(f"  [odds_api_io] None of the known recreational bookmakers found in this account's list "
              f"— skipping odds fetch for event {event_id}")
        return None
    bookmakers_param = ",".join(recreational_picks)
    print(f"  [odds_api_io] Using bookmakers: {bookmakers_param}")

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                f"{ODDS_API_IO_BASE}/odds",
                params={"apiKey": ODDS_API_IO_KEY, "eventId": event_id, "bookmakers": bookmakers_param},
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
        _raw_odds_cache[cache_key] = data
        print(f"  [odds_api_io] Fetched raw odds for event {event_id} "
              f"(rate limit remaining: {resp.headers.get('x-ratelimit-remaining', '?')})")
        return data

    except Exception as e:
        print(f"  [odds_api_io] Exception fetching odds for event {event_id}: {e}")
        return None


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

    data = await _fetch_raw_odds_for_event(event_id)
    if not data:
        return None

    result = _extract_hda_from_odds(data)
    if result:
        print(f"  [odds_api_io] Fallback 1X2 odds found for event {event_id}")
    return result


async def get_ou25_api_io_fallback(home_team: str, away_team: str):
    """
    Returns market Over/Under 2.5 goals odds:
    { over_odds, under_odds, over_pct, under_pct }
    Same fail-safe pattern as get_odds_api_io_fallback. Reuses the same
    cached raw fetch as the 1X2 fallback — if that one already ran for
    this match, this costs zero extra API quota.
    """
    if not ODDS_API_IO_KEY:
        return None

    event_id = await _find_event_id(home_team, away_team)
    if not event_id:
        return None

    data = await _fetch_raw_odds_for_event(event_id)
    if not data:
        return None

    result = _extract_ou25_from_odds(data)
    if result:
        print(f"  [odds_api_io] Fallback O/U 2.5 odds found for event {event_id}")
    return result
