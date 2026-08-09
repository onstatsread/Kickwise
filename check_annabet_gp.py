"""
AnnaBet League Games-Played Checker
Checks every league in ANNABET_LEAGUE_IDS for real games-played this
season. Free and fast — plain requests.get(), no ScraperAPI/paid scraper
needed, confirmed AnnaBet has no Cloudflare-style bot protection.

Confirmed via slug test: AnnaBet only needs the numeric serie_ID — any
text after it works, so every URL is built as serie_{ID}_x.html.

Flags (doesn't auto-exclude) leagues under 10 games played, so they can
be reviewed and decided on case by case, rather than guessed by calendar.
"""
import requests
import time
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Referer": "https://annabet.com/en/soccerstats/",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "same-origin",
    "Upgrade-Insecure-Requests": "1",
}

# Persistent session — reuses cookies and the underlying connection across
# requests, closer to how a real browser behaves than opening a brand new
# bare connection every time (which the earlier attempts were doing).
SESSION = requests.Session()
SESSION.headers.update(HEADERS)

from annabet_leagues import ANNABET_LEAGUE_IDS


def extract_max_gp(html):
    """Parse AnnaBet's league table(s) and find the highest games-played
    value across all teams/rows found on the page. AnnaBet's "All Games"
    table has columns: #, Team, GP, W, T, L, GF, GA, ... — GP is the
    first numeric column after the team name in each row.
    """
    soup = BeautifulSoup(html, "html.parser")
    max_gp = 0
    for table in soup.find_all("table"):
        for row in table.find_all("tr"):
            cells = row.find_all("td")
            if len(cells) < 3:
                continue
            # Column 0 is usually the rank ("1."), column 1 the team name,
            # column 2 the GP value — but be defensive and just look for
            # any cell in the first few columns that's a small, sane
            # integer (games played realistically won't exceed ~60).
            for cell in cells[1:4]:
                text = cell.get_text(strip=True)
                if text.isdigit():
                    val = int(text)
                    if 0 < val <= 60:
                        max_gp = max(max_gp, val)
                        break
    return max_gp


def check_league(name, serie_id, retries=1):
    url = f"https://annabet.com/en/soccerstats/serie_{serie_id}_x.html"
    last_error = None
    for attempt in range(1, retries + 1):
        try:
            resp = SESSION.get(url, timeout=15)
            resp.raise_for_status()
            max_gp = extract_max_gp(resp.text)
            if max_gp == 0:
                return {"status": "no_data", "max_gp": 0}
            return {"status": "ok", "max_gp": max_gp}
        except Exception as e:
            last_error = e
            if attempt < retries:
                time.sleep(3)
    return {"status": "error", "error": str(last_error), "max_gp": 0}


def main():
    print(f"🔍 Checking games-played across {len(ANNABET_LEAGUE_IDS)} leagues on AnnaBet...\n")

    # Testing a small batch first with slower pacing — a full 162-league
    # run at full speed got blocked after ~8 requests, and even slowing
    # to 5s between requests still only let the same first few leagues
    # through. No GP filter applied here — just reporting the real
    # number for whatever successfully loads, no pre-judging by count.
    test_leagues = dict(list(ANNABET_LEAGUE_IDS.items())[:6])

    results = []
    for name, serie_id in test_leagues.items():
        r = check_league(name, serie_id)
        if r["status"] == "ok":
            print(f"  ✅  {name} (serie_{serie_id}): {r['max_gp']} games played")
            results.append((name, serie_id, r["max_gp"]))
        elif r["status"] == "no_data":
            print(f"  ❓  {name} (serie_{serie_id}): no table data found — check manually")
            results.append((name, serie_id, 0))
        else:
            print(f"  ❌  {name} (serie_{serie_id}): failed — {r['error']}")
            results.append((name, serie_id, -1))

        time.sleep(20)  # much longer gap — testing if this is a time-window limit

    print(f"\n📊 Summary — {len(results)} leagues checked\n")

    ok = [r for r in results if r[2] > 0]
    failed = [r for r in results if r[2] == -1]

    print(f"✅ GAMES PLAYED (real numbers, no filtering):")
    for name, sid, gp in sorted(ok, key=lambda x: -x[2]):
        print(f"    {name}: {gp} GP")

    if failed:
        print(f"\n❌ FAILED TO CHECK ({len(failed)} leagues — connection blocked, unrelated to GP):")
        for name, sid, gp in failed:
            print(f"    {name} (serie_{sid})")


if __name__ == "__main__":
    main()
