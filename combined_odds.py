"""
Combined odds fetcher — tries sources in order of coverage breadth and
cost:

    1. Odds-API.io (free tier, 100 req/hour / 500/day — CONFIRMED real
       schema against live Render logs, not a guess; promoted to
       PRIMARY 2026-09-16 after confirming the account works again.
       Aggressively cached — see odds_api_io.py — so actual API usage
       should stay well under quota even as the primary tier.)
    2. OddStorm    (free, no quota limit, but coverage varies by match
       — demoted from primary to first fallback 2026-09-16)
    3. Oddsbook    (free, Playwright-based, broader coverage but
       slower; note market_ou25 is currently ALWAYS None here — see
       oddsbook_odds.py, the O/U fetcher was never actually built)
    4. The Odds API (paid quota — only covers a narrow set of major
       leagues, resolved dynamically via odds_api_leagues.py)
    5. API-Football (very tight free quota — 100 req/day total — used
       as the last resort only, with its own daily budget cap and
       429 circuit breaker; see api_football.py)

All five fetchers return (or are normalized to) the same shape:
    {
        "market_odds": {"home_odds":..., "draw_odds":..., "away_odds":...},
        "market_ou25": {"over_odds":..., "under_odds":...}
    }
so this wrapper is a straightforward drop-in for any of them.

FIX (2026-09-16): the previous version returned as soon as ANY tier
produced EITHER field ("if result.get('market_odds') or
result.get('market_ou25')"), which meant a match resolved by OddStorm
with 1X2 odds but no O/U 2.5 line yet (very common — bookmakers often
post the win/draw/loss line well before the O/U line) would return
immediately with market_ou25 permanently None, never even trying
Oddsbook for that match. Confirmed real-world trigger (2026-09-16):
Spain - LaLiga, Rayo Vallecano vs Espanyol — OddStorm supplied HDA,
market_ou25 came back empty on the live blog post even though
Oddsbook might well have had it, because the old logic never asked.

This version tracks market_odds and market_ou25 SEPARATELY and keeps
trying subsequent tiers until BOTH are filled (or every tier is
exhausted) — so a match can end up with HDA from one source and O/U
from a different one. "source" reports whichever tier supplied
market_odds (kept for backward compatibility with any caller/logging
that reads it); "ou25_source" reports whichever tier supplied
market_ou25, in case they differ.

Earlier fix (2026-09-13): added The Odds API and API-Football as
tiers. Neither of these supplies market_ou25 at all (The Odds API's
O/U support is stubbed out in odds.py's get_ou25_for_card();
API-Football's get_fallback_odds() only returns 1X2) — they can only
ever fill the market_odds side of a still-incomplete result.

This function is ASYNC (Odds-API.io, The Odds API, and API-Football
all use httpx.AsyncClient). Both call sites in app.py (/predict-v2,
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

from odds_api_io import get_odds_api_io_fallback, get_ou25_api_io_fallback
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

    Returns:
        {
            "market_odds": {...} or None,
            "market_ou25": {...} or None,
            "source": "odds_api_io" / "oddstorm" / "oddsbook" /
                       "odds_api" / "api_football" / None — whichever
                       tier supplied market_odds,
            "ou25_source": same set of values, or None — whichever
                       tier supplied market_ou25 (may differ from
                       "source" since fields are merged across tiers;
                       see FIX note above),
        }

    Keeps trying tiers, in order, until BOTH market_odds and
    market_ou25 are filled or every tier has been tried — merging in
    whichever field each tier can supply rather than stopping at the
    first tier with partial data.

    Both fields still None after every tier is almost always a
    genuine data-coverage gap (the fixture isn't priced yet anywhere)
    rather than a bug.
    """
    resolved_date = target_date or date.today()
    date_str = resolved_date.isoformat()

    market_odds = None
    market_ou25 = None
    source = None
    ou25_source = None

    def _still_incomplete():
        return market_odds is None or market_ou25 is None

    # ---- Tier 1: Odds-API.io (PRIMARY as of 2026-09-16) ----
    try:
        io_odds = await get_odds_api_io_fallback(home, away)
        if market_odds is None and io_odds:
            market_odds = io_odds
            source = "odds_api_io"
    except Exception as e:
        print(f"Odds-API.io HDA failed for {kickwise_league_name} "
              f"({home} vs {away}): {e} — trying next source")

    if market_ou25 is None:
        try:
            io_ou25 = await get_ou25_api_io_fallback(home, away)
            if io_ou25:
                market_ou25 = io_ou25
                ou25_source = "odds_api_io"
        except Exception as e:
            print(f"Odds-API.io O/U 2.5 failed for {kickwise_league_name} "
                  f"({home} vs {away}): {e} — trying next source")

    # ---- Tier 2: OddStorm ----
    if _still_incomplete() and has_oddstorm_coverage(kickwise_league_name):
        try:
            result = get_oddstorm_market_odds(home, away, target_date=resolved_date)
            if market_odds is None and result.get("market_odds"):
                market_odds = result["market_odds"]
                source = "oddstorm"
            if market_ou25 is None and result.get("market_ou25"):
                market_ou25 = result["market_ou25"]
                ou25_source = "oddstorm"
        except Exception as e:
            print(f"OddStorm odds failed for {kickwise_league_name} "
                  f"({home} vs {away}): {e} — trying next source")

    # ---- Tier 3: Oddsbook ----
    # FIX (2026-09-13): oddsbook_odds.py uses Playwright's SYNC API
    # internally. Now that this whole function is async and runs
    # inside FastAPI's asyncio event loop, calling that sync function
    # directly raises "Playwright Sync API inside the asyncio loop"
    # — confirmed via Render logs. Running it in a separate thread via
    # asyncio.to_thread() sidesteps the conflict: that thread has no
    # running asyncio loop of its own, so Playwright's sync API works
    # exactly as it did before this function became async.
    if _still_incomplete():
        try:
            result = await asyncio.to_thread(get_oddsbook_market_odds, home, away, resolved_date)
            if market_odds is None and result.get("market_odds"):
                market_odds = result["market_odds"]
                source = "oddsbook"
            if market_ou25 is None and result.get("market_ou25"):
                market_ou25 = result["market_ou25"]
                ou25_source = "oddsbook"
        except Exception as e:
            print(f"Oddsbook odds failed for {kickwise_league_name} "
                  f"({home} vs {away}): {e} — trying next source")

    # ---- Tier 4: The Odds API ----
    # Narrow coverage (mostly major leagues) — get_sport_key() returns
    # None for anything it doesn't confidently recognize, in which
    # case we skip straight to API-Football without spending a call.
    # Can only ever fill market_odds — this tier has no O/U 2.5 data.
    if market_odds is None:
        try:
            sport_key = await get_sport_key(kickwise_league_name)
            if sport_key:
                odds_api_result = await get_odds_for_card(sport_key, home, away)
                if odds_api_result:
                    market_odds = {
                        "home_odds": odds_api_result.get("home_odds"),
                        "draw_odds": odds_api_result.get("draw_odds"),
                        "away_odds": odds_api_result.get("away_odds"),
                        "home_pct": odds_api_result.get("home_pct"),
                        "draw_pct": odds_api_result.get("draw_pct"),
                        "away_pct": odds_api_result.get("away_pct"),
                    }
                    source = "odds_api"
        except Exception as e:
            print(f"The Odds API failed for {kickwise_league_name} "
                  f"({home} vs {away}): {e} — trying next source")

    # ---- Tier 5: API-Football (last resort — tight quota) ----
    # Also can only ever fill market_odds — no O/U 2.5 data here either.
    if market_odds is None:
        try:
            api_football_result = await get_api_football_odds(home, away, date_str)
            if api_football_result:
                market_odds = {
                    "home_odds": api_football_result.get("home_odds"),
                    "draw_odds": api_football_result.get("draw_odds"),
                    "away_odds": api_football_result.get("away_odds"),
                    "home_pct": api_football_result.get("home_pct"),
                    "draw_pct": api_football_result.get("draw_pct"),
                    "away_pct": api_football_result.get("away_pct"),
                }
                source = "api_football"
        except Exception as e:
            print(f"API-Football also failed for {kickwise_league_name} "
                  f"({home} vs {away}): {e}")

    return {
        "market_odds": market_odds,
        "market_ou25": market_ou25,
        "source": source,
        "ou25_source": ou25_source,
    }
