"""
Odds-API.io league mapping — NOT a complete, individually-verified
mapping of all 88 GOALAPI_LEAGUE_IDS leagues. This file exists to
record leagues where the automated fuzzy-match check
(/debug-odds-api-io-league-coverage) got it WRONG, or where a league
is confirmed to have no usable Odds-API.io coverage at all — built
incrementally, same pattern as oddstorm_leagues.py's own history of
"CORRECTIONS" and "CONFIRMED ABSENT" sections.

WHY THIS FILE IS DELIBERATELY INCOMPLETE:
On 2026-09-16, a naive automated pass (fuzzy string-matching every
Kickwise league name against ~640 distinct league names pulled from
Odds-API.io's live events list) reported 87 of 88 leagues as
"likely_covered". Manually eyeballing a sample of those results found
SIX were wrong despite passing the confidence threshold — including
one that matched an entirely different COUNTRY ("Malaysia - Super
League" -> "Malawi - Super League", fooled by spelling similarity).
Baking all 88 unaudited guesses into this file as if they were
confirmed would repeat exactly the mistake this whole migration
effort has been built around catching (see: the Azerbaijan/Qatar/
Finland-Ykkosliiga naming mixups, the Denmark/Austria stale-season
bug). Only manually-verified entries go here. Everything else falls
through to Odds-API.io's own real-time team-name search in
odds_api_io.py, which already fails safe to None on no match — so an
unaudited league isn't broken, it's just unconfirmed.

CORRECTIONS (fuzzy-match picked the wrong entry — verified via
/debug-odds-api-io-league-search, 2026-09-16):
    Bolivia - LFPB: fuzzy-match picked "Bolivia - Copa Bolivia" (a
        CUP competition, wrong) -> corrected to
        "Bolivia - Division Profesional" (the real league)
    Uruguay - Liga AUF: fuzzy-match picked "Uruguay - Segunda
        Division" (wrong tier) -> corrected to
        "Uruguay - Primera Division, Clausura" (Uruguay splits into
        Apertura/Clausura tournaments; this is the current top-flight
        stage)
    Montenegro - First League: fuzzy-match picked "Montenegro -
        2. CFL" (the SECOND tier) -> corrected to
        "Montenegro - 1. CFL" (the actual top flight)

CONFIRMED ABSENT (verified via /debug-odds-api-io-league-search,
2026-09-16 — Odds-API.io genuinely does not carry these):
    Malaysia - Super League: only "Malaysia FA Cup", "President Cup
        U20", and "Liga A1" exist — no top-flight entry. (Liga A1 is
        semi-pro, not top-flight — independently confirmed by
        oddstorm_leagues.py's own notes on this same league.)
    England - Southern Football League: same structural ambiguity
        oddstorm_leagues.py already documented — TWO same-tier
        geographic divisions exist ("Southern League, Premier
        Division Central" and "...Premier Division South") with no
        single unified entry. Left unmatched rather than guess which
        region, for the same reason oddstorm_leagues.py did.

STILL PENDING — not yet resolved, needs more investigation before
adding:
    Ecuador - Liga Pro: Odds-API.io splits Ecuador's Serie A into
        "Championship Round", "Relegation Round", and "Qualifying
        Round" stage-based entries — no plain "Serie A" entry. Which
        one is CURRENT depends on where the season actually is right
        now (same shape of problem as the GOAL API stale-season-stage
        bug fixed earlier this migration) — do not guess a fixed
        entry here without checking round dates first.

UNAUDITED (2026-09-16): the remaining ~82 of 88 leagues also came
back "likely_covered" from the same fuzzy-match pass, but have NOT
been individually eyeballed the way the six above were. Treat these
the same as any other auto-matched result pending confirmation —
they're probably fine (many showed near-perfect ~1.0+ confidence
scores), but "probably fine" is not the same as "verified", and this
file's whole purpose is to not blur that line.
"""

# Only CONFIRMED entries go here — corrections point to the real
# Odds-API.io league name (kept for reference/debugging only, since
# the actual runtime lookup in odds_api_io.py searches team names
# globally rather than filtering by this value); None means confirmed
# no usable coverage.
ODDS_API_IO_LEAGUE_OVERRIDES = {
    "Bolivia - LFPB": "Bolivia - Division Profesional",
    "Uruguay - Liga AUF": "Uruguay - Primera Division, Clausura",
    "Montenegro - First League": "Montenegro - 1. CFL",
    "Malaysia - Super League": None,  # confirmed absent
    "England - Southern Football League": None,  # confirmed absent — genuinely ambiguous, same as OddStorm
    # "Ecuador - Liga Pro": PENDING — see docstring, do not add until round-currency is checked
}


def has_odds_api_io_coverage(kickwise_league_name):
    """
    Returns False only for leagues CONFIRMED absent above. Returns
    True for everything else, including unaudited leagues — this is
    intentionally permissive, since odds_api_io.py's own team-name
    search already fails safe to None with no side effects if a
    league turns out to have no real coverage. This function exists
    to skip a known-wasted lookup for confirmed-absent leagues, not
    to gate correctness.
    """
    return ODDS_API_IO_LEAGUE_OVERRIDES.get(kickwise_league_name, True) is not None
