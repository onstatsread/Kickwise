"""
AnnaBet Real Parser — Verification Run
Finds the season-long All Games / At Home / At Away tables reliably,
without relying on fixed table index numbers (which shift per league
depending on how many cup-competition tables appear before them).

Method: scan all tables for the exact header signature
['#','Team','GP','W','T','L','GF','GA','Diff','Pts','Pts/G','W%','ØGF','ØGA'].
Group consecutive matches into runs of 3. Take the FIRST such run — this
is reliably the season stats (the "Last 6 Games" section, which shares
the same header, always appears later on the page).
"""
import requests
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

TEST_URL = "https://annabet.com/en/soccerstats/serie_249_x.html"  # K League 1

EXPECTED_HEADER = ['#', 'Team', 'GP', 'W', 'T', 'L', 'GF', 'GA', 'Diff', 'Pts', 'Pts/G', 'W%', 'ØGF', 'ØGA']


def get_header(table):
    rows = table.find_all("tr")
    if not rows:
        return None
    return [c.get_text(strip=True) for c in rows[0].find_all(["td", "th"])]


def parse_table(table):
    teams = {}
    for row in table.find_all("tr")[1:]:  # skip header row
        cells = [c.get_text(strip=True) for c in row.find_all("td")]
        if len(cells) < 8:
            continue
        try:
            name = cells[1]
            gp = int(cells[2])
            gf = int(cells[6])
            ga = int(cells[7])
            teams[name] = {"gp": gp, "gf": gf, "ga": ga}
        except (ValueError, IndexError):
            continue
    return teams


def find_season_tables(soup):
    """Returns (all_games_table, home_table, away_table) or (None, None, None)."""
    tables = soup.find_all("table")
    matching = [t for t in tables if get_header(t) == EXPECTED_HEADER]
    if len(matching) < 3:
        return None, None, None
    return matching[0], matching[1], matching[2]


def main():
    print(f"🔍 Fetching {TEST_URL}\n")
    resp = SESSION.get(TEST_URL, timeout=20)
    print(f"Status: {resp.status_code}, Length: {len(resp.text)}\n")

    soup = BeautifulSoup(resp.text, "html.parser")
    all_table, home_table, away_table = find_season_tables(soup)

    if not all_table:
        print("❌ Could not find 3 matching tables — structure may differ from expected")
        return

    all_data = parse_table(all_table)
    home_data = parse_table(home_table)
    away_data = parse_table(away_table)

    print(f"✅ Found season tables: All={len(all_data)} teams, Home={len(home_data)} teams, Away={len(away_data)} teams\n")

    print("--- Verification: Home GP + Away GP should equal All GP, per team ---")
    all_match = True
    for team in all_data:
        all_gp = all_data[team]["gp"]
        home_gp = home_data.get(team, {}).get("gp", "?")
        away_gp = away_data.get(team, {}).get("gp", "?")
        ok = (isinstance(home_gp, int) and isinstance(away_gp, int) and home_gp + away_gp == all_gp)
        if not ok:
            all_match = False
        flag = "✅" if ok else "❌"
        print(f"  {flag} {team}: All GP={all_gp}, Home GP={home_gp}, Away GP={away_gp}")

    print(f"\n{'✅ ALL TEAMS MATCH — parser is correct!' if all_match else '❌ MISMATCH FOUND — parser needs adjustment'}")


if __name__ == "__main__":
    main()
