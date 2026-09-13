"""
Combined odds fetcher — tries OddStorm first (no Playwright needed,
includes O/U 2.5), falls back to Oddsbook (Playwright-based) for
leagues or matches OddStorm doesn't have priced.

Both underlying fetchers return the same shape:
    {
        "market_odds": {"home_odds":..., "draw_odds":..., "away_odds":...},
        "market_ou25": {"over_odds":..., "under_odds":...}
    }
so this wrapper is a straightforward drop-in for either.

CLEANUP (2026-09-13): removed the league_url call added 2026-09-12.
CONFIRMED via /debug-oddstorm-match that OddStorm's
/odds/league/{id}-{slug} URLs don't scope content server-side — they
return the same full generic listing regardless of the slug given.
oddstorm_odds.get_market_odds() no longer accepts or needs a
league_url; it always searches the one full listing internally.

has_oddstorm_coverage() is still used as a first-pass filter — a
league confirmed absent from OddStorm's overall coverage skips
straight to Oddsbook rather than wasting a search over the full
listing for something that was never going to be there.
"""

from datetime import date

from oddstorm_leagues import has_oddstorm_coverage
from oddstorm_odds import get_market_odds as get_oddstorm_market_odds
from oddsbook_odds import get_oddsbook_market_odds


def get_combined_market_odds(kickwise_league_name, home, away, target_date=None):
    """
    kickwise_league_name: the "Country - League" string used as the
        key in daily_predictions.py's LEAGUE_CODES / GOALAPI_LEAGUE_IDS
        (e.g. "Algeria - Ligue 1") — used only to decide whether
        OddStorm covers this league at all before searching.
    home, away: team names as they appear in your fixtures data.
    target_date: a date to match fixtures/odds against. Defaults to
        date.today() if not given, and is passed to BOTH the OddStorm
        and Oddsbook branches.

    Returns the standard {"market_odds": ..., "market_ou25": ...}
    shape, with a "source" key added so you can see which fetcher
    actually supplied the data (useful for logging/debugging).

    A null result from both sources for a specific match is often a
    genuine data-coverage gap (the fixture isn't priced yet by either
    source's tracked bookmakers) rather than a bug — confirmed via
    /debug-oddstorm-match and a matching Oddsbook check on 2026-09-13.
    """
    resolved_date = target_date or date.today()

    if has_oddstorm_coverage(kickwise_league_name):
        try:
            result = get_oddstorm_market_odds(home, away, target_date=resolved_date)
            if result.get("market_odds") or result.get("market_ou25"):
                result["source"] = "oddstorm"
                return result
        except Exception as e:
            print(f"OddStorm odds failed for {kickwise_league_name} "
                  f"({home} vs {away}): {e} — falling back to Oddsbook")

    # Either OddStorm doesn't cover this league, or it returned
    # nothing for this specific match on this date (e.g. not yet
    # priced) — try Oddsbook as the fallback.
    try:
        result = get_oddsbook_market_odds(home, away, resolved_date)
        result["source"] = "oddsbook"
        return result
    except Exception as e:
        print(f"Oddsbook odds also failed for {kickwise_league_name} "
              f"({home} vs {away}): {e}")
        return {"market_odds": None, "market_ou25": None, "source": None}
