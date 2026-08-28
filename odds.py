"""
Kickwise — Odds Integration Module
-----------------------------------

The Odds API v4 integration.

Provides:

GET /odds/{sport_key}
GET /odds/{sport_key}/{home_team}/{away_team}

Also provides internal helpers:

get_odds_for_card()
get_ou25_for_card()
prefetch_odds_for_leagues()

Environment variable required on Render:

ODDS_API_KEY=your_api_key
"""

import os
import time
import httpx

from fastapi import APIRouter, HTTPException
from difflib import SequenceMatcher


router = APIRouter(prefix="/odds", tags=["odds"])


# ============================================================
# CONFIG
# ============================================================

ODDS_API_KEY = os.getenv("ODDS_API_KEY", "").strip()

ODDS_API_BASE = "https://api.the-odds-api.com/v4"

# Cache league odds for 4 hours
CACHE_TTL_SECONDS = 4 * 60 * 60

_odds_cache: dict[str, tuple[float, list]] = {}


# ============================================================
# TEAM MATCHING
# ============================================================

def _normalise_team(name: str) -> str:
    """
    Make team names easier to compare.
    """

    if not name:
        return ""

    name = name.lower().strip()

    replacements = {
        "&": "and",
        ".": "",
        ",": "",
        "-": " ",
        "_": " ",
        "'": "",
    }

    for old, new in replacements.items():
        name = name.replace(old, new)

    # Common football abbreviations
    replacements = {
        " utd ": " united ",
        " fc ": " ",
        " afc ": " ",
        " cf ": " ",
        " sc ": " ",
        " fk ": " ",
        " ak ": " akademia ",
    }

    # Add spaces so replacements work correctly
    name = f" {name} "

    for old, new in replacements.items():
        name = name.replace(old, new)

    return " ".join(name.split())


def _similar(a: str, b: str) -> float:
    """
    Compare two team names.

    Returns value from 0 to 1.
    """

    a = _normalise_team(a)
    b = _normalise_team(b)

    if not a or not b:
        return 0.0

    if a == b:
        return 1.0

    # Direct containment
    if a in b or b in a:
        shorter = min(len(a), len(b))
        longer = max(len(a), len(b))

        if shorter / longer >= 0.45:
            return 0.92

    base = SequenceMatcher(None, a, b).ratio()

    a_words = a.split()
    b_words = b.split()

    # Word-based comparison
    common = set(a_words) & set(b_words)

    if common:
        coverage_a = len(common) / max(len(a_words), 1)
        coverage_b = len(common) / max(len(b_words), 1)

        word_score = (coverage_a + coverage_b) / 2

        base = max(base, word_score)

    return base


def _find_best_match(
    raw_matches: list,
    home_team: str,
    away_team: str,
):
    """
    Find the best home/away fixture.
    """

    best_match = None
    best_score = 0.0

    for match in raw_matches:

        api_home = match.get("home_team", "")
        api_away = match.get("away_team", "")

        home_score = _similar(api_home, home_team)
        away_score = _similar(api_away, away_team)

        total_score = home_score + away_score

        if total_score > best_score:
            best_score = total_score
            best_match = match

    return best_match, best_score


# ============================================================
# EXTRACT 1X2
# ============================================================

def _extract_hda(match_data: dict) -> dict:

    home_team = match_data.get("home_team", "")
    away_team = match_data.get("away_team", "")

    home_odds = []
    draw_odds = []
    away_odds = []

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
                    home_odds.append(float(price))

                elif name == away_team:
                    away_odds.append(float(price))

                elif name.lower() == "draw":
                    draw_odds.append(float(price))

    def average(values):

        if not values:
            return None

        return round(sum(values) / len(values), 2)

    return {
        "home_team": home_team,
        "away_team": away_team,
        "commence_time": match_data.get("commence_time"),

        "home_odds": average(home_odds),
        "draw_odds": average(draw_odds),
        "away_odds": average(away_odds),

        "num_bookmakers": len(
            match_data.get("bookmakers", [])
        ),
    }


# ============================================================
# EXTRACT OVER/UNDER 2.5
# ============================================================

def _extract_ou25(match_data: dict) -> dict:

    over_odds = []
    under_odds = []

    for bookmaker in match_data.get("bookmakers", []):

        for market in bookmaker.get("markets", []):

            if market.get("key") != "totals":
                continue

            for outcome in market.get("outcomes", []):

                point = outcome.get("point")
                price = outcome.get("price")

                if point != 2.5 or price is None:
                    continue

                name = (
                    outcome.get("name") or ""
                ).lower()

                if name == "over":
                    over_odds.append(float(price))

                elif name == "under":
                    under_odds.append(float(price))

    def average(values):

        if not values:
            return None

        return round(sum(values) / len(values), 2)

    over = average(over_odds)
    under = average(under_odds)

    def probability(odd):

        if not odd or odd <= 1:
            return None

        return round((1 / odd) * 100, 1)

    return {
        "over_odds": over,
        "under_odds": under,
        "over_pct": probability(over),
        "under_pct": probability(under),
    }


# ============================================================
# FETCH ODDS
# ============================================================

async def _fetch_odds(
    sport_key: str,
    force_refresh: bool = False,
) -> list:

    sport_key = sport_key.strip()

    if not sport_key:

        raise HTTPException(
            status_code=400,
            detail="sport_key is empty.",
        )

    if not ODDS_API_KEY:

        raise HTTPException(
            status_code=500,
            detail=(
                "ODDS_API_KEY is missing from Render environment."
            ),
        )

    # --------------------------------------------------------
    # CACHE
    # --------------------------------------------------------

    cached = _odds_cache.get(sport_key)

    if cached and not force_refresh:

        fetched_at, cached_data = cached

        if time.time() - fetched_at < CACHE_TTL_SECONDS:

            print(
                f"[ODDS] CACHE HIT: {sport_key} "
                f"({len(cached_data)} matches)"
            )

            return cached_data

    # --------------------------------------------------------
    # API REQUEST
    # --------------------------------------------------------

    url = (
        f"{ODDS_API_BASE}/sports/"
        f"{sport_key}/odds/"
    )

    params = {
        "apiKey": ODDS_API_KEY,

        # Start with one region to reduce quota usage
        "regions": "eu",

        # Main market required by Kickwise
        "markets": "h2h",

        "oddsFormat": "decimal",
    }

    print(
        f"[ODDS] REQUEST: sport={sport_key}"
    )

    try:

        async with httpx.AsyncClient(
            timeout=20.0
        ) as client:

            response = await client.get(
                url,
                params=params,
            )

    except Exception as exc:

        print(
            f"[ODDS] CONNECTION ERROR: {exc}"
        )

        if cached:
            return cached[1]

        raise HTTPException(
            status_code=502,
            detail=f"Could not connect to Odds API: {exc}",
        )

    # --------------------------------------------------------
    # QUOTA INFORMATION
    # --------------------------------------------------------

    remaining = response.headers.get(
        "x-requests-remaining"
    )

    used = response.headers.get(
        "x-requests-used"
    )

    last_cost = response.headers.get(
        "x-requests-last"
    )

    print(
        "[ODDS] QUOTA: "
        f"remaining={remaining}, "
        f"used={used}, "
        f"last_cost={last_cost}"
    )

    # --------------------------------------------------------
    # ERROR
    # --------------------------------------------------------

    if response.status_code != 200:

        print(
            "[ODDS] API ERROR: "
            f"{response.status_code} "
            f"{response.text}"
        )

        if cached:
            print(
                f"[ODDS] Using stale cache for {sport_key}"
            )

            return cached[1]

        raise HTTPException(
            status_code=response.status_code,
            detail=(
                f"Odds API error "
                f"{response.status_code}: "
                f"{response.text}"
            ),
        )

    # --------------------------------------------------------
    # JSON
    # --------------------------------------------------------

    try:

        data = response.json()

    except Exception:

        raise HTTPException(
            status_code=502,
            detail="Odds API returned invalid JSON.",
        )

    if not isinstance(data, list):

        raise HTTPException(
            status_code=502,
            detail=(
                "Odds API returned unexpected data: "
                f"{data}"
            ),
        )

    print(
        f"[ODDS] SUCCESS: {sport_key} "
        f"returned {len(data)} matches"
    )

    # --------------------------------------------------------
    # CACHE
    # --------------------------------------------------------

    _odds_cache[sport_key] = (
        time.time(),
        data,
    )

    return data


# ============================================================
# PREFETCH
# ============================================================

async def prefetch_odds_for_leagues(
    sport_keys: list[str],
) -> None:

    """
    Warm the cache before the prediction loop.
    """

    unique_keys = list(
        dict.fromkeys(
            k.strip()
            for k in sport_keys
            if k and k.strip()
        )
    )

    print(
        f"[ODDS] PREFETCHING {len(unique_keys)} leagues"
    )

    for sport_key in unique_keys:

        try:

            data = await _fetch_odds(
                sport_key
            )

            print(
                f"[ODDS] PREFETCH OK: "
                f"{sport_key} -> "
                f"{len(data)} matches"
            )

        except Exception as exc:

            print(
                f"[ODDS] PREFETCH FAILED: "
                f"{sport_key} -> {exc}"
            )

            continue


# ============================================================
# API: LEAGUE ODDS
# ============================================================

@router.get("/{sport_key}")
async def get_league_odds(
    sport_key: str,
):

    raw_matches = await _fetch_odds(
        sport_key
    )

    return [
        _extract_hda(match)
        for match in raw_matches
    ]


# ============================================================
# API: SINGLE MATCH
# ============================================================

@router.get(
    "/{sport_key}/{home_team}/{away_team}"
)
async def get_match_odds(
    sport_key: str,
    home_team: str,
    away_team: str,
):

    raw_matches = await _fetch_odds(
        sport_key
    )

    best_match, best_score = _find_best_match(
        raw_matches,
        home_team,
        away_team,
    )

    print(
        f"[ODDS] MATCH SEARCH: "
        f"{home_team} vs {away_team}"
    )

    print(
        f"[ODDS] BEST SCORE: {best_score:.3f}"
    )

    if best_match:

        print(
            "[ODDS] FOUND: "
            f"{best_match.get('home_team')} "
            f"vs "
            f"{best_match.get('away_team')}"
        )

    # Require reasonably strong match
    if not best_match or best_score < 1.20:

        raise HTTPException(
            status_code=404,
            detail=(
                f"No close Odds API match found "
                f"for {home_team} vs {away_team} "
                f"in {sport_key}. "
                f"Best score={best_score:.3f}"
            ),
        )

    return _extract_hda(best_match)


# ============================================================
# INTERNAL: ODDS FOR CARD
# ============================================================

async def get_odds_for_card(
    sport_key: str,
    home_team: str,
    away_team: str,
) -> dict | None:

    try:

        raw_matches = await _fetch_odds(
            sport_key
        )

    except Exception as exc:

        print(
            f"[ODDS] CARD FETCH FAILED: {exc}"
        )

        return None

    best_match, best_score = _find_best_match(
        raw_matches,
        home_team,
        away_team,
    )

    print(
        f"[ODDS] CARD: "
        f"{home_team} vs {away_team} "
        f"score={best_score:.3f}"
    )

    if not best_match or best_score < 1.20:

        print(
            f"[ODDS] NO MATCH: "
            f"{home_team} vs {away_team}"
        )

        return None

    odds = _extract_hda(
        best_match
    )

    if not odds.get("home_odds"):

        print(
            f"[ODDS] NO H2H ODDS: "
            f"{home_team} vs {away_team}"
        )

        return None

    def pct(odd):

        if not odd:
            return None

        return round(
            (1 / odd) * 100,
            1,
        )

    return {
        "home_odds": odds["home_odds"],
        "draw_odds": odds["draw_odds"],
        "away_odds": odds["away_odds"],

        "home_pct": pct(
            odds["home_odds"]
        ),

        "draw_pct": pct(
            odds["draw_odds"]
        ),

        "away_pct": pct(
            odds["away_odds"]
        ),

        "num_bookmakers": odds[
            "num_bookmakers"
        ],
    }


# ============================================================
# INTERNAL: OVER/UNDER 2.5
# ============================================================

async def get_ou25_for_card(
    sport_key: str,
    home_team: str,
    away_team: str,
) -> dict | None:

    # IMPORTANT:
    #
    # The normal cache currently requests only h2h.
    # Therefore this function intentionally does NOT
    # pretend totals are available.
    #
    # We will add totals in a second request only when
    # you actually need O/U 2.5.

    return None


# ============================================================
# MERGE WITH POISSON
# ============================================================

def merge_with_prediction(
    prediction: dict,
    odds: dict,
) -> dict:

    combined = dict(
        prediction
    )

    combined["market_odds"] = {
        "home": odds.get(
            "home_odds"
        ),

        "draw": odds.get(
            "draw_odds"
        ),

        "away": odds.get(
            "away_odds"
        ),

        "num_bookmakers": odds.get(
            "num_bookmakers"
        ),
    }

    for (
        side,
        odds_key,
        prob_key,
    ) in [

        (
            "home",
            "home_odds",
            "home_win_prob",
        ),

        (
            "draw",
            "draw_odds",
            "draw_prob",
        ),

        (
            "away",
            "away_odds",
            "away_win_prob",
        ),

    ]:

        market_price = odds.get(
            odds_key
        )

        model_prob = prediction.get(
            prob_key
        )

        if (
            market_price
            and model_prob is not None
            and market_price > 1
        ):

            implied_market_prob = (
                1 / market_price
            )

            edge = (
                model_prob
                - implied_market_prob
            )

            combined.setdefault(
                "value_edges",
                {},
            )[side] = round(
                edge * 100,
                1,
            )

    return combined
