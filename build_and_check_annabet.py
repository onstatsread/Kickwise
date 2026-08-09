"""
AnnaBet Table Identification — Round 2
Last attempt assumed tables 0/1/2 = All/Home/Away by position, which
was wrong (table 1 was empty, and "All Games" + "At Away" showed
identical data — meaning the real Home/Away tables are elsewhere among
the 19 tables on the page).

This time: print EVERY table's surrounding context (any heading, tab
label, or text within ~300 characters before it) plus its first 2 data
rows, so we can visually match each table to what it actually contains
instead of guessing by position.
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


def main():
    print(f"🔍 Fetching {TEST_URL}\n")
    resp = SESSION.get(TEST_URL, timeout=20)
    print(f"Status: {resp.status_code}, Length: {len(resp.text)}\n")

    soup = BeautifulSoup(resp.text, "html.parser")
    tables = soup.find_all("table")
    print(f"Found {len(tables)} tables total.\n")
    print("=" * 60)

    for i, table in enumerate(tables):
        rows = table.find_all("tr")

        html_str = str(soup)
        table_html = str(table)
        pos = html_str.find(table_html)
        context_before = html_str[max(0, pos - 400):pos]
        context_soup = BeautifulSoup(context_before, "html.parser")
        context_text = context_soup.get_text(" ", strip=True)[-150:]

        table_attrs = table.attrs
        parent = table.parent
        parent_attrs = parent.attrs if parent else {}

        print(f"\nTABLE #{i} — {len(rows)} rows")
        print(f"  Context text just before: ...{context_text!r}")
        print(f"  Table attrs: {table_attrs}")
        print(f"  Parent tag: <{parent.name if parent else '?'}> attrs: {parent_attrs}")

        if rows:
            first_row_cells = [c.get_text(strip=True) for c in rows[0].find_all(["td", "th"])]
            print(f"  First row: {first_row_cells}")
        if len(rows) > 1:
            second_row_cells = [c.get_text(strip=True) for c in rows[1].find_all(["td", "th"])]
            print(f"  Second row: {second_row_cells}")

        print("-" * 60)


if __name__ == "__main__":
    main()
