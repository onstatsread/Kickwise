"""
Combined odds fetcher — tries OddStorm first (no Playwright needed,
includes O/U 2.5), falls back to Oddsbook (Playwright-based) for the
16 leagues confirmed absent from OddStorm's data (see
oddstorm_leagues.py's docstring for the full list and why).

Both underlying fetchers return the same shape:
    {
        "market_odds": {"home_odds":..., "draw_odds":..., "away_odds":...},
        "market_ou25": {"over_odds":..., "under_odds":...}
    }
so this wrapper is a straightforward drop-in for either.

FIX (2026-09-12, part 1): now actually calls oddstorm_leagues.
get_oddstorm_league_url() and passes the result through to
get_oddstorm_market_odds() as league_url. Previously
has_oddstorm_coverage() was used to DECIDE whether to try OddStorm,
but the matching league URL was never fetched or passed anywhere —
oddstorm_odds.py's fetcher only ever scraped the generic /odds/
homepage, which doesn't carry the full daily card for every mapped
league.

FIX (2026-09-12, part 2): also passes target_date through to
get_oddstorm_market_odds(). A screenshot of a live league page
(2026-09-12) showed it defaulting to the NEXT upcoming matchday
rather than today — so without a date check, a lookup could silently
pair today's fixture with a different day's odds for a same-named
match. oddstorm_odds.py now parses each league group's own date and
filters by it when target_date is given. Defaults to date.today()
here, same as the Oddsbook branch below, so both branches are
date-consistent by default.
"""

from datetime import date

from oddstorm_leagues import has_oddstorm_coverage, get_oddstorm_league_url
from oddstorm_odds import get_market_odds as get_oddstorm_market_odds
from oddsbook_odds import get_oddsbook_market_odds


def get_combined_market_odds(kickwise_league_name, home, away, target_date=None):
    """
    kickwise_league_name: the "Country - League" string used as the
        key in daily_predictions.py's LEAGUE_CODES / GOALAPI_LEAGUE_IDS
        (e.g. "Algeria - Ligue 1") — used to decide which source to
        try AND (via get_oddstorm_league_url()) to pick the specific
        OddStorm league page to fetch.
    home, away: team names as they appear in your fixtures data.
    target_date: a date to match fixtures/odds against. Defaults to
        date.today() if not given, and is passed to BOTH the OddStorm
        and Oddsbook branches so a match is never paired with the
        wrong day's odds regardless of which source ends up supplying
        them.

    Returns the standard {"market_odds": ..., "market_ou25": ...}
    shape, with a "source" key added so you can see which fetcher
    actually supplied the data (useful for logging/debugging).
    """
    resolved_date = target_date or date.today()

    if has_oddstorm_coverage(kickwise_league_name):
        league_url = get_oddstorm_league_url(kickwise_league_name)
        try:
            result = get_oddstorm_market_odds(
                home, away,
                target_date=resolved_date,
                league_url=league_url,
            )
            if result.get("market_odds") or result.get("market_ou25"):
                result["source"] = "oddstorm"
                return result
        except Exception as e:
            print(f"OddStorm odds failed for {kickwise_league_name} "
                  f"({home} vs {away}): {e} — falling back to Oddsbook")

    # Either OddStorm doesn't cover this league, or it returned
    # nothing for this specific match on this date (e.g. not yet
    # priced, or the fixture only exists on a later matchday page) —
    # try Oddsbook as the fallback.
    try:
        result = get_oddsbook_market_odds(home, away, resolved_date)
        result["source"] = "oddsbook"
        return result
    except Exception as e:
        print(f"Oddsbook odds also failed for {kickwise_league_name} "
              f"({home} vs {away}): {e}")
        return {"market_odds": None, "market_ou25": None, "source": None}
