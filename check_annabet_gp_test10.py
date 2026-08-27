"""
AnnaBet League Games-Played Checker — SMALL TEST BATCH (10 leagues)
Same corrected header-matching logic as the full check_annabet_gp.py,
but limited to 10 hand-picked leagues so this finishes in a few minutes
instead of ~40. Use this to confirm the fix is accurate before running
the full 162-league batch again.

The 10 chosen, and why:
  - Algeria           -> already confirmed correct (30 GP) via /debug_serie_gp
  - Armenia           -> already confirmed the OLD script was wrong here
                          (reported 23, real value is 4) — sanity check
                          that the fix now gets this right
  - South Korea K1     -> already in production (LEAGUE_CODES) — cross-check
                          against what the live backend currently shows
  - Brazil Serie A     -> already in production — cross-check
  - Malaysia           -> already in production but flagged as possibly
                          below threshold now — confirm real current GP
  - Argentina          -> untested top-40, Apertura calendar (Aug start)
  - England Premier L. -> untested top-40, just started (expect low GP)
  - Denmark Superligaen-> new candidate, has market-odds coverage already
  - Turkey Super Lig   -> new candidate, has market-odds coverage already
  - Mexico Liga MX     -> new candidate, Apertura calendar (Jul start)
"""
import requests
import time
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate",
    "Connection": "keep-alive",
    "Referer": "https://annabet.com/en/soccerstats/",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "same-origin",
    "Upgrade-Insecure-Requests": "1",
}

SESSION = requests.Session()
SESSION.headers.update(HEADERS)

# Hand-picked 10-league test set — name: serie_id
TEST_BATCH = {
    "Algeria - Ligue 1": 257,
    "Armenia - Premier League": 351,
    "South Korea - K League 1": 249,
    "Brazil - Serie A": 217,
    "Malaysia - Super League": 521,
    "Argentina - Primera Division": 212,
    "England - Premier League": 1,
    "Denmark - Superligaen": 38,
    "Turkey - Super Lig": 26,
    "Mexico - Liga MX": 334,
}

ANNABET_TABLE_HEADER = ['#', 'Team', 'GP', 'W', 'T', 'L', 'GF', 'GA', 'Diff', 'Pts', 'Pts/G', 'W%', 'ØGF', 'ØGA']


def _get_header(table):
    rows = table.find_all("tr")
    if not rows:
        return None
    return [c.get_text(strip=True) for c in rows[0].find_all(["td", "th"])]


def _max_gp_from_table(table):
    max_gp = 0
    for row in table.find_all("tr")[1:]:
        cells = [c.get_text(strip=True) for c in row.find_all("td")]
        if len(cells) < 3:
            continue
        try:
            gp = int(cells[2])
            max_gp = max(max_gp, gp)
        except (ValueError, IndexError):
            continue
    return max_gp


def extract_max_gp(html):
    soup = BeautifulSoup(html, "html.parser")
    tables = soup.find_all("table")
    matching = [t for t in tables if _get_header(t) == ANNABET_TABLE_HEADER]
    if not matching:
        return 0
    return _max_gp_from_table(matching[0])


def check_league(name, serie_id, retries=1):
    url = f"https://annabet.com/en/soccerstats/serie_{serie_id}_x.html"
    last_error = None
    for attempt in range(1, retries + 1):
        try:
            resp = SESSION.get(url, timeout=15)
            resp.raise_for_status()
            max_gp = extract_max_gp(resp.text)
            if max_gp == 0:
                snippet = resp.text[:300].replace("\n", " ")
                return {"status": "no_data", "max_gp": 0,
                        "debug": f"status={resp.status_code} len={len(resp.text)} url={resp.url} snippet={snippet}"}
            return {"status": "ok", "max_gp": max_gp}
        except Exception as e:
            last_error = e
            if attempt < retries:
                time.sleep(3)
    return {"status": "error", "error": str(last_error), "max_gp": 0}


def main():
    print(f"🔍 TEST BATCH — checking {len(TEST_BATCH)} hand-picked leagues on AnnaBet...\n")

    for name, serie_id in TEST_BATCH.items():
        r = check_league(name, serie_id)
        if r["status"] == "ok":
            print(f"  ✅  {name} (serie_{serie_id}): {r['max_gp']} games played")
        elif r["status"] == "no_data":
            print(f"  ❓  {name} (serie_{serie_id}): no matching table found")
            print(f"      DEBUG: {r.get('debug', 'n/a')}")
        else:
            print(f"  ❌  {name} (serie_{serie_id}): failed — {r['error']}")

        time.sleep(20)

    print("\n✅ Test batch complete.")


if __name__ == "__main__":
    main()
