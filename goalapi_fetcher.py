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
  every league. CONFIRMED (2026-09-12) this is a paginated list
  endpoint like every other list endpoint on GOAL API — response
  shape is {"success", "data": [...], "pagination": {"total", "limit",
  "offset", "hasMore"}}. Field names confirmed: homeTeamName,
  awayTeamName, leagueId, leagueName, kickoffUtc, matchStatus,
  homeTeamScore, awayTeamScore.
- GET /fixtures/{id}/odds — odds require a PAID plan, confirmed 403
  on free tier (capability: canAccessOdds).

Rate limits: 1,000 requests/day on free tier. Estimated real usage:
~61 calls for standings (1 per active league) + however many pages
the day's fixtures need (previously assumed 1, see fix below) +
~1 call per match needing odds — comfortably under budget even with
a few extra pages/day.

FIX (2026-09-15): /standings/{leagueId} is ALSO a paginated list
endpoint, per the same docs quoted above — but fetch_team_stats() was
calling it with no limit/offset at all, trusting whatever GOAL API's
default page size is. This is the exact same bug class as the
/fixtures/date/{date} truncation fixed 2026-09-12 and the Finland
Ykkosliiga id mixup fixed 2026-09-13: silent, no error, just missing
data past page 1. None of the 88 currently-mapped leagues are known
to exceed a typical default page size (biggest top-flight seen so far
is ~28 teams), so this hasn't caused an observed failure yet — but
it's exactly the kind of thing that fails silently the moment a
bigger league gets added, so fixed proactively rather than waiting
for it to bite. Now walks pages with offset until
pagination.hasMore is false, same pattern as fetch_fixtures_for_day()
below.
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

FIXTURES_PAGE_LIMIT = 100  # ceiling per GOAL API docs
STANDINGS_PAGE_LIMIT = 100  # same ceiling, for the standings pagination fix below


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


def _get_all_pages(path, params=None):
    """
    Walks a paginated GOAL API list endpoint (limit/offset, stops
    when pagination.hasMore is false) and returns every row across
    all pages. Shared by fetch_team_stats() and fetch_fixtures_for_day()
    so both get the same truncation-proof behavior.
    """
    params = dict(params or {})
    params.setdefault("limit", STANDINGS_PAGE_LIMIT)
    offset = 0
    rows = []

    while True:
        params["offset"] = offset
        data = _get(path, params=params)

        if not data or not data.get("data"):
            break

        page_rows = data["data"]
        if not isinstance(page_rows, list):
            break

        rows.extend(page_rows)

        pagination = data.get("pagination") or {}
        if not pagination.get("hasMore"):
            break

        offset += params["limit"]

    return rows


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

    GOAL API returns combined AND home/away splits together on
    /standings/{leagueId}, confirmed via testing. This now walks
    ALL pages of that endpoint (see FIX note at the top of the file)
    rather than trusting a single call to return every team.

    Falls back to 0-valued home/away splits if a league doesn't track
    them (e.g. international group-stage tournaments) — same
    graceful-degradation behavior AnnaBet's own code has when split
    data isn't available.
    """
    cache_key = league_id
    cached = _STATS_CACHE.get(cache_key)
    if cached and time.time() - cached[0] < STATS_CACHE_TTL:
        return cached[1]

    rows = _get_all_pages(f"/standings/{league_id}")

    if not rows:
        return {}

    # FIX (2026-09-13, v2): the earlier updatedAt-only comparison
    # wasn't reliable enough on its own — confirmed via raw dumps
    # that GOAL API's stale "Championship Group" rows sometimes have
    # updatedAt bumped by a bulk resync even though the underlying
    # game count never actually changed. Worse: max_gp is reported
    # across the WHOLE LEAGUE, so even if per-team dedup worked for
    # most teams, just ONE team lacking a "Current"-stage row (e.g.
    # newly promoted, or GOAL API hasn't created its current entry
    # yet) would still leave that team's stale, un-deduped row as the
    # league's reported max — exactly what happened with Austria.
    #
    # New approach: two passes. First, group every row by team name.
    # Second, for each team: if ANY row has stageName == "Current",
    # use that one (this label reliably marks the real, in-progress
    # season row — confirmed across Denmark and Austria's raw data).
    # A team with NO "Current"-labeled row at all is now SKIPPED
    # entirely (see FIX 2026-09-17 below) rather than falling back to
    # a stale row — leagues that don't use this group-stage split
    # (the majority) are unaffected either way.
    #
    # FIX (2026-09-17): the "fall back to most-recently-updated row"
    # branch this comment originally described has been REMOVED.
    # Confirmed via /debug-goalapi-stage-audit that this v2 fix was
    # otherwise working correctly (12 of 13 Austria teams resolved
    # correctly to gp=5-6) but Austria's league-wide max_gp still
    # stuck at 32 because of exactly ONE team (Blau-Weiß Linz) with
    # no "Current" row, only leftover last-season rows. That team's
    # stale fallback alone was enough to corrupt the whole league's
    # reported max — the failure mode this comment predicted, now
    # actually observed. A team missing a "Current" row entirely is
    # almost certainly no longer active in the league's current
    # season (relegated/promoted out) or GOAL API hasn't created
    # their entry yet — either way, stale data pretending to be
    # current is worse than no data, so that team is now skipped
    # entirely, same principle as the existing "skip teams with
    # gp==0" rule just below.
    rows_by_team = {}
    for row in rows:
        team_name = (row.get("team") or {}).get("name") or row.get("teamName")
        if not team_name:
            continue
        rows_by_team.setdefault(team_name, []).append(row)

    result = {}

    for team_name, team_rows in rows_by_team.items():
        current_rows = [r for r in team_rows if r.get("stageName") == "Current"]

        if not current_rows:
            # FIX (2026-09-17): no fallback to "most recently updated
            # stale row" anymore — confirmed real trigger: Blau-Weiß
            # Linz in Austria - Bundesliga, only had "Relegation
            # Group"/null-stage rows present (both leftover from last
            # season), while all 12 other teams in the same league
            # correctly had a "Current" row. A team with NO "Current"
            # row at all is most likely no longer active in this
            # league's current season (relegated/promoted out) or
            # GOAL API simply hasn't created their current-season
            # entry yet — either way, including their stale historical
            # row was silently corrupting the WHOLE LEAGUE's reported
            # max_gp with last-season data (Austria stayed stuck at
            # gp=32 because of exactly this one team, even though the
            # per-team dedup logic itself was working correctly for
            # the other 12). Skip this team entirely, same principle
            # as the existing "skip teams with gp==0" rule below.
            continue

        # Prefer "Current" stage; if somehow more than one, take
        # the most recently updated among just those.
        row = max(current_rows, key=lambda r: r.get("updatedAt") or "")

        gp = _to_int(row.get("overallLeaguePlayed"))
        gf = _to_int(row.get("overallLeagueGF"))
        ga = _to_int(row.get("overallLeagueGA"))

        # Skip teams with 0 games played — run_model() (in app.py)
        # divides by each team's gp when computing its home-advantage
        # ratio, which crashes with ZeroDivisionError for a team with
        # no matches yet. AnnaBet's standings never surfaced this
        # (its table only lists teams that have actually played), but
        # GOAL API's /standings can include newly-added or not-yet-
        # started teams. Confirmed real trigger: Algeria - Ligue 1,
        # 2026-09-11.
        if gp == 0:
            continue

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
# Fixtures — one day, all leagues, PAGINATED
# ------------------------------------------------------------

def fetch_fixtures_for_day(date_str):
    """
    date_str: 'YYYY-MM-DD'.

    Returns a list of fixtures:
        [{"id": ..., "league_id": ..., "league_name": ...,
          "home": ..., "away": ..., "kickoff": ...,
          "status": ..., "home_score": ..., "away_score": ...}, ...]

    FIX (2026-09-12): /fixtures/date/:date is a paginated list
    endpoint — GOAL API's docs confirm every list endpoint returns
    {"success", "data": [...], "pagination": {"total", "limit",
    "offset", "hasMore"}}. The previous version of this function
    made exactly ONE call and returned whatever page 1 contained
    (limit defaults to 50 if not specified), silently dropping every
    fixture past that on any day with more matches than one page —
    which, across 1,000+ leagues, is effectively every day. This now
    walks pages with offset until pagination.hasMore is false, via
    the shared _get_all_pages() helper.

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

    rows = _get_all_pages(f"/fixtures/date/{date_str}", params={"limit": FIXTURES_PAGE_LIMIT})

    fixtures = [
        {
            "id": row.get("id"),
            "league_id": row.get("leagueId"),
            "league_name": row.get("leagueName"),
            "home": row.get("homeTeamName"),
            "away": row.get("awayTeamName"),
            "kickoff": row.get("kickoffUtc"),
            "status": row.get("matchStatus"),
            "home_score": row.get("homeTeamScore"),
            "away_score": row.get("awayTeamScore"),
        }
        for row in rows
    ]

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

    Use combined_odds.get_combined_market_odds() instead for odds on
    the free tier — OddStorm primary, Oddsbook fallback. This function
    is kept as a stub in case the plan is upgraded later, but should
    not be called on the free tier.
    """
    return {"market_odds": None, "market_ou25": None}
