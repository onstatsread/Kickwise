"""
AnnaBet Table Structure Verification
Fetches ONE league page and prints out exactly what gets extracted from
each of the three tables (All Games / At Home / At Away), per team.

Purpose: confirm the parsing logic correctly distinguishes home-specific
and away-specific stats (not just any table on the page) BEFORE wiring
this into fetch_stats() for real predictions. Cross-check the printed
numbers against annabet.com/en/soccerstats/serie_249_x.html in a browser
to confirm they match.
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


def describe_table(table, index):
    """Print every row of a table with column positions labeled, so we
    can visually match columns to what the real site shows (Team, GP, W,
    T, L, GF, GA, ...)."""
    rows = table.find_all("tr")
    print(f"\n--- TABLE #{index} ({len(rows)} rows) ---")

    # Try to find a heading a few elements before this table, to help
    # identify which of the three (All/Home/Away) this is
    heading = None
    prev = table.find_previous(["h1", "h2", "h3", "h4", "a", "span", "div"])
    hops = 0
    while prev and hops < 5:
        text = prev.get_text(strip=True)
        if text and len(text) < 40:
            heading = text
            break
        prev = prev.find_previous(["h1", "h2", "h3", "h4", "a", "span", "div"])
        hops += 1
    print(f"Nearest preceding label: {heading!r}")

    for i, row in enumerate(rows[:5]):  # just first 5 rows per table
        cells = [c.get_text(strip=True) for c in row.find_all(["td", "th"])]
        print(f"  Row {i}: {cells}")


def main():
    print(f"🔍 Fetching {TEST_URL}\n")
    resp = SESSION.get(TEST_URL, timeout=20)
    print(f"Status: {resp.status_code}, Length: {len(resp.text)}\n")

    soup = BeautifulSoup(resp.text, "html.parser")
    tables = soup.find_all("table")
    print(f"Found {len(tables)} <table> elements on the page.")

    for i, table in enumerate(tables):
        describe_table(table, i)


if __name__ == "__main__":
    main()

