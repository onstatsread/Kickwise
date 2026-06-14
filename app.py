"""
Kickwise Backend — FastAPI server
Deploy to Render.com (free tier)
"""
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
import requests, os, subprocess, statistics, tempfile, shutil, difflib
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

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
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


def fetch_fixtures(code, date_str=None):
    if date_str:
        today1 = date_str
        today2 = date_str
    else:
        today1 = f"{date.today().day} {date.today().strftime('%b')}"
        today2 = date.today().strftime("%d %b")

    matches = []
    seen = set()
    try:
        soup = BeautifulSoup(
            requests.get(f"{BASE}/latest.asp?league={code}", headers=HEADERS, timeout=15).text,
            "html.parser")
        for table in soup.find_all("table"):
            for row in table.find_all("tr"):
                cells = row.find_all("td")
                if len(cells) < 3:
                    continue
                date_text = cells[0].get_text(strip=True)
                if date_text not in (today1, today2):
                    continue
                score_text = cells[-1].get_text(strip=True)
                if score_text != "-":
                    continue
                middle = cells[1].get_text(" ", strip=True)
                if " - " in middle:
                    home, away = middle.split(" - ", 1)
                    home, away = home.strip(), away.strip()
                    key = (home, away)
                    if key not in seen:
                        seen.add(key)
                        matches.append({"time": "", "home": home, "away": away})
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

    shutil.rmtree(tmp_dir, ignore_errors=True)
    return {"d70": d70, "b120": b120, "c120": c120}


@app.get("/analyze")
def analyze(league: str = Query(...), date: str = Query(None)):
    team_data = fetch_stats(league)
    fixtures  = fetch_fixtures(league, date)

    def process(fix):
        home = resolve_team(fix["home"], team_data)
        away = resolve_team(fix["away"], team_data)
        with ThreadPoolExecutor(max_workers=1) as inner:
            f1 = inner.submit(run_model, home, away, team_data)
            f2 = inner.submit(run_model, away, home, team_data)
            r1, r2 = f1.result(), f2.result()
        return {
            "time":  fix["time"],
            "home":  home,
            "away":  away,
            "d70":   r1["d70"],
            "b120":  r1["b120"],
            "c120":  r1["c120"],
            "d70r":  r2["d70"],
            "b120r": r2["b120"],
            "c120r": r2["c120"],
        }

    with ThreadPoolExecutor(max_workers=1) as executor:
        results = list(executor.map(process, fixtures))

    return {"league": league, "matches": results}


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/debug")
def debug(league: str = Query(...), date: str = Query(None)):
    team_data = fetch_stats(league)
    fixtures  = fetch_fixtures(league, date)
    resolved  = [{"home": resolve_team(f["home"], team_data),
                   "away": resolve_team(f["away"], team_data),
                   "raw_home": f["home"], "raw_away": f["away"]} for f in fixtures]
    return {
        "team_count": len(team_data),
        "team_names": list(team_data.keys()),
        "fixtures": fixtures,
        "resolved": resolved
    }
    LEAGUE_CODES = {
    "Australia - NPL NSW": "australia11",
    "Australia - NPL Queensland": "australia4",
    "Australia - NPL Victoria": "australia3",
    "Australia - NPL W. Australia": "australia5",
    "Australia - Victoria Premier": "australia13",
    "Austria - Bundesliga": "austria",
    "Austria - 2. Liga": "austria2",
    "Belarus - Vysshaya Liga": "belarus",
    "Belgium - Pro League": "belgium",
    "Belgium - Challenger Pro": "belgium2",
    "Brazil - Serie A": "brazil",
    "Bulgaria - Parva Liga": "bulgaria",
    "China - Super League": "china",
    "China - League One": "china2",
    "Colombia - Primera A": "colombia",
    "Croatia - 1. HNL": "croatia",
    "Czech Republic - 1. Liga": "czechrepublic",
    "Czech Republic - FNL": "czechrepublic2",
    "Denmark - Superligaen": "denmark",
    "Ecuador - Liga Pro": "ecuador",
    "England - Championship": "england2",
    "England - League One": "england3",
    "England - League Two": "england4",
    "England - Premier League": "england",
    "Faroe Islands - Premier": "faroeislands",
    "Finland - Veikkausliiga": "finland",
    "France - Ligue 1": "france",
    "France - Ligue 2": "france2",
    "France - National": "france3",
    "Germany - 2. Bundesliga": "germany2",
    "Germany - 3. Liga": "germany3",
    "Germany - Bundesliga": "germany",
    "Greece - Super League": "greece",
    "Hungary - NB I": "hungary",
    "Ireland - First Division": "ireland2",
    "Ireland - Premier Division": "ireland",
    "Italy - Serie A": "italy",
    "Italy - Serie B": "italy2",
    "Latvia - Virsliga": "latvia",
    "Lithuania - A Lyga": "lithuania",
    "Netherlands - Eerste Divisie": "netherlands2",
    "Netherlands - Eredivisie": "netherlands",
    "North Macedonia - First League": "northmacedonia",
    "Northern Ireland - NIFL": "northernireland",
    "Norway - Eliteserien": "norway",
    "Poland - 1. Liga": "poland2",
    "Poland - Ekstraklasa": "poland",
    "Portugal - Liga Portugal": "portugal",
    "Portugal - Liga Portugal 2": "portugal2",
    "Romania - Liga 1": "romania",
    "Russia - FNL": "russia2",
    "Russia - Premier League": "russia",
    "Scotland - Championship": "scotland2",
    "Scotland - Premiership": "scotland",
    "Serbia - Super Liga": "serbia",
    "Slovakia - 1. Liga": "slovakia",
    "Slovenia - Prva Liga": "slovenia",
    "South Africa - Premier Division": "southafrica",
    "South Korea - K League 1": "southkorea",
    "Spain - La Liga": "spain",
    "Spain - La Liga 2": "spain2",
    "Sweden - Allsvenskan": "sweden",
    "Switzerland - Super League": "switzerland",
    "Turkey - Super Lig": "turkey",
    "Ukraine - Premier League": "ukraine",
    "Wales - Cymru Premier": "wales",
}


@app.get("/leagues_today")
def leagues_today(date: str = Query(None)):
    def check(item):
        name, code = item
        try:
            fixtures = fetch_fixtures(code, date)
        except:
            fixtures = []
        return {"name": name, "code": code, "count": len(fixtures)} if fixtures else None

    with ThreadPoolExecutor(max_workers=10) as executor:
        results = list(executor.map(check, LEAGUE_CODES.items()))

    available = [r for r in results if r]
    available.sort(key=lambda x: x["name"])
    return {"leagues": available}
