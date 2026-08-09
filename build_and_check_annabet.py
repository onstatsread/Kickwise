"""
AnnaBet Fixtures Structure Check
Looks for upcoming (not-yet-played) matches on a league page — need to
confirm how AnnaBet marks these (kickoff time vs a final score, some
"vs" or "-" placeholder, date format, etc.) before building a real
fetch_fixtures() parser around it.
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

TEST_URL = "https://annabet.com/en/soccerstats/serie_249_x.html"  # K League 1

# Look for time-like patterns (HH:MM) which would indicate an upcoming
# match listing (kickoff time) rather than a final score (X-Y)
TIME_RE = re.compile(r'\b([01]?\d|2[0-3]):([0-5]\d)\b')
SCORE_RE = re.compile(r'\b(\d+)\s*[-:]\s*(\d+)\b')


def main():
    print(f"🔍 Fetching {TEST_URL}\n")
    resp = SESSION.get(TEST_URL, timeout=20)
    print(f"Status: {resp.status_code}, Length: {len(resp.text)}\n")

    soup = BeautifulSoup(resp.text, "html.parser")

    # Search all tables for rows containing a time pattern (possible
    # upcoming fixture) vs a score pattern (possible past result)
    tables = soup.find_all("table")
    print(f"Scanning {len(tables)} tables for time/score patterns...\n")

    for i, table in enumerate(tables):
        rows = table.find_all("tr")
        time_rows = []
        score_rows = []
        for row in rows:
            text = row.get_text(" ", strip=True)
            if TIME_RE.search(text) and not SCORE_RE.search(text):
                time_rows.append(text)
            elif SCORE_RE.search(text):
                score_rows.append(text)

        if time_rows or score_rows:
            print(f"--- TABLE #{i} — {len(rows)} rows, {len(time_rows)} time-like, {len(score_rows)} score-like ---")
            for t in time_rows[:3]:
                print(f"  TIME: {t[:150]}")
            for s in score_rows[:3]:
                print(f"  SCORE: {s[:150]}")
            print()


if __name__ == "__main__":
    main()
