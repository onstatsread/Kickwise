"""
Kickwise — The Odds API sport_key resolver
--------------------------------------------
The Odds API identifies leagues with its own "sport_key" strings
(e.g. "soccer_epl", "soccer_brazil_campeonato") that don't match
Kickwise's "Country - League" naming. Rather than hardcode a guessed
mapping — risky, since a wrong key silently returns zero matches with
no error — this module fetches The Odds API's own official list via
GET /v4/sports ONCE, caches it, and fuzzy-matches each Kickwise league
name against it automatically.

CONFIRMED per The Odds API's own docs: GET /v4/sports does NOT count
against your paid quota (it's a free reference endpoint), so calling
it liberally (once per day, cached) costs nothing.

The Odds API's soccer coverage is comparatively narrow — mostly
top-flight European leagues, a few top-flight leagues elsewhere
(Brazil, USA/MLS, etc.) — so most of Kickwise's 61 leagues will have
NO match here. That's expected and fine: get_sport_key() returns None
for anything uncovered, and callers should treat that exactly like
"this source doesn't have this league" and move to the next fallback.
"""

import os
import time
import httpx
from difflib import SequenceMatcher

ODDS_API_KEY = os.getenv("ODDS_API_KEY", "").strip()
ODDS_API_BASE = "https://api.the-odds-api.com/v4"

_SPORTS_LIST_CACHE_TTL = 24 * 60 * 60  # 24 hours — this list changes rarely
_sports_list_cache: tuple[float, list] | None = None

# Manual override for cases where fuzzy matching could plausibly guess
# wrong (e.g. multiple divisions of the same country/sport name) —
# checked BEFORE fuzzy matching. Add entries here if a specific
# Kickwise league keeps resolving to the wrong sport_key.
MANUAL_OVERRIDES: dict[str, str] = {
    # "Kickwise league name": "confirmed_correct_sport_key",
}


async def _get_sports_list(force_refresh: bool = False) -> list:
    global _sports_list_cache

    if not force_refresh and _sports_list_cache:
        fetched_at, data = _sports_list_cache
        if time.time() - fetched_at < _SPORTS_LIST_CACHE_TTL:
            return data

    if not ODDS_API_KEY:
        print("  [odds_api_leagues] No ODDS_API_KEY set — cannot resolve sport_keys")
        return []

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.get(
                f"{ODDS_API_BASE}/sports",
                params={"apiKey": ODDS_API_KEY, "all": "true"},
            )
        if resp.status_code != 200:
            print(f"  [odds_api_leagues] /sports fetch failed: HTTP {resp.status_code} — {resp.text[:200]}")
            return _sports_list_cache[1] if _sports_list_cache else []

        data = resp.json()
        soccer_only = [s for s in data if s.get("key", "").startswith("soccer_")]
        print(f"  [odds_api_leagues] Fetched {len(soccer_only)} soccer competitions from The Odds API "
              f"(does not count against quota)")
        _sports_list_cache = (time.time(), soccer_only)
        return soccer_only

    except Exception as e:
        print(f"  [odds_api_leagues] Exception fetching /sports: {e}")
        return _sports_list_cache[1] if _sports_list_cache else []


def _normalize(text: str) -> str:
    return " ".join(text.lower().replace("-", " ").replace(".", "").split())


def _score(kickwise_league_name: str, sport_entry: dict) -> float:
    """
    Compares a Kickwise "Country - League" name against one of The Odds
    API's sport entries, which look like:
        {"key": "soccer_epl", "group": "Soccer",
         "title": "EPL", "description": "English Premier League"}
    Scores against BOTH title and description, keeping the best.
    """
    kw_norm = _normalize(kickwise_league_name)

    candidates = [sport_entry.get("title", ""), sport_entry.get("description", "")]
    best = 0.0
    for candidate in candidates:
        if not candidate:
            continue
        cand_norm = _normalize(candidate)
        score = SequenceMatcher(None, kw_norm, cand_norm).ratio()

        # Boost for exact country match at minimum — the "Country - "
        # prefix in Kickwise names is a strong, cheap signal to check.
        country = kw_norm.split(" ")[0] if kw_norm else ""
        if country and country in cand_norm:
            score += 0.15

        best = max(best, score)

    return best


async def get_sport_key(kickwise_league_name: str, min_confidence: float = 0.55) -> str | None:
    """
    Returns The Odds API's sport_key for a given Kickwise league name,
    or None if there's no confident match (meaning: this source almost
    certainly doesn't cover this league — treat like any other "no
    coverage" case and move to the next fallback source).

    min_confidence is deliberately conservative — a wrong guess here
    means silently pulling ANOTHER league's odds under the wrong
    match, which is worse than just having no odds at all. When in
    doubt, this returns None rather than a low-confidence guess.
    """
    if kickwise_league_name in MANUAL_OVERRIDES:
        return MANUAL_OVERRIDES[kickwise_league_name]

    sports = await _get_sports_list()
    if not sports:
        return None

    best_key, best_score = None, 0.0
    for entry in sports:
        score = _score(kickwise_league_name, entry)
        if score > best_score:
            best_score = score
            best_key = entry.get("key")

    if best_key is None or best_score < min_confidence:
        return None

    print(f"  [odds_api_leagues] Matched '{kickwise_league_name}' -> '{best_key}' "
          f"(confidence {best_score:.2f})")
    return best_key
