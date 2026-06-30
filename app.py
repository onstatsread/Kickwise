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

app = FastAPI(title="Kickwise API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.soccerstats.com/",
    "Connection": "keep-alive",
}
BASE    = "https://www.soccerstats.com"
MODEL   = "A_mix2.xlsx"


def resolve_team(name, team_data):
    if name in team_data:
        return name
    for k in team_data:
        if name in k or k in name:
            return k
    matches = difflib.get_close_matches(name, team_data.keys(), n=1, cutoff=0.6)
    return matches[0] if matches else name


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


def fetch_fixtures(code, date_str=None):
    if date_str:
        today1 = date_str.strip()
    else:
        d = date.today()
        today1 = f"{d.day} {d.strftime('%b')}"

    matches = []
    seen = set()

    try:
        resp = requests.get(f"{BASE}/latest.asp?league={code}",
                            headers=HEADERS, timeout=8)
        soup = BeautifulSoup(resp.text, "html.parser")

        # Pass 1 — collect matches from form history table rows
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
                key = (home, away)
                if key in seen:
                    continue
                seen.add(key)
                # Try to get time from this row's first cell
                t = TIME_RE.search(c0)
                time_str = f"{t.group(1)}:{t.group(2)}" if t else ""
                matches.append({"time": time_str, "home": home, "away": away})

        # Pass 2 — look for times in the upcoming fixtures section at top of page
        # SoccerStats shows upcoming matches in a table with structure:
        # Time | Home - Away | - |
        # These rows contain ONLY a time (HH:MM) in the first cell
        time_map = {}
        for table in soup.find_all("table"):
            for row in table.find_all("tr"):
                cells = row.find_all("td")
                if len(cells) < 3:
                    continue
                c0 = cells[0].get_text(strip=True)
                # First cell should be ONLY a time like "19:45" or "20:00"
                if not re.match(r'^\d{1,2}:\d{2}$', c0):
                    continue
                c_last = cells[-1].get_text(strip=True)
                if c_last != "-":
                    continue
                # Find team names
                for cell in cells[1:]:
                    txt = cell.get_text(" ", strip=True)
                    if " - " in txt and len(txt) < 50:
                        parts = txt.split(" - ", 1)
                        h = clean_team_name(parts[0])
                        a = clean_team_name(parts[1])
                        if h and a and h != a:
                            time_map[(h, a)] = c0
                            time_map[(a, h)] = c0  # also map reversed
                        break

        # Apply times from Pass 2 to matches found in Pass 1
        for m in matches:
            if not m["time"]:
                key = (m["home"], m["away"])
                if key in time_map:
                    m["time"] = time_map[key]
                else:
                    # Fuzzy match — check if any time_map key partially matches
                    for (h, a), t in time_map.items():
                        if (m["home"] in h or h in m["home"]) and                            (m["away"] in a or a in m["away"]):
                            m["time"] = t
                            break

    except Exception as e:
        print(f"  Fixtures error: {e}")

    return matches


def run_model(home, away, team_data):
    if home not in team_data or away not in team_data:
        return {"d70": "N/A", "b120": "N/A", "c120": "N/A", "b46": "N/A", "d64": "N/A", "b118": "N/A", "aa15": "N/A", "b54": "N/A", "odds": None}

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

    shutil.rmtree(tmp_dir, ignore_errors=True)
    return {"d70": d70, "b120": b120, "c120": c120, "b46": b46, "d64": d64,
            "b118": b118, "aa15": aa15, "b54": b54, "odds": odds}


@app.get("/fixtures")
def fixtures_endpoint(league: str = Query(...), date: str = Query(None)):
    matches = fetch_fixtures(league, date)
    return {"league": league, "matches": matches}


@app.get("/predict")
def predict(league: str = Query(...), home: str = Query(...), away: str = Query(...)):
    team_data = fetch_stats(league)
    h = resolve_team(home, team_data)
    a = resolve_team(away, team_data)

    with ThreadPoolExecutor(max_workers=5) as executor:
        f1 = executor.submit(run_model, h, a, team_data)
        f2 = executor.submit(run_model, a, h, team_data)
        r1, r2 = f1.result(), f2.result()

    return {
        "home": h, "away": a,
        "d70": r1["d70"], "b120": r1["b120"], "c120": r1["c120"],
        "b46": r1["b46"], "d64": r1["d64"], "b118": r1["b118"], "aa15": r1["aa15"], "b54": r1["b54"],
        "odds": r1.get("odds"),
        "d70r": r2["d70"], "b120r": r2["b120"], "c120r": r2["c120"],
        "b46r": r2["b46"], "d64r": r2["d64"], "b118r": r2["b118"], "aa15r": r2["aa15"], "b54r": r2["b54"],
        "oddsr": r2.get("odds"),
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
