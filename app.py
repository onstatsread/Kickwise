"""
Kickwise Backend — FastAPI server
Deploy to Render.com (free tier)
"""
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
import requests, os, subprocess, statistics, tempfile, shutil, difflib, re
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
SCORE_RE = re.compile(r'\d+\s*[:\-]\s*\d+')
DAY_RE   = re.compile(r'^(Mon|Tue|Wed|Thu|Fri|Sat|Sun)\b\s*')


def clean_team_name(name):
    name = DAY_RE.sub("", name).strip()
    # Cut off at first score-like pattern or long number sequence (stats junk)
    m = SCORE_RE.search(name)
    if m:
        name = name[:m.start()].strip()
    return name


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

        for table in soup.find_all("table"):
            for row in table.find_all("tr"):
                cells = row.find_all("td")
                if len(cells) < 2:
                    continue

                # Date must be in the FIRST cell only (not anywhere in the row,
                # to avoid matching unrelated stats text elsewhere in the row)
                first_cell_text = cells[0].get_text(" ", strip=True)
                if today1 not in first_cell_text:
                    continue
                # Reject if first cell is way too long (means it's not a clean date cell)
                if len(first_cell_text) > 20:
                    continue

                # Score must be unplayed "-"
                score_cell = cells[-1].get_text(strip=True)
                if score_cell != "-":
                    continue

                # Match teams should be in cells[1], format "TeamA - TeamB"
                if len(cells) < 2:
                    continue
                match_cell_text = cells[1].get_text(" ", strip=True)
                if " - " not in match_cell_text:
                    continue
                # Reject overly long match cell (means stats junk got merged in)
                if len(match_cell_text) > 60:
                    continue

                parts = match_cell_text.split(" - ", 1)
                home = clean_team_name(parts[0])
                away = clean_team_name(parts[1])

                if not home or not away or home == away:
                    continue
                if len(home) < 2 or len(away) < 2 or len(home) > 30 or len(away) > 30:
                    continue

                key = (home, away)
                if key in seen:
                    continue
                seen.add(key)

                t = TIME_RE.search(first_cell_text)
                time_str = f"{t.group(1)}:{t.group(2)}" if t else ""
                matches.append({"time": time_str, "home": home, "away": away})

        # Fallback: team-link based detection (only if Method 1 found nothing)
        if not matches:
            for row in soup.find_all("tr"):
                cells = row.find_all("td")
                if len(cells) < 2 or len(cells) > 6:
                    continue
                first_cell_text = cells[0].get_text(" ", strip=True)
                if today1 not in first_cell_text or len(first_cell_text) > 20:
                    continue
                score_cell = cells[-1].get_text(strip=True)
                if score_cell != "-":
                    continue
                team_links = [a.get_text(strip=True) for a in row.find_all("a")
                              if "team=" in (a.get("href") or "")]
                if len(team_links) >= 2:
                    home, away = clean_team_name(team_links[0]), clean_team_name(team_links[1])
                    if home and away and home != away:
                        key = (home, away)
                        if key not in seen:
                            seen.add(key)
                            t = TIME_RE.search(first_cell_text)
                            time_str = f"{t.group(1)}:{t.group(2)}" if t else ""
                            matches.append({"time": time_str, "home": home, "away": away})

    except Exception as e:
        print(f"  Fixtures error: {e}")

    return matches


def run_model(home, away, team_data):
    if home not in team_data or away not in team_data:
        return {"d70": "N/A", "b120": "N/A", "c120": "N/A"}

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

    d70  = str(ws2["D70"].value  or "")
    b120 = str(ws2["B120"].value or "")
    c120 = str(ws2["C120"].value or "")

    if b120 in ("#NAME?", "#N/A", "None", ""):
        parts = [x for x in [str(ws2["B119"].value or ""),
                              str(ws2["C119"].value or ""),
                              str(ws2["D119"].value or "")]
                 if x and x not in ("run", "#NAME?", "#N/A", "None")]
        b120 = " /".join(parts)

    b46 = str(ws2["B46"].value or "")
    d64 = str(ws2["D64"].value or "")

    if b46 in ("#NAME?", "#N/A", "None", ""):
        parts = [x for x in [str(ws2["C114"].value or ""),
                              str(ws2["O84"].value or ""),
                              str(ws2["O85"].value or "")]
                 if x and x not in ("#NAME?", "#N/A", "None")]
        b46 = ", ".join(parts)

    shutil.rmtree(tmp_dir, ignore_errors=True)
    return {"d70": d70, "b120": b120, "c120": c120, "b46": b46, "d64": d64}


@app.get("/fixtures")
def fixtures_endpoint(league: str = Query(...), date: str = Query(None)):
    matches = fetch_fixtures(league, date)
    return {"league": league, "matches": matches}


@app.get("/predict")
def predict(league: str = Query(...), home: str = Query(...), away: str = Query(...)):
    team_data = fetch_stats(league)
    h = resolve_team(home, team_data)
    a = resolve_team(away, team_data)

    with ThreadPoolExecutor(max_workers=3) as executor:
        f1 = executor.submit(run_model, h, a, team_data)
        f2 = executor.submit(run_model, a, h, team_data)
        r1, r2 = f1.result(), f2.result()

    return {
        "home": h, "away": a,
        "d70": r1["d70"], "b120": r1["b120"], "c120": r1["c120"],
        "b46": r1["b46"], "d64": r1["d64"],
        "d70r": r2["d70"], "b120r": r2["b120"], "c120r": r2["c120"],
        "b46r": r2["b46"], "d64r": r2["d64"],
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
