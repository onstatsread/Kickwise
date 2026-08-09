"""
AnnaBet Parser — One-Shot Build & Check
Parses one league page assuming AnnaBet's tables appear in page order:
table 0 = All Games, table 1 = At Home, table 2 = At Away (based on the
page layout seen earlier). Prints team stats clearly labeled by section
so this can be checked against the real site in a single look, instead
of a separate inspect-then-build round trip.

If the labels come out wrong (e.g. "At Home" numbers don't match what
the real site shows under Home), that tells us the table order
assumption is wrong and needs flipping — but we'll know from ONE run.
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


def parse_table(table):
    """Extract Team name + GP + GF + GA for every row in one table.
    Assumes column order: #, Team, GP, W, T, L, GF, GA, ... (based on
    the page layout seen earlier)."""
    teams = []
    for row in table.find_all("tr"):
        cells = [c.get_text(strip=True) for c in row.find_all("td")]
        if len(cells) < 8:
            continue
        try:
            name = cells[1]
            gp = int(cells[2])
            gf = int(cells[6])
            ga = int(cells[7])
            teams.append({"team": name, "gp": gp, "gf": gf, "ga": ga})
        except (ValueError, IndexError):
            continue
    return teams


def main():
    print(f"🔍 Fetching {TEST_URL}\n")
    resp = SESSION.get(TEST_URL, timeout=20)
    print(f"Status: {resp.status_code}, Length: {len(resp.text)}\n")

    soup = BeautifulSoup(resp.text, "html.parser")
    tables = soup.find_all("table")
    print(f"Found {len(tables)} tables on the page.\n")

    labels = ["ALL GAMES (assumed)", "AT HOME (assumed)", "AT AWAY (assumed)"]

    for i, table in enumerate(tables[:3]):
        label = labels[i] if i < len(labels) else f"TABLE {i}"
        teams = parse_table(table)
        print(f"=== {label} — table index {i}, {len(teams)} teams parsed ===")
        for t in teams[:5]:  # first 5 teams only, enough to eyeball
            print(f"    {t['team']}: GP={t['gp']} GF={t['gf']} GA={t['ga']}")
        print()

    print("👉 Compare the numbers above to annabet.com/en/soccerstats/serie_249_x.html")
    print("   in a browser. If 'AT HOME' numbers here match the real Home table,")
    print("   the table order assumption is correct.")


if __name__ == "__main__":
    main()
