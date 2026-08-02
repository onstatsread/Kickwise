"""
Kickwise Backend — FastAPI server
Deploy to Render.com (free tier)
"""
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
import requests, os, subprocess, statistics, tempfile, shutil, difflib, re
from scipy.stats import poisson
from datetime import date
from bs4 import BeautifulSoup
from openpyxl import load_workbook
from concurrent.futures import ThreadPoolExecutor
from odds import get_odds_for_card, get_ou25_for_card, router as odds_router  # NEW — market odds from The Odds API
from api_football import get_fallback_odds  # Fallback tier 2 — currently suspended, kept in case reactivated
from odds_api_io import get_odds_api_io_fallback, get_ou25_api_io_fallback  # NEW — fallback tier, tried before api_football

app = FastAPI(title="Kickwise API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)

app.include_router(odds_router)  # NEW — registers /odds/{sport_key} and /odds/{sport_key}/{home}/{away}


HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.soccerstats.com/",
    "Connection": "keep-alive",
}
BASE    = "https://www.soccerstats.com"
MODEL   = "A_mix2.xlsx"

# NEW — maps your SoccerStats league codes to The Odds API's sport keys.
# Only major leagues are covered by the odds provider — leagues not listed
# here simply won't get market_odds (the frontend already handles that
# gracefully since it only renders when odds data is present).
LEAGUE_TO_SPORT_KEY = {
    # Verified directly against this account's /v4/sports/ response —
    # every key below was confirmed to exist, not guessed.
    "argentina":   "soccer_argentina_primera_division",  # Apertura; same key likely also covers Clausura fixtures
    "austria":     "soccer_austria_bundesliga",
    "belgium":     "soccer_belgium_first_div",
    "brazil":      "soccer_brazil_campeonato",
    "brazil2":     "soccer_brazil_serie_b",
    "chile":       "soccer_chile_campeonato",
    "china":       "soccer_china_superleague",
    "denmark":     "soccer_denmark_superliga",
    "england":     "soccer_epl",
    "england2":    "soccer_efl_champ",
    "england3":    "soccer_england_league1",
    "england4":    "soccer_england_league2",
    "finland":     "soccer_finland_veikkausliiga",
    "france":      "soccer_france_ligue_one",
    "germany":     "soccer_germany_bundesliga",
    "germany2":    "soccer_germany_bundesliga2",
    "germany3":    "soccer_germany_liga3",
    "greece":      "soccer_greece_super_league",
    "ireland":     "soccer_league_of_ireland",
    "italy":       "soccer_italy_serie_a",
    "italy2":      "soccer_italy_serie_b",
    "mexico":      "soccer_mexico_ligamx",  # Apertura; likely also covers Clausura fixtures
    "netherlands": "soccer_netherlands_eredivisie",
    "norway":      "soccer_norway_eliteserien",
    "poland":      "soccer_poland_ekstraklasa",
    "portugal":    "soccer_portugal_primeira_liga",
    "russia":      "soccer_russia_premier_league",
    "scotland":    "soccer_spl",
    "southkorea":  "soccer_korea_kleague1",
    "spain":       "soccer_spain_la_liga",
    "sweden":      "soccer_sweden_allsvenskan",
    "sweden2":     "soccer_sweden_superettan",
    "switzerland": "soccer_switzerland_superleague",
    "turkey":      "soccer_turkey_super_league",
    "usa":         "soccer_usa_mls",
    # NOT available on this plan (confirmed absent from /v4/sports/):
    # UEFA Champions League, UEFA Europa League — only qualification
    # rounds and Nations League exist in this account's coverage.
    # Re-check /v4/sports/?apiKey=YOUR_KEY periodically — The Odds API
    # adds leagues over time, and any newly available league just needs
    # a new line here.
}


def calc_win_draw_away(lambda_home, lambda_away, max_goals=10):
    """Compute Win/Draw/Away probabilities from Poisson-distributed expected goals."""
    try:
        lambda_home = float(lambda_home)
        lambda_away = float(lambda_away)
    except (TypeError, ValueError):
        return None
    if lambda_home < 0 or lambda_away < 0:
        return None

    home_win = draw = away_win = 0.0
    for h in range(max_goals + 1):
        for a in range(max_goals + 1):
            p = poisson.pmf(h, lambda_home) * poisson.pmf(a, lambda_away)
            if h > a:
                home_win += p
            elif h == a:
                draw += p
            else:
                away_win += p
    total = home_win + draw + away_win
    if total <= 0:
        return None
    return {
        "home_pct": round(home_win / total * 100, 1),
        "draw_pct": round(draw / total * 100, 1),
        "away_pct": round(away_win / total * 100, 1),
    }


def pct_to_odds(pct):
    if not pct or pct <= 0:
        return None
    return round(100 / pct, 2)


def calc_odds(lambda_home, lambda_away):
    wda = calc_win_draw_away(lambda_home, lambda_away)
    if not wda:
        return {"home_pct": None, "draw_pct": None, "away_pct": None,
                "home_odds": None, "draw_odds": None, "away_odds": None}
    return {
        "home_pct": wda["home_pct"], "draw_pct": wda["draw_pct"], "away_pct": wda["away_pct"],
        "home_odds": pct_to_odds(wda["home_pct"]),
        "draw_odds": pct_to_odds(wda["draw_pct"]),
        "away_odds": pct_to_odds(wda["away_pct"]),
    }


def calc_over_under_25(lambda_home, lambda_away, max_goals=10):
    """
    Model's own Over/Under 2.5 goals probabilities — same Poisson approach
    as calc_win_draw_away, just summing joint probabilities by TOTAL goals
    (home + away) relative to the 2.5 line instead of by which side scores
    more. Returns model-implied odds the same way calc_odds() does for 1X2.
    """
    try:
        lambda_home = float(lambda_home)
        lambda_away = float(lambda_away)
    except (TypeError, ValueError):
        return {"over_pct": None, "under_pct": None, "over_odds": None, "under_odds": None}
    if lambda_home < 0 or lambda_away < 0:
        return {"over_pct": None, "under_pct": None, "over_odds": None, "under_odds": None}

    over = under = 0.0
    for h in range(max_goals + 1):
        for a in range(max_goals + 1):
            p = poisson.pmf(h, lambda_home) * poisson.pmf(a, lambda_away)
            if h + a > 2.5:
                over += p
            else:
                under += p
    total = over + under
    if total <= 0:
        return {"over_pct": None, "under_pct": None, "over_odds": None, "under_odds": None}

    over_pct = round(over / total * 100, 1)
    under_pct = round(under / total * 100, 1)
    return {
        "over_pct": over_pct, "under_pct": under_pct,
        "over_odds": pct_to_odds(over_pct), "under_odds": pct_to_odds(under_pct),
    }


def resolve_team(name, team_data):
    """
    Try to match `name` to a team in team_data (SoccerStats' own stats page).
    Returns None — rather than a wrong guess — when no confident match exists.
    Silently substituting the closest-sounding name (e.g. matching
    "KuPS Akatemia" to "SJK Akatemia") corrupts the whole prediction
    downstream, so this only returns a match it's actually confident about.
    """
    if name in team_data:
        return name

    # Substring match — only accept if exactly ONE team qualifies, and the
    # shorter string is long enough that a coincidental substring is unlikely.
    substring_candidates = [
        k for k in team_data
        if len(name) >= 5 and (name in k or k in name)
    ]
    if len(substring_candidates) == 1:
        return substring_candidates[0]

    # Fuzzy match — raised cutoff from 0.6 to 0.8, and only accept if
    # there's a single clear best match (not several similarly-close ones).
    close = difflib.get_close_matches(name, team_data.keys(), n=2, cutoff=0.8)
    if len(close) == 1:
        return close[0]

    return None


def fetch_stats(code):
    teams = {}
    try:
        soup = BeautifulSoup(
            requests.get(f"{BASE}/homeaway.asp?league={code}", headers=HEADERS, timeout=15).text,
            "html.parser")
        tables = soup.find_all("table")
        section_count = 0
        for tbl in tables:
            valid_rows = []
            for row in tbl.find_all("tr"):
                cells = row.find_all("td")
                if len(cells) < 8:
                    continue
                team = cells[1].get_text(strip=True)
                if not team:
                    continue
                try:
                    gp = float(cells[2].get_text(strip=True))
                    gf = float(cells[6].get_text(strip=True))
                    ga = float(cells[7].get_text(strip=True))
                except:
                    continue
                if gp <= 0:
                    continue
                valid_rows.append((team, gp, gf, ga))
            if len(valid_rows) >= 10:
                section_count += 1
                for team, gp, gf, ga in valid_rows:
                    if team not in teams:
                        teams[team] = {}
                    if section_count == 1:
                        teams[team]["hgp"] = gp
                        teams[team]["hgf"] = gf
                        teams[team]["hga"] = ga
                    elif section_count == 2:
                        teams[team]["agp"] = gp
                        teams[team]["agf"] = gf
                        teams[team]["aga"] = ga
            if section_count >= 2:
                break
    except Exception as e:
        print(f"Stats error: {e}")

    result = {}
    for team, d in teams.items():
        if "hgp" not in d or "agp" not in d:
            continue
        hgp, hgf, hga = d["hgp"], d["hgf"], d["hga"]
        agp, agf, aga = d["agp"], d["agf"], d["aga"]
        gp = hgp + agp
        gf = hgf + agf
        ga = hga + aga
        result[team] = {
            "gp": gp,
            "gf": gf / gp if gp else 0,
            "ga": ga / gp if gp else 0,
            "tot": (gf + ga) / gp if gp else 0,
            "hgf": hgf / hgp if hgp else 0,
            "hga": hga / hgp if hgp else 0,
            "htot": (hgf + hga) / hgp if hgp else 0,
            "agf": agf / agp if agp else 0,
            "aga": aga / agp if agp else 0,
            "atot": (agf + aga) / agp if agp else 0,
        }
    return result

TIME_RE  = re.compile(r'\b([01]?\d|2[0-3]):([0-5]\d)\b')
DAY_RE   = re.compile(r'^(Mon|Tue|Wed|Thu|Fri|Sat|Sun)\b\s*')


def clean_team_name(name):
    return DAY_RE.sub("", name).strip()


def _norm_key(a, b):
    """Normalized dedup key — lowercased, whitespace-collapsed, so the same
    match listed with slightly different formatting across SoccerStats'
    different tables (extra spaces, casing) is still recognized as a
    duplicate instead of slipping through as two separate fixtures."""
    def clean(s):
        return " ".join(s.lower().split())
    return (clean(a), clean(b))


def fetch_fixtures(code, date_str=None):
    if date_str:
        today1 = date_str.strip()
    else:
        d = date.today()
        today1 = f"{d.day} {d.strftime('%b')}"

    matches = []
    seen = set()
    time_map = {}

    try:
        resp = requests.get(f"{BASE}/latest.asp?league={code}",
                            headers=HEADERS, timeout=8)
        soup = BeautifulSoup(resp.text, "html.parser")

        # Pass 0 - dedicated "upcoming matches" table near top of page.
        # Structure: "Sun 28 Jun 16:00" | "TeamA TeamB" | pmatch link
        for table in soup.find_all("table"):
            for row in table.find_all("tr"):
                cells = row.find_all("td")
                if len(cells) < 2:
                    continue
                c0 = cells[0].get_text(" ", strip=True)
                c0_nodate = DAY_RE.sub("", c0).strip()
                if not c0_nodate.startswith(today1):
                    continue
                t = TIME_RE.search(c0)
                if not t:
                    continue
                time_str = f"{t.group(1)}:{t.group(2)}"

                links = [a.get_text(strip=True) for a in row.find_all("a")
                         if "team=" in (a.get("href") or "") or "teamstats.asp" in (a.get("href") or "")]
                if len(links) >= 2:
                    h, a_ = clean_team_name(links[0]), clean_team_name(links[1])
                else:
                    c1 = cells[1].get_text(" ", strip=True) if len(cells) > 1 else ""
                    parts = c1.split()
                    if len(parts) < 2:
                        continue
                    mid = len(parts) // 2
                    h = clean_team_name(" ".join(parts[:mid]))
                    a_ = clean_team_name(" ".join(parts[mid:]))

                if h and a_ and h != a_ and len(h) < 30 and len(a_) < 30:
                    time_map[_norm_key(h, a_)] = time_str
                    time_map[_norm_key(a_, h)] = time_str
                    key = _norm_key(h, a_)
                    if key not in seen:
                        seen.add(key)
                        matches.append({"time": time_str, "home": h, "away": a_})

        # Pass 1 - collect any remaining matches from form history table rows
        for table in soup.find_all("table"):
            for row in table.find_all("tr"):
                cells = row.find_all("td")
                if len(cells) < 3:
                    continue
                c0 = cells[0].get_text(" ", strip=True)
                c0_clean = DAY_RE.sub("", c0).strip()
                if not (c0_clean == today1 or c0_clean.startswith(today1 + " ")):
                    continue
                c_last = cells[-1].get_text(strip=True)
                if c_last != "-":
                    continue
                c1 = cells[1].get_text(" ", strip=True)
                if " - " not in c1 or len(c1) > 50:
                    continue
                home_raw, away_raw = c1.split(" - ", 1)
                home = clean_team_name(home_raw)
                away = clean_team_name(away_raw)
                if not home or not away or home == away:
                    continue
                if len(home) > 25 or len(away) > 25:
                    continue
                key = _norm_key(home, away)
                if key in seen:
                    continue
                seen.add(key)
                t = TIME_RE.search(c0)
                time_str = f"{t.group(1)}:{t.group(2)}" if t else ""
                matches.append({"time": time_str, "home": home, "away": away})

        # Pass 2 - backfill times for any match still missing one
        for m in matches:
            if not m["time"]:
                key = _norm_key(m["home"], m["away"])
                if key in time_map:
                    m["time"] = time_map[key]
                else:
                    for (h, a), t in time_map.items():
                        if (m["home"] in h or h in m["home"]) and \
                           (m["away"] in a or a in m["away"]):
                            m["time"] = t
                            break

    except Exception as e:
        print(f"  Fixtures error: {e}")

    # Final safety-net dedup — belt-and-braces in case anything slipped
    # through the per-pass checks above with a normalized-key collision
    # that wasn't caught inline (e.g. ordering edge cases).
    final_seen = set()
    deduped = []
    for m in matches:
        key = _norm_key(m["home"], m["away"])
        if key in final_seen:
            continue
        final_seen.add(key)
        deduped.append(m)

    return deduped



def run_model(home, away, team_data):
    if home not in team_data or away not in team_data:
        return {"d70": "N/A", "b120": "N/A", "c120": "N/A", "b46": "N/A", "d64": "N/A", "b118": "N/A", "aa15": "N/A", "b54": "N/A", "odds": None, "ou25": None, "b119": "", "d119": "", "d70val": "", "o73": "", "o74": ""}

    data = sorted([
        (n, d["gp"], d["gf"], d["ga"], d["tot"],
         d["hgf"], d["hga"], d["htot"], d["agf"], d["aga"], d["atot"])
        for n, d in team_data.items()], key=lambda x: x[0])

    lhs = statistics.mean([d[5] for d in data]) or 1
    lhc = statistics.mean([d[6] for d in data]) or 1
    las = statistics.mean([d[8] for d in data]) or 1
    lac = statistics.mean([d[9] for d in data]) or 1

    wb = load_workbook(MODEL)
    ws = wb.active
    for row in ws.iter_rows(min_row=6, max_row=42, min_col=3, max_col=22):
        for cell in row:
            cell.value = None

    for i, d in enumerate(data):
        r = 6 + i
        hs, hc, ht = d[5], d[6], d[7]
        as_, ac, at_ = d[8], d[9], d[10]
        ws.cell(r, 3).value  = d[0]
        ws.cell(r, 4).value  = d[1]
        ws.cell(r, 5).value  = round(d[2], 4)
        ws.cell(r, 6).value  = round(d[3], 4)
        ws.cell(r, 7).value  = round(d[4], 4)
        ws.cell(r, 8).value  = "  "
        ws.cell(r, 9).value  = round(hs, 4)
        ws.cell(r, 10).value = round(hc, 4)
        ws.cell(r, 11).value = round(ht, 4)
        ws.cell(r, 12).value = "  "
        ws.cell(r, 13).value = round(as_, 4)
        ws.cell(r, 14).value = round(ac, 4)
        ws.cell(r, 15).value = round(at_, 4)
        ws.cell(r, 16).value = round(hs / lhs, 4)
        ws.cell(r, 17).value = round(hc / lhc, 4)
        ws.cell(r, 18).value = round(as_ / las, 4)
        ws.cell(r, 19).value = round(ac / lac, 4)
        ws.cell(r, 20).value = round(max((hs - as_) / d[1], 0), 4)
        ws.cell(r, 22).value = round((ht + at_) / 2, 4)

    ws["B69"] = home
    ws["C69"] = away
    ws.title  = "Sheet1"

    tmp_dir  = tempfile.mkdtemp()
    tmp_file = os.path.join(tmp_dir, "fm_tmp.xlsx")
    out_dir  = os.path.join(tmp_dir, "out")
    os.makedirs(out_dir)
    wb.save(tmp_file)

    subprocess.run(["libreoffice", "--headless", "--calc", "--convert-to", "xlsx",
                    "--outdir", out_dir, tmp_file],
                   capture_output=True, timeout=90)

    out_file = os.path.join(out_dir, "fm_tmp.xlsx")
    wb2 = load_workbook(out_file, data_only=True)
    ws2 = wb2.active

    d70  = str(ws2["D69"].value  or "")
    c120 = str(ws2["C120"].value or "")

    # B120 = TEXTJOIN of B119/C119/D119 ("double"/"under"/"run").
    # Always rebuild from the three source cells directly so we reliably
    # capture all 4 outcomes: empty, "double", "under", or "double /under".
    b119_raw = str(ws2["B119"].value or "")
    c119_raw = str(ws2["C119"].value or "")
    d119_raw = str(ws2["D119"].value or "")
    parts = [x for x in [b119_raw, c119_raw, d119_raw]
             if x and x not in ("run", "#NAME?", "#N/A", "None")]
    b120 = " /".join(parts)  # "" if none, "double" / "under" alone, or "double /under" combined

    # Helper: safely read a cell, returning "" on any error value
    def safe(ref, sheet=None):
        s = sheet if sheet else ws2
        v = str(s[ref].value or "")
        return "" if v in ("#NAME?", "#N/A", "#VALUE!", "None") else v

    # B118 = TEXTJOIN("/ ", L115, N111, O111) — always rebuild from source cells
    b118_parts = [x for x in [safe("L115"), safe("N111"), safe("O111")] if x]
    b118 = "/ ".join(b118_parts)

    # B46 = TEXTJOIN(", ", C114, IFERROR(O84,""), IFERROR(O85,"")) — rebuild from source
    b46_parts = [x for x in [safe("C114"), safe("O84"), safe("O85")] if x]
    b46 = ", ".join(b46_parts)

    d64 = safe("D64")

    sheet2 = wb2["Sheet2"]
    aa15 = safe("AA15", sheet2)

    # B54 = TEXTJOIN(T99, T100) — rebuild from source cells
    t99  = safe("T99")
    t100 = safe("T100")
    b54_parts = [x for x in [t99, t100] if x]
    b54 = "/ ".join(b54_parts)

    # Win/Draw/Away odds from the model's own computed expected goals (Sheet2!C5, D5)
    lambda_home = sheet2["C5"].value
    lambda_away = sheet2["D5"].value
    odds = calc_odds(lambda_home, lambda_away)
    ou25 = calc_over_under_25(lambda_home, lambda_away)  # NEW — model's own O/U 2.5

    # B119, D119 — show only when not "run"
    b119_raw = safe("B119")
    d119_raw = safe("D119")
    b119 = b119_raw if b119_raw not in ("run", "") else ""
    d119 = d119_raw if d119_raw not in ("run", "") else ""

    # D70
    d70_val = safe("D70")

    # Sheet2!O73 and O74
    o73 = safe("O73", sheet2)
    o74_raw = sheet2["O74"].value
    try:
        o74 = str(round(float(o74_raw), 1)) + "%" if o74_raw is not None else ""
    except:
        o74 = str(o74_raw or "")

    shutil.rmtree(tmp_dir, ignore_errors=True)
    return {"d70": d70, "b120": b120, "c120": c120, "b46": b46, "d64": d64,
            "b118": b118, "aa15": aa15, "b54": b54, "odds": odds, "ou25": ou25,
            "b119": b119, "d119": d119, "d70val": d70_val,
            "o73": o73, "o74": o74}


@app.get("/fixtures")
def fixtures_endpoint(league: str = Query(...), date: str = Query(None)):
    matches = fetch_fixtures(league, date)
    return {"league": league, "matches": matches}


@app.get("/predict")
async def predict(league: str = Query(...), home: str = Query(...), away: str = Query(...)):
    team_data = fetch_stats(league)
    resolved_h = resolve_team(home, team_data)
    resolved_a = resolve_team(away, team_data)
    # If resolution failed, fall back to the raw name for display/model lookup —
    # run_model already returns clean "N/A" values when a name isn't in
    # team_data, so this doesn't risk silently using a wrong team anymore.
    h = resolved_h or home
    a = resolved_a or away

    with ThreadPoolExecutor(max_workers=5) as executor:
        f1 = executor.submit(run_model, h, a, team_data)
        f2 = executor.submit(run_model, a, h, team_data)
        r1, r2 = f1.result(), f2.result()

    # NEW — fetch real bookmaker odds alongside the model's own implied odds.
    # Uses the RAW fixture team names (home/away as given), not the
    # SoccerStats-resolved names — market odds providers use their own team
    # naming and have nothing to do with whether SoccerStats recognized the
    # team, so a stats-resolution failure shouldn't block market odds too.
    # Only leagues in LEAGUE_TO_SPORT_KEY are covered by the primary provider;
    # everything else just gets market_odds: None, which the frontend
    # can treat the same way it already treats missing odds.
    market_odds = None
    market_ou25 = None  # NEW — market Over/Under 2.5 goals odds
    sport_key = LEAGUE_TO_SPORT_KEY.get(league)
    if sport_key:
        market_odds = await get_odds_for_card(sport_key, home, away)
        market_ou25 = await get_ou25_for_card(sport_key, home, away)

    # NEW — if the primary provider had nothing (unmapped league, or no
    # odds posted for this specific match), try Odds-API.io next, then
    # API-Football as a last resort (currently suspended, but kept in
    # case it gets reactivated). Each fails safe to None.
    if not market_odds:
        market_odds = await get_odds_api_io_fallback(home, away)
    if not market_odds:
        market_odds = await get_fallback_odds(home, away)

    # NEW — same fallback chain for O/U 2.5. API-Football's odds endpoint
    # doesn't return totals/O-U markets, so it's not part of this chain —
    # just the primary provider (already tried above) then Odds-API.io.
    if not market_ou25:
        market_ou25 = await get_ou25_api_io_fallback(home, away)

    # NEW — value%: ((market_odd - model_odd) / model_odd) * 100 per side.
    # Positive = market is offering LONGER odds than the model thinks fair
    # (i.e. potential value on that side). Negative = market odds are
    # shorter than the model's fair odds.
    # Computed once here so it's available identically to the live site
    # AND your blog automation script — both just read these fields from
    # the same /predict response, no duplicate logic needed elsewhere.
    value_pct = None
    value_signal = None
    model_odds = r1.get("odds")
    if model_odds and market_odds:
        def pct_diff(market_o, model_o):
            if not model_o or not market_o:
                return None
            return round(((market_o - model_o) / model_o) * 100, 1)

        home_v = pct_diff(market_odds.get("home_odds"), model_odds.get("home_odds"))
        draw_v = pct_diff(market_odds.get("draw_odds"), model_odds.get("draw_odds"))
        away_v = pct_diff(market_odds.get("away_odds"), model_odds.get("away_odds"))

        if home_v is not None or draw_v is not None or away_v is not None:
            total_v = round(sum(x for x in [home_v, draw_v, away_v] if x is not None), 1)

            # Share% — each side's absolute value as a % of the sum of all
            # three absolute values. Mirrors:
            # =ABS(B134)/(ABS($B$134)+ABS($C$134)+ABS($D$134))*100
            abs_sum = abs(home_v or 0) + abs(draw_v or 0) + abs(away_v or 0)

            def share(v):
                if v is None or abs_sum == 0:
                    return None
                return round(abs(v) / abs_sum * 100, 1)

            home_share_v = share(home_v)
            draw_share_v = share(draw_v)
            away_share_v = share(away_v)

            value_pct = {
                "home": home_v, "draw": draw_v, "away": away_v, "total": total_v,
                "home_share": home_share_v, "draw_share": draw_share_v, "away_share": away_share_v,
            }

            # Next step: home_share - away_share (both already absolute-value
            # based, per the share formula above)
            share_diff = None
            if home_share_v is not None and away_share_v is not None:
                share_diff = round(home_share_v - away_share_v, 1)
                value_pct["share_diff"] = share_diff

            # "under" flag — total value falls in the -30 to 0 range,
            # EXCEPT when home_v and away_v are the same sign (both negative
            # or both positive) — in that case "under" is forced empty,
            # since that condition is instead handled by the same-sign
            # override in result_signal below.
            same_sign = home_v is not None and away_v is not None and (
                (home_v < 0 and away_v < 0) or (home_v > 0 and away_v > 0)
            )
            under_flag = "" if same_sign else ("under" if -30 <= total_v <= 0 else "")

            # Signal — decision now based on share_diff (home_share - away_share):
            # positive share_diff -> "away", negative share_diff -> "Home"
            result_signal = ""
            if share_diff is not None and share_diff != 0:
                result_signal = "away" if share_diff > 0 else "Home"

            value_signal = {"under": under_flag, "result": result_signal}

    # NEW — same formula applied to Over/Under 2.5 goals, completely
    # separate from the 1X2 value_pct/value_signal above:
    #   over_v  = (market_over_odd  - model_over_odd)  / model_over_odd  * 100
    #   under_v = (market_under_odd - model_under_odd) / model_under_odd * 100
    # then share% of each (abs value / sum of abs values), then
    # share_diff = over_share - under_share, then decision:
    #   share_diff > 0 -> "Under" (opposite side favored)
    #   share_diff < 0 -> "Over"  (same side favored)
    ou25_value_pct = None
    ou25_value_signal = None
    model_ou25 = r1.get("ou25")
    if model_ou25 and market_ou25:
        def pct_diff_ou(market_o, model_o):
            if not model_o or not market_o:
                return None
            return round(((market_o - model_o) / model_o) * 100, 1)

        over_v = pct_diff_ou(market_ou25.get("over_odds"), model_ou25.get("over_odds"))
        under_v = pct_diff_ou(market_ou25.get("under_odds"), model_ou25.get("under_odds"))

        if over_v is not None or under_v is not None:
            ou_total_v = round(sum(x for x in [over_v, under_v] if x is not None), 1)

            ou_abs_sum = abs(over_v or 0) + abs(under_v or 0)

            def ou_share(v):
                if v is None or ou_abs_sum == 0:
                    return None
                return round(abs(v) / ou_abs_sum * 100, 1)

            over_share_v = ou_share(over_v)
            under_share_v = ou_share(under_v)

            ou25_value_pct = {
                "over": over_v, "under": under_v, "total": ou_total_v,
                "over_share": over_share_v, "under_share": under_share_v,
            }

            ou_share_diff = None
            if over_share_v is not None and under_share_v is not None:
                ou_share_diff = round(over_share_v - under_share_v, 1)
                ou25_value_pct["share_diff"] = ou_share_diff

            ou_result_signal = ""
            if ou_share_diff is not None and ou_share_diff != 0:
                ou_result_signal = "Under" if ou_share_diff > 0 else "Over"

            ou25_value_signal = {"result": ou_result_signal}

    return {
        "home": h, "away": a,
        "d70": r1["d70"], "b120": r1["b120"], "c120": r1["c120"],
        "b46": r1["b46"], "d64": r1["d64"], "b118": r1["b118"], "aa15": r1["aa15"], "b54": r1["b54"],
        "odds": r1.get("odds"),
        "ou25": r1.get("ou25"),  # NEW — model's own Over/Under 2.5 goals odds
        "market_ou25": market_ou25,  # NEW — market Over/Under 2.5 goals odds (primary provider only for now)
        "ou25_value_pct": ou25_value_pct,  # NEW — same formula applied to O/U 2.5
        "ou25_value_signal": ou25_value_signal,  # NEW — Over/Under decision from share_diff
        "market_odds": market_odds,  # NEW
        "value_pct": value_pct,  # NEW — signed % diff between market and model odds, plus total
        "value_signal": value_signal,  # NEW — under flag + home/away handicap-or-team-name signal
        "b119": r1["b119"], "d119": r1["d119"], "d70val": r1["d70val"],
        "o73": r1["o73"], "o74": r1["o74"],
        "d70r": r2["d70"], "b120r": r2["b120"], "c120r": r2["c120"],
        "b46r": r2["b46"], "d64r": r2["d64"], "b118r": r2["b118"], "aa15r": r2["aa15"], "b54r": r2["b54"],
        "oddsr": r2.get("odds"),
        "b119r": r2["b119"], "d119r": r2["d119"], "d70valr": r2["d70val"],
        "o73r": r2["o73"], "o74r": r2["o74"],
    }


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/debug")
def debug(league: str = Query(...), date: str = Query(None)):
    debug_info = {}
    try:
        resp = requests.get(f"{BASE}/homeaway.asp?league={league}", headers=HEADERS, timeout=10)
        debug_info["homeaway_status"] = resp.status_code
        debug_info["homeaway_length"] = len(resp.text)
        debug_info["homeaway_snippet"] = resp.text[:500]
    except Exception as e:
        debug_info["homeaway_error"] = str(e)

    try:
        resp2 = requests.get(f"{BASE}/latest.asp?league={league}", headers=HEADERS, timeout=10)
        debug_info["latest_status"] = resp2.status_code
        debug_info["latest_length"] = len(resp2.text)
    except Exception as e:
        debug_info["latest_error"] = str(e)

    team_data = fetch_stats(league)
    fixtures  = fetch_fixtures(league, date)
    resolved  = [{"home": resolve_team(f["home"], team_data),
                   "away": resolve_team(f["away"], team_data),
                   "raw_home": f["home"], "raw_away": f["away"]} for f in fixtures]
    return {
        "debug_info": debug_info,
        "team_count": len(team_data),
        "team_names": list(team_data.keys()),
        "fixtures": fixtures,
        "resolved": resolved
    }
