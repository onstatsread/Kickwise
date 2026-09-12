"""
GOAL API fetcher — production replacement for AnnaBet fixture/stat fetching.

Purpose
-------
1. Fetch league standings from GOAL API.
2. Convert GOAL API standings into the SAME structure expected by Kickwise.
3. Fetch ALL fixtures for a given date.
4. Handle pagination automatically.
5. Cache standings and fixtures.
6. Avoid scraping / Playwright / Cloudflare.
7. Keep GOAL API odds disabled on the free plan.

GOAL API
--------
Base:
    https://api.goal-api.com/v1

Authentication:
    Authorization: Bearer {GOAL_API_KEY}

Important
---------
GOAL API list responses are paginated. The daily fixture fetcher therefore
continues requesting pages until hasMore is false.

Environment variable required:
    GOAL_API_KEY
"""

import os
import time
import requests


# ============================================================
# CONFIGURATION
# ============================================================

GOAL_API_BASE = "https://api.goal-api.com/v1"
GOAL_API_KEY = os.environ.get("GOAL_API_KEY", "").strip()

# Keep the page reasonably large.
# If GOAL API enforces a smaller maximum, the API response will determine
# the actual number returned and pagination will continue automatically.
FIXTURE_PAGE_LIMIT = 100

# Cache settings
STATS_CACHE_TTL = 3600       # 1 hour
FIXTURES_CACHE_TTL = 300     # 5 minutes

# Request settings
REQUEST_TIMEOUT = 20
MAX_RETRIES = 3

# Small delay between paginated requests.
# This helps avoid hammering the API if a day contains many pages.
PAGE_DELAY = 0.15


# ============================================================
# SESSION
# ============================================================

_SESSION = requests.Session()

_SESSION.headers.update({
    "Accept": "application/json",
    "User-Agent": "Kickwise/1.0",
})

if GOAL_API_KEY:
    _SESSION.headers.update({
        "Authorization": f"Bearer {GOAL_API_KEY}"
    })


# ============================================================
# CACHES
# ============================================================

_STATS_CACHE = {}
_FIXTURES_CACHE = {}


# ============================================================
# BASIC HELPERS
# ============================================================

def _to_float(value, default=0.0):
    """
    Safely convert a value to float.
    """
    if value is None:
        return default

    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _to_int(value, default=0):
    """
    Safely convert a value to int.

    GOAL API standings values may arrive as strings.
    """
    if value is None:
        return default

    try:
        return int(value)
    except (TypeError, ValueError):
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return default


def _clean_name(value):
    """
    Normalize a team/league name enough for internal comparisons.
    Does NOT aggressively alter the actual returned name.
    """
    if value is None:
        return ""

    return " ".join(str(value).strip().split())


# ============================================================
# API REQUEST
# ============================================================

def _get(path, params=None, retries=MAX_RETRIES):
    """
    Authenticated GET request.

    Returns:
        Parsed JSON dictionary on success.
        None on failure.

    Retries:
        429
        502
        503
        504
        connection/timeouts
    """

    if not GOAL_API_KEY:
        print("GOAL API ERROR: GOAL_API_KEY is not configured.")
        return None

    url = f"{GOAL_API_BASE}{path}"

    for attempt in range(1, retries + 1):

        try:
            response = _SESSION.get(
                url,
                params=params,
                timeout=REQUEST_TIMEOUT,
            )

        except requests.RequestException as exc:
            print(
                f"GOAL API request exception "
                f"(attempt {attempt}/{retries}): "
                f"{url} -> {exc}"
            )

            if attempt < retries:
                time.sleep(min(2 ** (attempt - 1), 5))

            continue

        # ----------------------------------------------------
        # SUCCESS
        # ----------------------------------------------------

        if response.status_code == 200:

            try:
                payload = response.json()
            except ValueError as exc:
                print(
                    f"GOAL API JSON parse failed: "
                    f"{url} -> {exc}"
                )
                return None

            return payload

        # ----------------------------------------------------
        # RATE LIMIT
        # ----------------------------------------------------

        if response.status_code == 429:

            print(
                f"GOAL API 429 rate limit "
                f"(attempt {attempt}/{retries}): {url}"
            )

            if attempt < retries:
                # Increasing delay between attempts.
                time.sleep(min(3 * attempt, 10))

            continue

        # ----------------------------------------------------
        # TEMPORARY SERVER ERROR
        # ----------------------------------------------------

        if response.status_code in (502, 503, 504):

            print(
                f"GOAL API {response.status_code} "
                f"(attempt {attempt}/{retries}): {url}"
            )

            if attempt < retries:
                time.sleep(min(2 ** attempt, 8))

            continue

        # ----------------------------------------------------
        # OTHER ERROR
        # ----------------------------------------------------

        body = response.text[:500]

        print(
            f"GOAL API {response.status_code}: "
            f"{url} -> {body}"
        )

        return None

    return None


# ============================================================
# PAGINATION HELPERS
# ============================================================

def _extract_rows(payload):
    """
    Extract the data array from a GOAL API response.

    Expected:
        {
            "success": true,
            "data": [...]
        }
    """

    if not isinstance(payload, dict):
        return []

    rows = payload.get("data")

    if isinstance(rows, list):
        return rows

    return []


def _has_more(payload, current_count, offset):
    """
    Determine whether another page should be requested.

    GOAL API responses may expose:
        hasMore
        total
        limit
        offset

    We prioritize explicit hasMore when available.
    """

    if not isinstance(payload, dict):
        return False

    # Most reliable signal.
    if "hasMore" in payload:
        return bool(payload.get("hasMore"))

    # Some APIs put pagination inside a pagination object.
    pagination = payload.get("pagination")

    if isinstance(pagination, dict):

        if "hasMore" in pagination:
            return bool(pagination.get("hasMore"))

        total = _to_int(pagination.get("total"), 0)
        limit = _to_int(
            pagination.get("limit"),
            current_count,
        )
        page_offset = _to_int(
            pagination.get("offset"),
            offset,
        )

        if total > 0 and limit > 0:
            return page_offset + current_count < total

    # Top-level total/limit/offset.
    total = _to_int(payload.get("total"), 0)
    limit = _to_int(payload.get("limit"), current_count)
    page_offset = _to_int(payload.get("offset"), offset)

    if total > 0 and limit > 0:
        return page_offset + current_count < total

    # If there is no pagination metadata, a short page is normally
    # the final page.
    if current_count < FIXTURE_PAGE_LIMIT:
        return False

    return True


# ============================================================
# TEAM STATS
# ============================================================

def fetch_team_stats(league_id):
    """
    Fetch league standings.

    Returns the SAME shape expected by the existing Kickwise model:

        {
            "Team Name": {
                "gp": ...,
                "gf": ...,
                "ga": ...,
                "tot": ...,
                "hgf": ...,
                "hga": ...,
                "htot": ...,
                "agf": ...,
                "aga": ...,
                "atot": ...
            }
        }

    GOAL API:
        GET /standings/{league_id}

    The endpoint provides:
        overall
        home
        away

    statistics.
    """

    if not league_id:
        print("GOAL API standings: missing league_id.")
        return {}

    cache_key = str(league_id)

    cached = _STATS_CACHE.get(cache_key)

    if cached:
        cached_time, cached_data = cached

        if time.time() - cached_time < STATS_CACHE_TTL:
            return cached_data

    # --------------------------------------------------------
    # API REQUEST
    # --------------------------------------------------------

    data = _get(
        f"/standings/{league_id}"
    )

    if not data:
        return {}

    if data.get("success") is False:
        print(
            f"GOAL API standings unsuccessful "
            f"for league {league_id}: {data}"
        )
        return {}

    rows = _extract_rows(data)

    if not rows:
        print(
            f"GOAL API standings returned no rows "
            f"for league {league_id}"
        )
        return {}

    result = {}

    # --------------------------------------------------------
    # PARSE TEAMS
    # --------------------------------------------------------

    for row in rows:

        if not isinstance(row, dict):
            continue

        team = row.get("team")

        if isinstance(team, dict):
            team_name = team.get("name")
        else:
            team_name = row.get("teamName")

        team_name = _clean_name(team_name)

        if not team_name:
            continue

        # ----------------------------------------------------
        # OVERALL
        # ----------------------------------------------------

        gp = _to_int(
            row.get("overallLeaguePlayed")
        )

        gf = _to_int(
            row.get("overallLeagueGF")
        )

        ga = _to_int(
            row.get("overallLeagueGA")
        )

        # ----------------------------------------------------
        # IMPORTANT:
        # Skip teams that have played zero matches.
        #
        # Your run_model() expects usable GP and can otherwise
        # encounter division-by-zero situations.
        # ----------------------------------------------------

        if gp <= 0:
            continue

        # ----------------------------------------------------
        # HOME
        # ----------------------------------------------------

        hgp = _to_int(
            row.get("homeLeaguePlayed")
        )

        hgf_total = _to_int(
            row.get("homeLeagueGF")
        )

        hga_total = _to_int(
            row.get("homeLeagueGA")
        )

        # ----------------------------------------------------
        # AWAY
        # ----------------------------------------------------

        agp = _to_int(
            row.get("awayLeaguePlayed")
        )

        agf_total = _to_int(
            row.get("awayLeagueGF")
        )

        aga_total = _to_int(
            row.get("awayLeagueGA")
        )

        # ----------------------------------------------------
        # CALCULATED RATES
        # ----------------------------------------------------

        overall_gf = gf / gp
        overall_ga = ga / gp
        overall_total = (gf + ga) / gp

        home_gf = (
            hgf_total / hgp
            if hgp > 0
            else 0.0
        )

        home_ga = (
            hga_total / hgp
            if hgp > 0
            else 0.0
        )

        home_total = (
            (hgf_total + hga_total) / hgp
            if hgp > 0
            else 0.0
        )

        away_gf = (
            agf_total / agp
            if agp > 0
            else 0.0
        )

        away_ga = (
            aga_total / agp
            if agp > 0
            else 0.0
        )

        away_total = (
            (agf_total + aga_total) / agp
            if agp > 0
            else 0.0
        )

        result[team_name] = {
            "gp": gp,

            "gf": overall_gf,
            "ga": overall_ga,
            "tot": overall_total,

            "hgf": home_gf,
            "hga": home_ga,
            "htot": home_total,

            "agf": away_gf,
            "aga": away_ga,
            "atot": away_total,
        }

    # --------------------------------------------------------
    # CACHE
    # --------------------------------------------------------

    _STATS_CACHE[cache_key] = (
        time.time(),
        result,
    )

    print(
        f"GOAL API standings: "
        f"league={league_id}, teams={len(result)}"
    )

    return result


# ============================================================
# DAILY FIXTURES — PAGINATED
# ============================================================

def fetch_fixtures_for_day(date_str):
    """
    Fetch ALL GOAL API fixtures for a date.

    date_str:
        YYYY-MM-DD

    Returns:

        [
            {
                "id": ...,
                "league_id": ...,
                "league_name": ...,
                "home": ...,
                "away": ...,
                "kickoff": ...,
                "status": ...,
                "home_score": ...,
                "away_score": ...
            }
        ]

    IMPORTANT
    ---------
    GOAL API fixture lists are paginated.

    This function therefore keeps requesting pages until there are
    no more pages.

    It does NOT filter by GOALAPI_LEAGUE_IDS.

    Therefore this function represents the full fixture universe
    returned by GOAL API for that date.
    """

    if not date_str:
        return []

    cache_key = str(date_str)

    # --------------------------------------------------------
    # CACHE
    # --------------------------------------------------------

    cached = _FIXTURES_CACHE.get(cache_key)

    if cached:
        cached_time, cached_data = cached

        if time.time() - cached_time < FIXTURES_CACHE_TTL:
            return cached_data

    # --------------------------------------------------------
    # PAGINATION
    # --------------------------------------------------------

    all_rows = []

    offset = 0
    page_number = 1

    while True:

        params = {
            "limit": FIXTURE_PAGE_LIMIT,
            "offset": offset,
        }

        data = _get(
            f"/fixtures/date/{date_str}",
            params=params,
        )

        if not data:
            # If page 1 failed, return nothing.
            # If a later page failed, keep whatever was successfully
            # retrieved rather than destroying the whole day's data.
            if page_number == 1:
                return []

            print(
                f"GOAL API fixture pagination stopped at "
                f"page {page_number}"
            )
            break

        rows = _extract_rows(data)

        if not rows:
            break

        all_rows.extend(rows)

        current_count = len(rows)

        print(
            f"GOAL API fixtures: "
            f"date={date_str}, "
            f"page={page_number}, "
            f"received={current_count}, "
            f"total_so_far={len(all_rows)}, "
            f"offset={offset}"
        )

        # ----------------------------------------------------
        # STOP CONDITION
        # ----------------------------------------------------

        if not _has_more(
            data,
            current_count,
            offset,
        ):
            break

        # ----------------------------------------------------
        # NEXT PAGE
        # ----------------------------------------------------

        next_offset = offset + current_count

        # Safety protection against a broken API response
        # repeatedly returning the same page.
        if next_offset <= offset:
            print(
                "GOAL API pagination safety stop: "
                "offset did not advance."
            )
            break

        offset = next_offset
        page_number += 1

        time.sleep(PAGE_DELAY)

    # --------------------------------------------------------
    # CONVERT RAW ROWS
    # --------------------------------------------------------

    fixtures = []

    seen_ids = set()

    for row in all_rows:

        if not isinstance(row, dict):
            continue

        fixture_id = row.get("id")

        # ----------------------------------------------------
        # Duplicate protection
        # ----------------------------------------------------

        if fixture_id is not None:

            fixture_key = str(fixture_id)

            if fixture_key in seen_ids:
                continue

            seen_ids.add(fixture_key)

        # ----------------------------------------------------
        # Confirmed flat fields
        # ----------------------------------------------------

        home = _clean_name(
            row.get("homeTeamName")
        )

        away = _clean_name(
            row.get("awayTeamName")
        )

        league_name = _clean_name(
            row.get("leagueName")
        )

        league_id = row.get("leagueId")

        kickoff = row.get("kickoffUtc")

        status = row.get("matchStatus")

        home_score = row.get(
            "homeTeamScore"
        )

        away_score = row.get(
            "awayTeamScore"
        )

        # ----------------------------------------------------
        # Don't return malformed fixtures.
        #
        # A fixture without both teams isn't useful to the
        # Kickwise prediction engine.
        # ----------------------------------------------------

        if not home or not away:
            print(
                "GOAL API fixture skipped: "
                f"missing team name, row={row}"
            )
            continue

        fixtures.append({
            "id": fixture_id,
            "league_id": league_id,
            "league_name": league_name,
            "home": home,
            "away": away,
            "kickoff": kickoff,
            "status": status,
            "home_score": home_score,
            "away_score": away_score,
        })

    # --------------------------------------------------------
    # CACHE COMPLETE DAY
    # --------------------------------------------------------

    _FIXTURES_CACHE[cache_key] = (
        time.time(),
        fixtures,
    )

    print(
        f"GOAL API fixtures COMPLETE: "
        f"date={date_str}, "
        f"raw_rows={len(all_rows)}, "
        f"usable_fixtures={len(fixtures)}, "
        f"pages={page_number}"
    )

    return fixtures


# ============================================================
# ODDS
# ============================================================

def fetch_market_odds(fixture_id):
    """
    GOAL API fixture odds.

    CURRENT FREE PLAN:
        Disabled because /fixtures/{id}/odds requires
        paid-plan access.

    Keep this function so the rest of Kickwise can continue
    importing it safely if the API plan changes later.
    """

    return {
        "market_odds": None,
        "market_ou25": None,
    }


# ============================================================
# OPTIONAL CACHE MANAGEMENT
# ============================================================

def clear_goalapi_cache():
    """
    Clear all in-memory GOAL API caches.

    Useful for debugging or testing.
    """

    _STATS_CACHE.clear()
    _FIXTURES_CACHE.clear()

    print("GOAL API caches cleared.")


def clear_fixture_cache(date_str=None):
    """
    Clear fixture cache.

    If date_str is supplied, only that date is cleared.
    Otherwise all fixture cache is cleared.
    """

    if date_str is None:
        _FIXTURES_CACHE.clear()
        print("GOAL API fixture cache cleared.")
        return

    _FIXTURES_CACHE.pop(
        str(date_str),
        None,
    )

    print(
        f"GOAL API fixture cache cleared: "
        f"{date_str}"
    )


# ============================================================
# DEBUG / TEST
# ============================================================

def goalapi_status():
    """
    Small diagnostic helper.

    Returns basic configuration information without exposing
    the API key.
    """

    return {
        "base_url": GOAL_API_BASE,
        "api_key_configured": bool(GOAL_API_KEY),
        "stats_cache_entries": len(_STATS_CACHE),
        "fixture_cache_entries": len(_FIXTURES_CACHE),
        "fixture_page_limit": FIXTURE_PAGE_LIMIT,
    }
