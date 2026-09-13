"""
Combined odds fetcher — tries sources in order of coverage breadth and
cost, stopping at the first one that returns anything usable:

    1. OddStorm   (free, no quota limit, but coverage varies by match)
    2. Oddsbook   (free, Playwright-based, broader coverage but slower)
    3. The Odds API (paid quota — only covers a narrow set of major
       leagues, resolved dynamically via odds_api_leagues.py)
    4. API-Football (very tight free quota — 100 req/day total — used
       as the last resort only, with its own daily budget cap and
       429 circuit breaker; see api_football.py)

All four fetchers return (or are normalized to) the same shape:
    {
        "market_odds": {"home_odds":..., "draw_odds":..., "away_odds":...},
        "market_ou25": {"over_odds":..., "under_odds":...}
    }
so this wrapper is a straightforward drop-in for any of them.

FIX (2026-09-13): added The Odds API and API-Football as tiers 3 and 4.
Neither of these currently supplies market_ou25 (The Odds API's O/U
support is stubbed out in odds.py's get_ou25_for_card(); API-Football's
get_fallback_odds() only returns 1X2) — so a match resolved via either
of these tiers will have market_odds populated but market_ou25 will
stay None. That's an accepted tradeoff: getting SOME market odds
beats getting none, and O/U value-signal logic already handles a
None market_ou25 gracefully (see app.py's predict_v2/predict_combined_test,
which only compute ou25_value_pct when both model and market ou25
exist).

This function is now ASYNC (The Odds API and API-Football both use
httpx.AsyncClient). Both call sites in app.py (/predict-v2,
/predict-combined-test) are already `async def`, so callers just need
to add `await`.

CLEANUP (2026-09-13, earlier): removed the league_url call added
2026-09-12. CONFIRMED via /debug-oddstorm-match that OddStorm's
/odds/league/{id}-{slug} URLs don't scope content server-side — they
return the same full generic listing regardless of the slug given.
oddstorm_odds.get_market_odds() no longer accepts or needs a
league_url; it always searches the one full listing internally.
"""

from datetime import date
import asyncio

from oddstorm_leagues import has_oddstorm_coverage
from oddstorm_odds import get_market_odds as get_oddstorm_market_odds
from oddsbook_odds import get_oddsbook_market_odds
from odds_api_leagues import get_sport_key
from odds import get_odds_for_card
from api_football import get_fallback_odds as get_api_football_odds


async def get_combined_market_odds(kickwise_league_name, home, away, target_date=None):
    """
    kickwise_league_name: the "Country - League" string used as the
        key in daily_predictions.py's LEAGUE_CODES / GOALAPI_LEAGUE_IDS
        (e.g. "Algeria - Ligue 1") — used to decide OddStorm coverage
        AND to resolve a The Odds API sport_key.
    home, away: team names as they appear in your fixtures data.
    target_date: a date to match fixtures/odds against. Defaults to
        date.today() if not given.

    Returns the standard {"market_odds": ..., "market_ou25": ...}
    shape, with a "source" key added so you can see which fetcher
    actually supplied the data (useful for logging/debugging):
    "oddstorm", "oddsbook", "odds_api", "api_football", or None if
    every tier came up empty.

    A null result across ALL FOUR sources for a specific match is
    almost always a genuine data-coverage gap (the fixture isn't
    priced yet anywhere) rather than a bug.
    """
    resolved_date = target_date or date.today()
    date_str = resolved_date.isoformat()

    # ---- Tier 1: OddStorm ----
    if has_oddstorm_coverage(kickwise_league_name):
        try:
            result = get_oddstorm_market_odds(home, away, target_date=resolved_date)
            if result.get("market_odds") or result.get("market_ou25"):
                result["source"] = "oddstorm"
                return result
        except Exception as e:
            print(f"OddStorm odds failed for {kickwise_league_name} "
                  f"({home} vs {away}): {e} — trying next source")

    # ---- Tier 2: Oddsbook ----
    # FIX (2026-09-13): oddsbook_odds.py uses Playwright's SYNC API
    # internally. Now that this whole function is async and runs
    # inside FastAPI's asyncio event loop, calling that sync function
    # directly raises "Playwright Sync API inside the asyncio loop"
    # — confirmed via Render logs. Running it in a separate thread via
    # asyncio.to_thread() sidesteps the conflict: that thread has no
    # running asyncio loop of its own, so Playwright's sync API works
    # exactly as it did before this function became async.
    try:
        result = await asyncio.to_thread(get_oddsbook_market_odds, home, away, resolved_date)
        if result.get("market_odds") or result.get("market_ou25"):
            result["source"] = "oddsbook"
            return result
    except Exception as e:
        print(f"Oddsbook odds failed for {kickwise_league_name} "
              f"({home} vs {away}): {e} — trying next source")

    # ---- Tier 3: The Odds API ----
    # Narrow coverage (mostly major leagues) — get_sport_key() returns
    # None for anything it doesn't confidently recognize, in which
    # case we skip straight to API-Football without spending a call.
    try:
        sport_key = await get_sport_key(kickwise_league_name)
        if sport_key:
            odds_api_result = await get_odds_for_card(sport_key, home, away)
            if odds_api_result:
                return {
                    "market_odds": {
                        "home_odds": odds_api_result.get("home_odds"),
                        "draw_odds": odds_api_result.get("draw_odds"),
                        "away_odds": odds_api_result.get("away_odds"),
                        "home_pct": odds_api_result.get("home_pct"),
                        "draw_pct": odds_api_result.get("draw_pct"),
                        "away_pct": odds_api_result.get("away_pct"),
                    },
                    # The Odds API's O/U 2.5 support (get_ou25_for_card)
                    # is currently a stub that always returns None — see
                    # odds.py. No O/U data available via this tier yet.
                    "market_ou25": None,
                    "source": "odds_api",
                }
    except Exception as e:
        print(f"The Odds API failed for {kickwise_league_name} "
              f"({home} vs {away}): {e} — trying next source")

    # ---- Tier 4: API-Football (last resort — tight quota) ----
    try:
        api_football_result = await get_api_football_odds(home, away, date_str)
        if api_football_result:
            return {
                "market_odds": {
                    "home_odds": api_football_result.get("home_odds"),
                    "draw_odds": api_football_result.get("draw_odds"),
                    "away_odds": api_football_result.get("away_odds"),
                    "home_pct": api_football_result.get("home_pct"),
                    "draw_pct": api_football_result.get("draw_pct"),
                    "away_pct": api_football_result.get("away_pct"),
                },
                # API-Football's get_fallback_odds() only fetches the
                # "Match Winner" (1X2) market to conserve its very
                # tight quota — no O/U 2.5 data via this tier.
                "market_ou25": None,
                "source": "api_football",
            }
    except Exception as e:
        print(f"API-Football also failed for {kickwise_league_name} "
              f"({home} vs {away}): {e}")

    return {"market_odds": None, "market_ou25": None, "source": None}
