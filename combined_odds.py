"""
Kickwise Combined Odds Provider
================================

Purpose
-------
Combines market odds from multiple sources while keeping the rest of
Kickwise independent from the individual odds providers.

PRIMARY:
    OddStorm

FALLBACKS:
    1. Oddsbook
    2. AnnaBet
    3. Odds API IO

Markets:
    - 1X2 (Home / Draw / Away)
    - Over 2.5
    - Under 2.5

Important
---------
1X2 and O/U 2.5 are handled independently.

Therefore a match can have:

    1X2       -> OddStorm
    O/U 2.5   -> OddStorm

or:

    1X2       -> OddStorm
    O/U 2.5   -> Oddsbook

or:

    1X2       -> Oddsbook
    O/U 2.5   -> another fallback

The first valid source wins for each market.

Expected OddStorm function:
    oddstorm_odds.get_market_odds(home, away)

Expected Oddsbook function:
    oddsbook_odds.get_oddsbook_market_odds(home, away)

Existing AnnaBet functions:
    odds.get_odds_for_card(...)
    odds.get_ou25_for_card(...)

Existing Odds API IO functions:
    odds_api_io.get_odds_api_io_fallback(...)
    odds_api_io.get_ou25_api_io_fallback(...)
"""


# ============================================================
# IMPORT ODDSTORM
# ============================================================

try:

    from oddstorm_odds import (
        get_market_odds as get_oddstorm_market_odds
    )

    ODDSTORM_AVAILABLE = True

except Exception as exc:

    print(
        f"[COMBINED ODDS] OddStorm import failed: {exc}"
    )

    get_oddstorm_market_odds = None
    ODDSTORM_AVAILABLE = False


# ============================================================
# IMPORT ODDSBOOK
# ============================================================

try:

    from oddsbook_odds import (
        get_oddsbook_market_odds
    )

    ODDSBOOK_AVAILABLE = True

except Exception as exc:

    print(
        f"[COMBINED ODDS] Oddsbook import failed: {exc}"
    )

    get_oddsbook_market_odds = None
    ODDSBOOK_AVAILABLE = False


# ============================================================
# IMPORT ANNABET
# ============================================================

try:

    from odds import (
        get_odds_for_card,
        get_ou25_for_card
    )

    ANNABET_AVAILABLE = True

except Exception as exc:

    print(
        f"[COMBINED ODDS] AnnaBet import failed: {exc}"
    )

    get_odds_for_card = None
    get_ou25_for_card = None
    ANNABET_AVAILABLE = False


# ============================================================
# IMPORT ODDS API IO
# ============================================================

try:

    from odds_api_io import (
        get_odds_api_io_fallback,
        get_ou25_api_io_fallback
    )

    ODDS_API_IO_AVAILABLE = True

except Exception as exc:

    print(
        f"[COMBINED ODDS] Odds API IO import failed: {exc}"
    )

    get_odds_api_io_fallback = None
    get_ou25_api_io_fallback = None
    ODDS_API_IO_AVAILABLE = False


# ============================================================
# HELPERS
# ============================================================

def _is_number(value):
    """
    True when value can safely be treated as a positive number.
    """

    try:

        value = float(value)

        return value > 0

    except (
        TypeError,
        ValueError
    ):

        return False


def _valid_1x2(market):
    """
    Check whether a returned market contains complete 1X2 odds.

    Required:
        home_odds
        draw_odds
        away_odds
    """

    if not isinstance(
        market,
        dict
    ):
        return False

    return (
        _is_number(
            market.get("home_odds")
        )
        and
        _is_number(
            market.get("draw_odds")
        )
        and
        _is_number(
            market.get("away_odds")
        )
    )


def _valid_ou25(market):
    """
    Check whether a returned market contains complete
    Over/Under 2.5 odds.
    """

    if not isinstance(
        market,
        dict
    ):
        return False

    return (
        _is_number(
            market.get("over_odds")
        )
        and
        _is_number(
            market.get("under_odds")
        )
    )


def _empty_result():
    """
    Standard empty response.
    """

    return {
        "market_odds": None,
        "market_ou25": None,

        "odds_source": None,
        "ou25_source": None,

        "odds_status": "not_found",
        "ou25_status": "not_found",
    }


def _safe_dict(value):
    """
    Return dictionary or empty dictionary.
    """

    if isinstance(
        value,
        dict
    ):
        return value

    return {}


# ============================================================
# ODDSTORM
# ============================================================

def _try_oddstorm(home, away):
    """
    Try OddStorm.

    Returns:
        {
            "market_odds": ...,
            "market_ou25": ...
        }

    or empty dictionary.
    """

    if not ODDSTORM_AVAILABLE:
        return {}

    if not get_oddstorm_market_odds:
        return {}

    try:

        result = get_oddstorm_market_odds(
            home,
            away
        )

        return _safe_dict(
            result
        )

    except Exception as exc:

        print(
            "[COMBINED ODDS] "
            f"OddStorm error for "
            f"{home} vs {away}: {exc}"
        )

        return {}


# ============================================================
# ODDSBOOK
# ============================================================

def _try_oddsbook(home, away):
    """
    Try Oddsbook.
    """

    if not ODDSBOOK_AVAILABLE:
        return {}

    if not get_oddsbook_market_odds:
        return {}

    try:

        result = get_oddsbook_market_odds(
            home,
            away
        )

        return _safe_dict(
            result
        )

    except Exception as exc:

        print(
            "[COMBINED ODDS] "
            f"Oddsbook error for "
            f"{home} vs {away}: {exc}"
        )

        return {}


# ============================================================
# ANNABET 1X2
# ============================================================

def _try_annabet_1x2(
    league,
    home,
    away
):
    """
    Try existing AnnaBet 1X2 provider.
    """

    if not ANNABET_AVAILABLE:
        return None

    if not get_odds_for_card:
        return None

    try:

        result = get_odds_for_card(
            league,
            home,
            away
        )

        if _valid_1x2(result):
            return result

    except Exception as exc:

        print(
            "[COMBINED ODDS] "
            f"AnnaBet 1X2 error: {exc}"
        )

    return None


# ============================================================
# ANNABET O/U 2.5
# ============================================================

def _try_annabet_ou25(
    league,
    home,
    away
):
    """
    Try existing AnnaBet O/U 2.5 provider.
    """

    if not ANNABET_AVAILABLE:
        return None

    if not get_ou25_for_card:
        return None

    try:

        result = get_ou25_for_card(
            league,
            home,
            away
        )

        if _valid_ou25(result):
            return result

    except Exception as exc:

        print(
            "[COMBINED ODDS] "
            f"AnnaBet O/U 2.5 error: {exc}"
        )

    return None


# ============================================================
# ODDS API IO 1X2
# ============================================================

def _try_odds_api_io_1x2(
    league,
    home,
    away
):
    """
    Try existing Odds API IO 1X2 fallback.
    """

    if not ODDS_API_IO_AVAILABLE:
        return None

    if not get_odds_api_io_fallback:
        return None

    try:

        result = get_odds_api_io_fallback(
            league,
            home,
            away
        )

        if _valid_1x2(result):
            return result

    except Exception as exc:

        print(
            "[COMBINED ODDS] "
            f"Odds API IO 1X2 error: {exc}"
        )

    return None


# ============================================================
# ODDS API IO O/U
# ============================================================

def _try_odds_api_io_ou25(
    league,
    home,
    away
):
    """
    Try existing Odds API IO O/U 2.5 fallback.
    """

    if not ODDS_API_IO_AVAILABLE:
        return None

    if not get_ou25_api_io_fallback:
        return None

    try:

        result = get_ou25_api_io_fallback(
            league,
            home,
            away
        )

        if _valid_ou25(result):
            return result

    except Exception as exc:

        print(
            "[COMBINED ODDS] "
            f"Odds API IO O/U error: {exc}"
        )

    return None


# ============================================================
# MAIN FUNCTION
# ============================================================

def get_combined_market_odds(
    league,
    home,
    away,
    target_date=None
):
    """
    Get the best available market odds for a fixture.

    Priority
    --------

    1X2:

        OddStorm
            ↓
        Oddsbook
            ↓
        AnnaBet
            ↓
        Odds API IO

    O/U 2.5:

        OddStorm
            ↓
        Oddsbook
            ↓
        AnnaBet
            ↓
        Odds API IO


    Parameters
    ----------
    league:
        Kickwise league name.

    home:
        Home team.

    away:
        Away team.

    target_date:
        Optional fixture date.

        Kept in the interface for compatibility with app.py.

        Current OddStorm lookup uses home + away because that is the
        interface of the existing working OddStorm scraper.


    Returns
    -------

    {
        "market_odds": {...} or None,
        "market_ou25": {...} or None,

        "odds_source": "OddStorm",
        "ou25_source": "OddStorm",

        "odds_status": "found",
        "ou25_status": "found"
    }

    1X2 and O/U are deliberately independent.
    """

    result = _empty_result()

    print(
        "\n"
        "==================================================\n"
        "[COMBINED ODDS]\n"
        f"League : {league}\n"
        f"Match  : {home} vs {away}\n"
        f"Date   : {target_date}\n"
        "=================================================="
    )

    # ========================================================
    # 1. ODDSTORM
    # ========================================================

    oddstorm = _try_oddstorm(
        home,
        away
    )

    oddstorm_1x2 = oddstorm.get(
        "market_odds"
    )

    oddstorm_ou25 = oddstorm.get(
        "market_ou25"
    )

    # --------------------------------------------------------
    # OddStorm 1X2
    # --------------------------------------------------------

    if _valid_1x2(
        oddstorm_1x2
    ):

        result["market_odds"] = (
            oddstorm_1x2
        )

        result["odds_source"] = (
            "OddStorm"
        )

        result["odds_status"] = (
            "found"
        )

        print(
            "[COMBINED ODDS] "
            "1X2 = OddStorm"
        )

    else:

        print(
            "[COMBINED ODDS] "
            "OddStorm 1X2 unavailable"
        )

    # --------------------------------------------------------
    # OddStorm O/U 2.5
    # --------------------------------------------------------

    if _valid_ou25(
        oddstorm_ou25
    ):

        result["market_ou25"] = (
            oddstorm_ou25
        )

        result["ou25_source"] = (
            "OddStorm"
        )

        result["ou25_status"] = (
            "found"
        )

        print(
            "[COMBINED ODDS] "
            "O/U 2.5 = OddStorm"
        )

    else:

        print(
            "[COMBINED ODDS] "
            "OddStorm O/U 2.5 unavailable"
        )

    # ========================================================
    # 2. ODDSBOOK FALLBACK
    # ========================================================

    if (
        result["market_odds"] is None
        or
        result["market_ou25"] is None
    ):

        oddsbook = _try_oddsbook(
            home,
            away
        )

        oddsbook_1x2 = oddsbook.get(
            "market_odds"
        )

        oddsbook_ou25 = oddsbook.get(
            "market_ou25"
        )

        # ----------------------------------------------------
        # Oddsbook 1X2
        # ----------------------------------------------------

        if (
            result["market_odds"] is None
            and
            _valid_1x2(
                oddsbook_1x2
            )
        ):

            result["market_odds"] = (
                oddsbook_1x2
            )

            result["odds_source"] = (
                "Oddsbook"
            )

            result["odds_status"] = (
                "found"
            )

            print(
                "[COMBINED ODDS] "
                "1X2 = Oddsbook fallback"
            )

        # ----------------------------------------------------
        # Oddsbook O/U
        # ----------------------------------------------------

        if (
            result["market_ou25"] is None
            and
            _valid_ou25(
                oddsbook_ou25
            )
        ):

            result["market_ou25"] = (
                oddsbook_ou25
            )

            result["ou25_source"] = (
                "Oddsbook"
            )

            result["ou25_status"] = (
                "found"
            )

            print(
                "[COMBINED ODDS] "
                "O/U 2.5 = Oddsbook fallback"
            )

    # ========================================================
    # 3. ANNABET FALLBACK
    # ========================================================

    # --------------------------------------------------------
    # AnnaBet 1X2
    # --------------------------------------------------------

    if result["market_odds"] is None:

        annabet_1x2 = _try_annabet_1x2(
            league,
            home,
            away
        )

        if annabet_1x2 is not None:

            result["market_odds"] = (
                annabet_1x2
            )

            result["odds_source"] = (
                "AnnaBet"
            )

            result["odds_status"] = (
                "found"
            )

            print(
                "[COMBINED ODDS] "
                "1X2 = AnnaBet fallback"
            )

    # --------------------------------------------------------
    # AnnaBet O/U
    # --------------------------------------------------------

    if result["market_ou25"] is None:

        annabet_ou25 = _try_annabet_ou25(
            league,
            home,
            away
        )

        if annabet_ou25 is not None:

            result["market_ou25"] = (
                annabet_ou25
            )

            result["ou25_source"] = (
                "AnnaBet"
            )

            result["ou25_status"] = (
                "found"
            )

            print(
                "[COMBINED ODDS] "
                "O/U 2.5 = AnnaBet fallback"
            )

    # ========================================================
    # 4. ODDS API IO FALLBACK
    # ========================================================

    # --------------------------------------------------------
    # Odds API IO 1X2
    # --------------------------------------------------------

    if result["market_odds"] is None:

        api_io_1x2 = (
            _try_odds_api_io_1x2(
                league,
                home,
                away
            )
        )

        if api_io_1x2 is not None:

            result["market_odds"] = (
                api_io_1x2
            )

            result["odds_source"] = (
                "OddsAPI-IO"
            )

            result["odds_status"] = (
                "found"
            )

            print(
                "[COMBINED ODDS] "
                "1X2 = OddsAPI-IO fallback"
            )

    # --------------------------------------------------------
    # Odds API IO O/U
    # --------------------------------------------------------

    if result["market_ou25"] is None:

        api_io_ou25 = (
            _try_odds_api_io_ou25(
                league,
                home,
                away
            )
        )

        if api_io_ou25 is not None:

            result["market_ou25"] = (
                api_io_ou25
            )

            result["ou25_source"] = (
                "OddsAPI-IO"
            )

            result["ou25_status"] = (
                "found"
            )

            print(
                "[COMBINED ODDS] "
                "O/U 2.5 = OddsAPI-IO fallback"
            )

    # ========================================================
    # FINAL STATUS
    # ========================================================

    if result["market_odds"] is None:

        result["odds_status"] = (
            "not_found"
        )

    if result["market_ou25"] is None:

        result["ou25_status"] = (
            "not_found"
        )

    # ========================================================
    # FINAL LOG
    # ========================================================

    print(
        "\n"
        "[COMBINED ODDS FINAL]\n"
        f"1X2 source    : "
        f"{result['odds_source']}\n"
        f"O/U 2.5 source: "
        f"{result['ou25_source']}\n"
        f"1X2 status    : "
        f"{result['odds_status']}\n"
        f"O/U status    : "
        f"{result['ou25_status']}\n"
    )

    return result


# ============================================================
# SIMPLE 1X2 FUNCTION
# ============================================================

def get_combined_1x2(
    league,
    home,
    away,
    target_date=None
):
    """
    Convenience function.

    Returns only the selected 1X2 market.
    """

    result = get_combined_market_odds(
        league,
        home,
        away,
        target_date
    )

    return result.get(
        "market_odds"
    )


# ============================================================
# SIMPLE O/U 2.5 FUNCTION
# ============================================================

def get_combined_ou25(
    league,
    home,
    away,
    target_date=None
):
    """
    Convenience function.

    Returns only the selected O/U 2.5 market.
    """

    result = get_combined_market_odds(
        league,
        home,
        away,
        target_date
    )

    return result.get(
        "market_ou25"
    )


# ============================================================
# PROVIDER STATUS
# ============================================================

def combined_odds_status():
    """
    Show which providers successfully imported.

    Useful for /health or debugging.
    """

    return {
        "oddstorm": ODDSTORM_AVAILABLE,
        "oddsbook": ODDSBOOK_AVAILABLE,
        "annabet": ANNABET_AVAILABLE,
        "odds_api_io": ODDS_API_IO_AVAILABLE,

        "priority": [
            "OddStorm",
            "Oddsbook",
            "AnnaBet",
            "OddsAPI-IO"
        ],

        "markets": [
            "1X2",
            "Over 2.5",
            "Under 2.5"
        ]
    }

One thing to check before deploying

Your "app.py" already has:

from combined_odds import get_combined_market_odds

so you don't need to change that import.

Your existing call:

get_combined_market_odds(
    league,
    home,
    away,
    target_date
)

also remains compatible.

The resulting priority is now:

1X2

OddStorm
   ↓ unavailable
Oddsbook
   ↓ unavailable
AnnaBet
   ↓ unavailable
Odds API IO

O/U 2.5

OddStorm
   ↓ unavailable
Oddsbook
   ↓ unavailable
AnnaBet
   ↓ unavailable
Odds API IO

That independent fallback is important: if OddStorm has 1X2 but happens to be missing O/U 2.5 for one match, Kickwise doesn't throw away the good 1X2 odds.

One caution: your current OddStorm scraper searches its main odds page by team names only. So this combined file is ready, but I would test it with one actual GOAL API fixture before relying on it for hundreds of matches. The next useful test is your "Ogre United - FK Liepāja" example through "/predict-combined-test", because that will tell us whether the GOAL API team names match OddStorm correctly and whether both 1X2 and O/U 2.5 are reaching the existing value/confirmation logic.
