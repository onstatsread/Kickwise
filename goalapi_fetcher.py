"""
GOAL API fetcher — candidate full replacement for AnnaBet AND
Oddsbook. No scraping, no Cloudflare fight, no Playwright/browser
needed — plain authenticated REST calls.

CONFIRMED (2026-09-06) via GitHub Actions testing:
- Base URL: https://api.goal-api.com/v1, auth via
  "Authorization: Bearer {key}" header.
- GET /standings/{leagueId} returns ALL of combined + home + away
  splits in ONE call — fields are overallLeague{Played,W,D,L,GF,GA,PTS},
  homeLeague{...same...}, awayLeague{...same...}, all as STRINGS
  needing int() conversion. Team name is in row["team"]["name"].
  Confirmed populated for real domestic leagues (La Liga); may be
  null for international tournaments (Copa America) that don't
  track home/away splits — handled gracefully below (falls back to
  combined-only stats in that case, matching AnnaBet's own behavior
  when split data is unavailable).
- GET /fixtures/date/{date} returns all fixtures for that date across
  every league in one call (NOT yet field-confirmed in detail — built
  from the SDK's documented shape: homeTeam.name, awayTeam.name,
  homeScore, awayScore, plus a fixture id — verify against a real
  response and adjust if field names differ).
- GET /fixtures/{id}/odds — odds shape NOT yet confirmed. Placeholder
  parsing below needs verification against a real response.

Rate limits: 1,000 requests/day on free tier. Estimated real usage:
~61 calls for standings (1 per active league) + 1 call for the day's
fixtures + ~1 call per match needing odds — comfortably under budget.
"""

import os
import time
import requests

GOAL_API_BASE = "https://api.goal-api.com/v1"
GOAL_API_KEY = os.environ.get("GOAL_API_KEY", "")

_SESSION = requests.Session()
_SESSION.headers.update({"Authorization": f"Bearer {GOAL_API_KEY}"})


_STATS_CACHE = {}
STATS_CACHE_TTL = 3600  # 1 hour — standings don't change mid-day

_FIXTURES_CACHE = {}
FIXTURES_CACHE_TTL = 300  # 5 minutes


def _get(path, params=None, retries=2):
    """
    Authenticated GET with basic retry — GOAL API's own SDK documents
    exponential backoff with jitter for 429/5xx; this is a simpler
    fixed-delay version. Returns parsed JSON dict, or None on failure.
    """
    url = f"{GOAL_API_BASE}{path}"

    for attempt in range(1, retries + 1):
        try:
            resp = _SESSION.get(url, params=params, timeout=20)
        except Exception as e:
            print(f"GOAL API request failed (attempt {attempt}): {url} -> {e}")
            if attempt < retries:
                time.sleep(3)
            continue

        if resp.status_code == 200:
            try:
                return resp.json()
            except Exception as e:
                print(f"GOAL API JSON parse failed: {url} -> {e}")
                return None

        if resp.status_code in (429, 502, 503):
            print(f"GOAL API {resp.status_code} (attempt {attempt}): {url}")
            if attempt < retries:
                time.sleep(5)
            continue

        # 404 etc — not worth retrying, just report and stop.
        print(f"GOAL API {resp.status_code}: {url} -> {resp.text[:300]}")
        return None

    return None


def _to_float(value, default=0.0):
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _to_int(value, default=0):
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


# ------------------------------------------------------------
# Team stats — combined + home/away splits in ONE call
# ------------------------------------------------------------

def fetch_team_stats(league_id):
    """
    Returns the SAME shape as AnnaBet's fetch_stats_annabet():

        {
            team_name: {
                "gp": ..., "gf": ..., "ga": ..., "tot": ...,
                "hgf": ..., "hga": ..., "htot": ...,
                "agf": ..., "aga": ..., "atot": ...,
            },
            ...
        }

    Single API call — GOAL API returns combined AND home/away splits
    together on /standings/{leagueId}, confirmed via testing.

    Falls back to 0-valued home/away splits if a league doesn't track
    them (e.g. international group-stage tournaments) — same
    graceful-degradation behavior AnnaBet's own code has when split
    data isn't available.
    """
    cache_key = league_id
    cached = _STATS_CACHE.get(cache_key)
    if cached and time.time() - cached[0] < STATS_CACHE_TTL:
        return cached[1]

    data = _get(f"/standings/{league_id}")

    if not data or not data.get("success", True) or not data.get("data"):
        return {}

    rows = data["data"]
    if not isinstance(rows, list):
        return {}

    result = {}

    for row in rows:
        team_name = (row.get("team") or {}).get("name") or row.get("teamName")
        if not team_name:
            continue

        gp = _to_int(row.get("overallLeaguePlayed"))
        gf = _to_int(row.get("overallLeagueGF"))
        ga = _to_int(row.get("overallLeagueGA"))

        hgp = _to_int(row.get("homeLeaguePlayed"))
        hgf_total = _to_int(row.get("homeLeagueGF"))
        hga_total = _to_int(row.get("homeLeagueGA"))

        agp = _to_int(row.get("awayLeaguePlayed"))
        agf_total = _to_int(row.get("awayLeagueGF"))
        aga_total = _to_int(row.get("awayLeagueGA"))

        result[team_name] = {
            "gp": gp,
            "gf": gf / gp if gp else 0,
            "ga": ga / gp if gp else 0,
            "tot": (gf + ga) / gp if gp else 0,
            "hgf": hgf_total / hgp if hgp else 0,
            "hga": hga_total / hgp if hgp else 0,
            "htot": (hgf_total + hga_total) / hgp if hgp else 0,
            "agf": agf_total / agp if agp else 0,
            "aga": aga_total / agp if agp else 0,
            "atot": (agf_total + aga_total) / agp if agp else 0,
        }

    _STATS_CACHE[cache_key] = (time.time(), result)
    return result


# ------------------------------------------------------------
# Fixtures — one call for the whole day, all leagues
# ------------------------------------------------------------

def fetch_fixtures_for_day(date_str):
    """
    date_str: 'YYYY-MM-DD'.

    Returns a list of fixtures:
        [{"id": ..., "league_id": ..., "league_name": ...,
          "home": ..., "away": ..., "kickoff": ...,
          "status": ..., "home_score": ..., "away_score": ...}, ...]

    CONFIRMED (2026-09-06) real field names — flat top-level fields,
    NOT the nested homeTeam/awayTeam objects. Those nested objects
    sometimes carry a DIFFERENT (globally-shared?) team name than the
    match-specific homeTeamName/awayTeamName fields (e.g. one fixture
    showed homeTeam.name="Firpo" vs homeTeamName="Luis Angel Firpo") —
    using the flat fields avoids that mismatch.
    """
    cache_key = date_str
    cached = _FIXTURES_CACHE.get(cache_key)
    if cached and time.time() - cached[0] < FIXTURES_CACHE_TTL:
        return cached[1]

    data = _get(f"/fixtures/date/{date_str}")

    if not data or not data.get("data"):
        return []

    rows = data["data"]
    if not isinstance(rows, list):
        return []

    fixtures = []

    for row in rows:
        fixtures.append({
            "id": row.get("id"),
            "league_id": row.get("leagueId"),
            "league_name": row.get("leagueName"),
            "home": row.get("homeTeamName"),
            "away": row.get("awayTeamName"),
            "kickoff": row.get("kickoffUtc"),
            "status": row.get("matchStatus"),
            "home_score": row.get("homeTeamScore"),
            "away_score": row.get("awayTeamScore"),
        })

    _FIXTURES_CACHE[cache_key] = (time.time(), fixtures)
    return fixtures


# ------------------------------------------------------------
# Odds — per fixture
# ------------------------------------------------------------

def fetch_market_odds(fixture_id):
    """
    CONFIRMED (2026-09-06): odds require a PAID GOAL API plan — free
    tier returns 403 "Feature not available in your plan" (capability:
    canAccessOdds) for /fixtures/:id/odds.

    Use oddsbook_odds.get_oddsbook_market_odds(home, away) instead for
    odds on the free tier — confirmed working via Playwright earlier
    in this project. This function is kept as a stub in case the plan
    is upgraded later, but should not be called on the free tier.
    """
    return {"market_odds": None, "market_ou25": None}
