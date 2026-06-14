"""
Kickwise Backend — FastAPI server
Deploy to Render.com (free tier) — see README
"""
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import requests, os, subprocess, statistics, tempfile, shutil
from datetime import date
from bs4 import BeautifulSoup
from openpyxl import load_workbook

app = FastAPI(title="Kickwise API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)

HEADERS  = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
BASE     = "https://www.soccerstats.com"
MODEL    = "A_mix2.xlsx"   # must be in same folder as app.py

# ── Fetch team stats ───────────────────────────────────────────
def fetch_stats(code):
    teams = {}
    try:
        soup = BeautifulSoup(
            requests.get(f"{BASE}/latest.asp?league={code}", headers=HEADERS, timeout=15).text,
            "html.parser")
        for tbl in soup.find_all("table"):
            for row in tbl.find_all("tr"):
                cells = row.find_all("td")
                if len(cells) < 5: continue
                name = cells[0].get_text(strip=True)
                if not name or name.isdigit(): continue
                nums = []
                for c in cells[1:]:
                    try: nums.append(float(c.get_text(strip=True).replace(",",".")))
                    except: nums.append(None)
                if len(nums) >= 3 and nums[0] and nums[0] > 0:
                    gp = nums[0]
                    gf = (nums[1] or 0)/gp
                    ga = (nums[2] or 0)/gp
                    teams[name] = {"gp":gp,"gf":gf,"ga":ga,"tot":gf+ga,
                                   "hgf":gf,"hga":ga,"htot":gf+ga,
                                   "agf":gf,"aga":ga,"atot":gf+ga}
    except: pass

    for section in ["home","away"]:
        try:
            s2 = BeautifulSoup(
                requests.get(f"{BASE}/table.asp?league={code}&tid={section}", headers=HEADERS, timeout=10).text,
                "html.parser")
            for tbl in s2.find_all("table"):
                for row in tbl.find_all("tr"):
                    cells = row.find_all("td")
                    if len(cells) < 4: continue
                    name = cells[0].get_text(strip=True)
                    if name not in teams: continue
                    nums2 = []
                    for c in cells[1:]:
                        try: nums2.append(float(c.get_text(strip=True).replace(",",".")))
                        except: nums2.append(None)
                    if len(nums2) >= 3 and nums2[0]:
                        gp2 = nums2[0]
                        gf2 = (nums2[1] or 0)/gp2
                        ga2 = (nums2[2] or 0)/gp2
                        if section == "home":
                            teams[name].update({"hgf":gf2,"hga":ga2,"htot":gf2+ga2})
                        else:
                            teams[name].update({"agf":gf2,"aga":ga2,"atot":gf2+ga2})
        except: pass
    return teams

# ── Fetch today's fixtures ─────────────────────────────────────
def fetch_fixtures(code, date_str=None):
    if date_str:
        today1 = date_str
        today2 = date_str
    else:
        today1 = f"{date.today().day} {date.today().strftime('%b')}"
        today2 = date.today().strftime("%d %b")                              # "13 Jun" (zero-padded)
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

# ── Run the A_mix2 model ───────────────────────────────────────
def run_model(home, away, team_data):
    if home not in team_data or away not in team_data:
        return {"d70":"N/A","b120":"N/A","c120":"N/A"}

    data = sorted([
        (n,d["gp"],d["gf"],d["ga"],d["tot"],
         d["hgf"],d["hga"],d["htot"],d["agf"],d["aga"],d["atot"])
        for n,d in team_data.items()], key=lambda x: x[0])

    lhs = statistics.mean([d[5] for d in data]) or 1
    lhc = statistics.mean([d[6] for d in data]) or 1
    las = statistics.mean([d[8] for d in data]) or 1
    lac = statistics.mean([d[9] for d in data]) or 1

    wb = load_workbook(MODEL)
    ws = wb.active
    for row in ws.iter_rows(min_row=6,max_row=42,min_col=3,max_col=22):
        for cell in row: cell.value = None

    for i,d in enumerate(data):
        r=6+i; hs,hc,ht=d[5],d[6],d[7]; as_,ac,at_=d[8],d[9],d[10]
        ws.cell(r,3).value=d[0];  ws.cell(r,4).value=d[1]
        ws.cell(r,5).value=round(d[2],4); ws.cell(r,6).value=round(d[3],4)
        ws.cell(r,7).value=round(d[4],4); ws.cell(r,8).value="  "
        ws.cell(r,9).value=round(hs,4);   ws.cell(r,10).value=round(hc,4)
        ws.cell(r,11).value=round(ht,4);  ws.cell(r,12).value="  "
        ws.cell(r,13).value=round(as_,4); ws.cell(r,14).value=round(ac,4)
        ws.cell(r,15).value=round(at_,4); ws.cell(r,16).value=round(hs/lhs,4)
        ws.cell(r,17).value=round(hc/lhc,4); ws.cell(r,18).value=round(as_/las,4)
        ws.cell(r,19).value=round(ac/lac,4)
        ws.cell(r,20).value=round(max((hs-as_)/d[1],0),4)
        ws.cell(r,22).value=round((ht+at_)/2,4)

    ws["B69"]=home; ws["C69"]=away; ws.title="Sheet1"

    tmp_dir  = tempfile.mkdtemp()
    tmp_file = os.path.join(tmp_dir, "fm_tmp.xlsx")
    out_dir  = os.path.join(tmp_dir, "out")
    os.makedirs(out_dir)
    wb.save(tmp_file)

    subprocess.run(["libreoffice","--headless","--calc","--convert-to","xlsx",
                    "--outdir",out_dir, tmp_file],
                   capture_output=True, timeout=90)

    out_file = os.path.join(out_dir, "fm_tmp.xlsx")
    wb2 = load_workbook(out_file, data_only=True)
    ws2 = wb2.active

    d70  = str(ws2["D70"].value  or "")
    b120 = str(ws2["B120"].value or "")
    c120 = str(ws2["C120"].value or "")

    if b120 in ("#NAME?","#N/A","None",""):
        parts = [x for x in [str(ws2["B119"].value or ""),
                              str(ws2["C119"].value or ""),
                              str(ws2["D119"].value or "")]
                 if x and x not in ("run","#NAME?","#N/A","None")]
        b120 = " /".join(parts)

    shutil.rmtree(tmp_dir, ignore_errors=True)
    return {"d70":d70, "b120":b120, "c120":c120}

# ── API endpoint ───────────────────────────────────────────────
from concurrent.futures import ThreadPoolExecutor

@app.get("/analyze")
def analyze(league: str = Query(...), date: str = Query(None)):
    team_data = fetch_stats(league)
    fixtures  = fetch_fixtures(league, date)

    def process(fix):
        home, away = fix["home"], fix["away"]
        with ThreadPoolExecutor(max_workers=2) as inner:
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

    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(process, fixtures))

    return {"league": league, "matches": results}
@app.get("/health")
def health():
    return {"status": "ok", "date": str(date.today())}
