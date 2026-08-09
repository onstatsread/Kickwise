"""
AnnaBet Upcoming Fixtures Parser
Targets the "Upcoming Games" data — date header rows (e.g. "09.08.2026")
followed by team1 - team2 rows with NO score yet (just odds: 1/X/2).
This is likely sitting in the same big results/fixtures table we found
earlier (~100+ rows) — just wasn't recognized before because it has no
score digits and no HH:MM time, only a date header per group.
"""
import requests
from bs4 import BeautifulSoup
import re

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

TEST_URL = "https://annabet.com/en/soccerstats/serie_36_x.html"  # Norway Eliteserien

DATE_RE = re.compile(r'\b(\d{2})\.(\d{2})\.(\d{4})\b')  # DD.MM.YYYY
ODDS_RE = re.compile(r'\b\d+\.\d{2}\b')  # decimal odds like 5.20


def main():
    print(f"🔍 Fetching {TEST_URL}\n")
    resp = SESSION.get(TEST_URL, timeout=20)
    print(f"Status: {resp.status_code}, Length: {len(resp.text)}\n")

    soup = BeautifulSoup(resp.text, "html.parser")
    tables = soup.find_all("table")

    for i, table in enumerate(tables):
        rows = table.find_all("tr")
        current_date = None
        fixtures_found = []

        for row in rows:
            row_text = row.get_text(" ", strip=True)

            # Is this a date header row?
            date_match = DATE_RE.search(row_text)
            cells = row.find_all("td")

            # Date header rows are usually short (just the date, maybe
            # spanning the row) — check if this row is JUST a date
            if date_match and len(row_text) < 20:
                current_date = date_match.group(0)
                continue

            # Look for a row with exactly 2 team links and odds, no score
            links = row.find_all("a")
            if len(links) == 2 and current_date:
                team1 = links[0].get_text(strip=True)
                team2 = links[1].get_text(strip=True)
                odds = ODDS_RE.findall(row_text)
                # No digit-digit score should appear between team names —
                # if there's a real score, SCORE pattern like "2 - 1"
                # (single/double digit, not decimal) would show up instead
                has_score = re.search(r'\b\d{1,2}\s*-\s*\d{1,2}\b(?!\d)', row_text.replace(".", ""))
                if odds and (team1 == "Kristiansund" or team2 == "Kristiansund" or team1 == "Molde FK" or team2 == "Molde FK"):
                    fixtures_found.append({
                        "table": i, "date": current_date,
                        "team1": team1, "team2": team2,
                        "odds": odds[:3], "raw": row_text[:150]
                    })

        if fixtures_found:
            print(f"=== TABLE #{i} — found {len(fixtures_found)} Kristiansund/Molde row(s) ===")
            for f in fixtures_found:
                print(f"  Date: {f['date']}, {f['team1']} vs {f['team2']}, Odds: {f['odds']}")
                print(f"  Raw: {f['raw']}")
            print()


if __name__ == "__main__":
    main()
