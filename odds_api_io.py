"""
Kickwise — Odds-API.io Odds Module
-----------------------------------
Odds source #1 in combined_odds.py. Free tier: 100 requests/hour.

HOW TO USE: save next to app.py, set ODDS_API_IO_KEY on Render.

SCHEMA NOTE: confirmed from live logs — data["bookmakers"] is a dict keyed by
bookmaker name; the "ML" market holds 1X2 odds and "Goals Over/Under" holds
the goal lines (hdp == 2.5 is used here).

FIX (2026-09-16): accent stripping in _similar() (Grêmio vs Gremio), and the
rejected-candidate names are logged.

FIX (2026-11-01) — HOURLY LIMIT (429) PROTECTION. The Odds-API.io dashboard
showed repeated "429 Hourly limit reached" errors, including the SAME event
requested twice 2 seconds apart. Causes found in the old code:
  * a failed odds fetch was never remembered, so the O/U lookup (and every
    later match) called the API again straight after a 429 — wasted calls
    and a slow fall-through to the next source;
  * every match cost one /odds call, so a day with 100+ matches could not fit
    inside 100 requests/hour;
  * cached odds never expired.
What changed:
  1. ONE central request function (_api_get) used by every call. It keeps a
     sliding-window counter and refuses to go over ODDS_API_IO_HOURLY_BUDGET
     (default 90, leaving a reserve) — those matches fall straight through to
     OddStorm / Oddsbook instead of causing 429s.
  2. On a 429 the module reads the reset time (x-ratelimit-reset header, or
     the "resets in N minutes" text) and stops calling until then. It also
     stops early when x-ratelimit-remaining drops to the reserve.
  3. Failures are remembered briefly (404 / bad responses) so one match never
     triggers the same failing call twice.
  4. Cached odds now expire after RAW_ODDS_CACHE_TTL (3 hours).
  5. NEW: prefetch_odds_for_matches() fetches up to 10 events per request
     through /v3/odds/multi (documented as ONE request for up to 10 events),
     so 200 matches cost about 20 requests instead of 200. Call it once when
     the day's fixtures are known (see the /fixtures-v2 patch for app.py).
"""

import os
import re
import time
import unicodedata
from collections import deque
from datetime import datetime, timezone
from difflib import SequenceMatcher

import httpx

ODDS_API_IO_KEY = os.environ.get("ODDS_API_IO_KEY", "")
ODDS_API_IO_BASE = "https://api.odds-api.io/v3"

# Self-imposed ceiling, below the real 100/hour limit (reserve for events +
# bookmakers calls and for other tools using the same key).
HOURLY_BUDGET = int(os.environ.get("ODDS_API_IO_HOURLY_BUDGET", "90") or 90)
RATE_RESERVE = 2                      # stop when the API says this few calls remain
DEFAULT_BLOCK_SECONDS = 600           # used if a 429 gives no reset time
RAW_ODDS_CACHE_TTL = 3 * 60 * 60      # 3 hours
FAILED_EVENT_TTL = 10 * 60            # remember a failing event for 10 minutes
MULTI_BATCH_SIZE = 10                 # documented maximum for /odds/multi

# Cache: one entry holding today's football events list
_events_cache: dict[str, tuple[float, list]] = {}
EVENTS_CACHE_TTL = 2 * 60 * 60
_events_fail_until = 0.0

# Cache: the account's valid bookmaker list
_bookmakers_cache: dict = {"data": None, "fetched_at": 0, "fail_until": 0.0}
BOOKMAKERS_CACHE_TTL = 24 * 60 * 60

# Cache: raw /odds response per event -> (stored_at, data). Shared by the 1X2
# and O/U 2.5 functions so one match costs a single API call (or zero when
# prefetched).
_raw_odds_cache: dict[str, tuple[float, dict]] = {}
_failed_events: dict[str, float] = {}

# ---- rate-limit state ----
_call_times: deque = deque()
_blocked_until = 0.0
_last_block_log = 0.0


def _cache_get(event_key: str):
    entry = _raw_odds_cache.get(event_key)
    if not entry:
        return None
    if time.time() - entry[0] > RAW_ODDS_CACHE_TTL:
        _raw_odds_cache.pop(event_key, None)
        return None
    return entry[1]


def _cache_put(event_key: str, data: dict):
    _raw_odds_cache[event_key] = (time.time(), data)


def _event_recently_failed(event_key: str) -> bool:
    until = _failed_events.get(event_key, 0.0)
    if time.time() < until:
        return True
    _failed_events.pop(event_key, None)
    return False


def _log_blocked(reason: str):
    """Print at most once a minute so a long block doesn't flood the logs."""
    global _last_block_log
    now = time.time()
    if now - _last_block_log > 60:
        _last_block_log = now
        print(f"  [odds_api_io] Skipping API call — {reason}")


def _parse_reset_epoch(resp) -> float | None:
    """x-ratelimit-reset is an ISO 8601 timestamp (documented)."""
    raw = resp.headers.get("x-ratelimit-reset")
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except Exception:
        return None


def _block_from_429(resp):
    """Stop calling until the hourly window resets."""
    global _blocked_until
    now = time.time()
    reset_epoch = _parse_reset_epoch(resp)
    if reset_epoch and reset_epoch > now:
        wait = reset_epoch - now
    else:
        m = re.search(r"resets in (\d+) minutes?(?: and (\d+) seconds?)?", resp.text or "", re.I)
        if m:
            wait = int(m.group(1)) * 60 + int(m.group(2) or 0)
        else:
            wait = DEFAULT_BLOCK_SECONDS
    _blocked_until = max(_blocked_until, now + wait + 5)
    print(f"  [odds_api_io] Hourly limit reached (429) — pausing Odds-API.io for "
          f"{int(wait // 60)} min {int(wait % 60)} s; other odds sources take over")


def _note_rate_headers(resp):
    """Stop early (before a 429) when the API reports almost no calls left."""
    global _blocked_until
    remaining = resp.headers.get("x-ratelimit-remaining")
    try:
        remaining = int(remaining)
    except (TypeError, ValueError):
        return
    if remaining <= RATE_RESERVE:
        now = time.time()
        reset_epoch = _parse_reset_epoch(resp)
        wait = (reset_epoch - now) if reset_epoch and reset_epoch > now else DEFAULT_BLOCK_SECONDS
        _blocked_until = max(_blocked_until, now + wait + 5)
        print(f"  [odds_api_io] Only {remaining} calls left this hour — pausing for {int(wait // 60)} min")


async def _api_get(path: str, params: dict, timeout: float = 15.0):
    """
    The ONLY place that calls Odds-API.io. Returns the httpx response, or None
    when the call was skipped (no key / paused after a 429 / hourly budget
    used up) or failed to connect. Callers treat None as "no data here".
    """
    if not ODDS_API_IO_KEY:
        return None

    now = time.time()
    if now < _blocked_until:
        _log_blocked(f"paused for another {int((_blocked_until - now) // 60)} min after the hourly limit")
        return None

    while _call_times and now - _call_times[0] > 3600:
        _call_times.popleft()
    if len(_call_times) >= HOURLY_BUDGET:
        _log_blocked(f"self-imposed budget of {HOURLY_BUDGET} calls/hour used up")
        return None
    _call_times.append(now)

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(
                f"{ODDS_API_IO_BASE}{path}",
                params={**params, "apiKey": ODDS_API_IO_KEY},
            )
    except Exception as e:
        print(f"  [odds_api_io] Request to {path} failed: {type(e).__name__}")
        return None

    if resp.status_code == 429:
        _block_from_429(resp)
    else:
        _note_rate_headers(resp)
    return resp


# Well-known recreational (non-sharp, non-exchange) bookmakers, in preference
# order. The free plan excludes sharp/exchange books (a live 403 named "10BET").
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
    """The account's valid bookmaker identifiers (cached 24h; a failure is
    remembered for 5 minutes so it is not retried for every match)."""
    if _bookmakers_cache["data"] and time.time() - _bookmakers_cache["fetched_at"] < BOOKMAKERS_CACHE_TTL:
        return _bookmakers_cache["data"]
    if time.time() < _bookmakers_cache["fail_until"]:
        return []
    if not ODDS_API_IO_KEY:
        return []

    resp = await _api_get("/bookmakers", {}, timeout=15.0)
    if resp is None:
        return []
    if resp.status_code != 200:
        print(f"  [odds_api_io] Bookmakers fetch failed: HTTP {resp.status_code} — {resp.text[:200]}")
        _bookmakers_cache["fail_until"] = time.time() + 300
        return []

    try:
        data = resp.json()
        bookmakers = data if isinstance(data, list) else (data.get("bookmakers") or data.get("data") or [])
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
        print(f"  [odds_api_io] Exception reading bookmakers: {e}")
        _bookmakers_cache["fail_until"] = time.time() + 300
        return []


def _strip_accents(s: str) -> str:
    """"Grêmio" -> "Gremio", "São Paulo" -> "Sao Paulo" (see FIX note above)."""
    return "".join(
        c for c in unicodedata.normalize("NFKD", s)
        if not unicodedata.combining(c)
    )


def _similar(a: str, b: str) -> float:
    """Abbreviation-aware fuzzy matcher with accent-stripping applied first."""
    a = _strip_accents(a)
    b = _strip_accents(b)

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
    """Defensive extraction — tries several likely field name variants."""
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
    global _events_fail_until
    cached = _events_cache.get("football")
    if cached and time.time() - cached[0] < EVENTS_CACHE_TTL:
        return cached[1]

    if not ODDS_API_IO_KEY:
        print("  [odds_api_io] No ODDS_API_IO_KEY set — skipping")
        return []
    if time.time() < _events_fail_until:
        return cached[1] if cached else []

    resp = await _api_get("/events", {"sport": "football"}, timeout=20.0)
    if resp is None:
        return cached[1] if cached else []
    if resp.status_code != 200:
        if resp.status_code == 401:
            print("  [odds_api_io] Invalid API key (401)")
        elif resp.status_code != 429:
            print(f"  [odds_api_io] Events fetch failed: HTTP {resp.status_code} — {resp.text[:200]}")
        _events_fail_until = time.time() + 300
        return cached[1] if cached else []

    try:
        data = resp.json()
        events = data if isinstance(data, list) else (data.get("events") or data.get("data") or [])
        print(f"  [odds_api_io] Fetched {len(events)} football events "
              f"(rate limit remaining: {resp.headers.get('x-ratelimit-remaining', '?')})")
        if events:
            sample_home, _ = _extract_team_names(events[0])
            if not sample_home:
                print(f"  [odds_api_io] WARNING: could not extract team names from sample event — "
                      f"raw shape: {str(events[0])[:300]}")
        _events_cache["football"] = (time.time(), events)
        return events
    except Exception as e:
        print(f"  [odds_api_io] Exception reading events: {e}")
        _events_fail_until = time.time() + 300
        return cached[1] if cached else []


# Age-group/reserve qualifiers that signal a cross-match into the wrong competition.
_SUSPICIOUS_QUALIFIERS = ["u17", "u18", "u19", "u20", "u21", "u23", "reserve", "reserves", "youth", "academy"]


def _has_unwanted_qualifier(query: str, matched: str) -> bool:
    q, m = f" {query.lower()} ", f" {matched.lower()} "
    return any(qual in m and qual not in q for qual in _SUSPICIOUS_QUALIFIERS)


def _filter_events_near_today(events: list, hours_before: float = 6, hours_after: float = 36) -> list:
    """Keeps events starting within a window around now (events carry an ISO
    'date'). Used by the batch prefetch to avoid fuzzy-matching every match
    against thousands of far-future events. Falls back to ALL events when no
    event has a parsable date or nothing is in the window."""
    now = time.time()
    kept, dated = [], 0
    for ev in events:
        raw = ev.get("date") if isinstance(ev, dict) else None
        if not raw:
            continue
        try:
            ts = datetime.fromisoformat(str(raw).replace("Z", "+00:00")).timestamp()
        except Exception:
            continue
        dated += 1
        if now - hours_before * 3600 <= ts <= now + hours_after * 3600:
            kept.append(ev)
    return kept if kept else events


async def _find_event_id(home_team: str, away_team: str, events: list | None = None):
    if events is None:
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
              f"(best score: {best_score:.2f}"
              f"{f', closest candidate: {best_home!r} vs {best_away!r}' if best_id else ''})")
        return None

    if _has_unwanted_qualifier(home_team, best_home) or _has_unwanted_qualifier(away_team, best_away):
        print(f"  [odds_api_io] Rejected cross-match into different competition: "
              f"'{home_team}' vs '{away_team}' matched '{best_home}' vs '{best_away}' "
              f"— looks like a different (youth/reserve) competition")
        return None

    return best_id


def _extract_hda_from_odds(data: dict):
    """data["bookmakers"] is a DICT keyed by bookmaker name; the "ML" market
    has odds: [{"home": "2.000", "draw": "4.000", "away": "2.750"}]."""
    home_odds, draw_odds, away_odds = [], [], []

    bookmakers = data.get("bookmakers")
    if not isinstance(bookmakers, dict):
        print(f"  [odds_api_io] Unexpected 'bookmakers' shape (not a dict) — raw: {str(data)[:800]}")
        return None

    for bm_name, markets in bookmakers.items():
        if not isinstance(markets, list):
            continue
        for market in markets:
            if market.get("name") != "ML":
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
    """Reads the "Goals Over/Under" market; only the hdp == 2.5 line is used."""
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


async def _bookmakers_param() -> str | None:
    valid = await _get_valid_bookmakers()
    if not valid:
        return None
    picks = _pick_recreational_bookmakers(valid, n=2)
    if not picks:
        print("  [odds_api_io] None of the known recreational bookmakers found in this account's list")
        return None
    return ",".join(picks)


async def _fetch_raw_odds_for_event(event_id) -> dict | None:
    """
    Raw /odds response for one event, cached (3h) and shared by the 1X2 and
    O/U 2.5 lookups — so checking both markets for a match costs ONE call at
    most, and ZERO when prefetch_odds_for_matches() already loaded it.
    """
    key = str(event_id)
    cached = _cache_get(key)
    if cached is not None:
        return cached
    if _event_recently_failed(key):
        return None

    bookmakers_param = await _bookmakers_param()
    if not bookmakers_param:
        return None

    resp = await _api_get("/odds", {"eventId": event_id, "bookmakers": bookmakers_param}, timeout=15.0)
    if resp is None:
        return None
    if resp.status_code == 200:
        try:
            data = resp.json()
        except Exception as e:
            print(f"  [odds_api_io] Odds response for event {event_id} was not JSON: {e}")
            _failed_events[key] = time.time() + FAILED_EVENT_TTL
            return None
        _cache_put(key, data)
        print(f"  [odds_api_io] Fetched raw odds for event {event_id} "
              f"(rate limit remaining: {resp.headers.get('x-ratelimit-remaining', '?')})")
        return data

    if resp.status_code == 429:
        return None            # the global pause is already set — don't retry
    if resp.status_code == 401:
        print("  [odds_api_io] Invalid API key (401) fetching odds")
    elif resp.status_code == 404:
        print(f"  [odds_api_io] Event {event_id} not found when fetching odds")
    else:
        print(f"  [odds_api_io] Odds fetch failed: HTTP {resp.status_code} — {resp.text[:200]}")
    _failed_events[key] = time.time() + FAILED_EVENT_TTL
    return None


async def prefetch_odds_for_matches(pairs) -> int:
    """
    Loads odds for many matches with as few requests as possible.
    pairs: list of (home_team, away_team). Events are matched with the same
    fuzzy matcher the per-match lookups use, then fetched 10 at a time through
    /v3/odds/multi (documented as ONE request for up to 10 events).
    Returns the number of events cached. Never raises.
    """
    try:
        if not ODDS_API_IO_KEY:
            return 0
        bookmakers_param = await _bookmakers_param()
        if not bookmakers_param:
            return 0

        candidates = _filter_events_near_today(await _get_football_events())
        ids, seen = [], set()
        for home, away in pairs:
            eid = await _find_event_id(home, away, events=candidates)
            if not eid:
                continue
            key = str(eid)
            if key in seen or _cache_get(key) is not None or _event_recently_failed(key):
                continue
            seen.add(key)
            ids.append(eid)

        print(f"  [odds_api_io] Prefetch: {len(pairs)} matches -> {len(ids)} events to load "
              f"in {(len(ids) + MULTI_BATCH_SIZE - 1) // MULTI_BATCH_SIZE} request(s)")

        loaded = 0
        for i in range(0, len(ids), MULTI_BATCH_SIZE):
            chunk = ids[i:i + MULTI_BATCH_SIZE]
            resp = await _api_get(
                "/odds/multi",
                {"eventIds": ",".join(str(x) for x in chunk), "bookmakers": bookmakers_param},
                timeout=30.0,
            )
            if resp is None:
                break                      # paused / over budget / connection failure
            if resp.status_code == 429:
                break
            if resp.status_code != 200:
                print(f"  [odds_api_io] Multi-odds fetch failed: HTTP {resp.status_code} — {resp.text[:200]}")
                continue
            try:
                items = resp.json()
            except Exception as e:
                print(f"  [odds_api_io] Multi-odds response was not JSON: {e}")
                continue
            if isinstance(items, dict):
                items = items.get("data") or items.get("events") or []
            got = 0
            for item in items if isinstance(items, list) else []:
                if not isinstance(item, dict):
                    continue
                item_id = _extract_event_id(item)
                if item_id is None:
                    continue
                _cache_put(str(item_id), item)
                got += 1
            loaded += got
            if got == 0:
                print(f"  [odds_api_io] Multi-odds returned no usable events — raw: {str(items)[:300]}")
        print(f"  [odds_api_io] Prefetch done: {loaded} events cached "
              f"({len(_call_times)} calls used in the last hour)")
        return loaded
    except Exception as e:
        print(f"  [odds_api_io] Prefetch skipped: {type(e).__name__}: {e}")
        return 0


async def get_odds_api_io_fallback(home_team: str, away_team: str):
    """
    Returns odds in the SAME shape as odds.py's get_odds_for_card():
    { home_odds, draw_odds, away_odds, home_pct, draw_pct, away_pct }
    Fails safe to None on any error, missing key, no coverage, or while the
    hourly limit pause is active.
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
        print(f"  [odds_api_io] 1X2 odds found for event {event_id}")
    return result


async def get_ou25_api_io_fallback(home_team: str, away_team: str):
    """
    Returns market Over/Under 2.5 goals odds:
    { over_odds, under_odds, over_pct, under_pct }
    Reuses the same cached raw fetch as the 1X2 lookup.
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
        print(f"  [odds_api_io] O/U 2.5 odds found for event {event_id}")
    return result
