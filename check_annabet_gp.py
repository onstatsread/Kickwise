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
}

from annabet_leagues import ANNABET_LEAGUE_IDS

MIN_GP_THRESHOLD = 10


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


def check_league(name, serie_id, retries=2):
    url = f"https://annabet.com/en/soccerstats/serie_{serie_id}_x.html"
    last_error = None
    for attempt in range(1, retries + 1):
        try:
            resp = requests.get(url, headers=HEADERS, timeout=20)
            resp.raise_for_status()
            max_gp = extract_max_gp(resp.text)
            if max_gp == 0:
                return {"status": "no_data", "max_gp": 0}
            return {"status": "ok", "max_gp": max_gp}
        except Exception as e:
            last_error = e
            if attempt < retries:
                time.sleep(2)
    return {"status": "error", "error": str(last_error), "max_gp": 0}


def main():
    print(f"🔍 Checking games-played across {len(ANNABET_LEAGUE_IDS)} leagues on AnnaBet...\n")

    results = []
    for name, serie_id in ANNABET_LEAGUE_IDS.items():
        r = check_league(name, serie_id)
        if r["status"] == "ok":
            flag = "✅" if r["max_gp"] >= MIN_GP_THRESHOLD else "⚠️ THIN"
            print(f"  {flag}  {name} (serie_{serie_id}): {r['max_gp']} games played")
            results.append((name, serie_id, r["max_gp"]))
        elif r["status"] == "no_data":
            print(f"  ❓  {name} (serie_{serie_id}): no table data found — check manually")
            results.append((name, serie_id, 0))
        else:
            print(f"  ❌  {name} (serie_{serie_id}): failed — {r['error']}")
            results.append((name, serie_id, -1))

        time.sleep(0.3)  # light pause, be reasonable even though it's free

    print(f"\n📊 Summary — {len(results)} leagues checked\n")

    keep = [r for r in results if r[2] >= MIN_GP_THRESHOLD]
    thin = [r for r in results if 0 <= r[2] < MIN_GP_THRESHOLD]
    failed = [r for r in results if r[2] == -1]

    print(f"✅ READY ({len(keep)} leagues, >= {MIN_GP_THRESHOLD} games played):")
    for name, sid, gp in sorted(keep, key=lambda x: -x[2]):
        print(f"    {name}: {gp} GP")

    print(f"\n⚠️ THIN — noted, not excluded ({len(thin)} leagues, < {MIN_GP_THRESHOLD} games played):")
    for name, sid, gp in sorted(thin, key=lambda x: -x[2]):
        print(f"    {name}: {gp} GP")

    if failed:
        print(f"\n❌ FAILED TO CHECK ({len(failed)} leagues):")
        for name, sid, gp in failed:
            print(f"    {name} (serie_{sid})")


if __name__ == "__main__":
    main()
