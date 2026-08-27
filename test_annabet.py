import requests
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

url = "https://annabet.com/en/soccerstats/serie_351_x.html"

session = requests.Session()
session.headers.update(HEADERS)

print("🔍 Loading AnnaBet Armenia Premier League...")
print(url)
print()

response = session.get(url, timeout=30)

print("HTTP status:", response.status_code)
print("HTML length:", len(response.text))
print("Final URL:", response.url)
print()

soup = BeautifulSoup(response.text, "html.parser")

print("=" * 80)
print("SEARCHING FOR TABLES CONTAINING GP")
print("=" * 80)

found = 0

for table_number, table in enumerate(soup.find_all("table"), start=1):

    rows = table.find_all("tr")

    if not rows:
        continue

    # Look through the first few rows for headers
    for row_number, row in enumerate(rows[:5], start=1):

        cells = row.find_all(["th", "td"])

        headers = [
            " ".join(cell.get_text(" ", strip=True).split())
            for cell in cells
        ]

        joined = " | ".join(headers)

        if "GP" in [x.upper() for x in headers] or "GAMES PLAYED" in joined.upper():

            found += 1

            print()
            print(f"TABLE #{table_number}")
            print(f"HEADER ROW #{row_number}")
            print("-" * 80)
            print(joined)

            print()
            print("FIRST 15 DATA ROWS:")
            print("-" * 80)

            header_index = rows.index(row)

            count = 0

            for data_row in rows[header_index + 1:]:

                data_cells = data_row.find_all("td")

                if not data_cells:
                    continue

                values = [
                    " ".join(
                        cell.get_text(" ", strip=True).split()
                    )
                    for cell in data_cells
                ]

                print(" | ".join(values))

                count += 1

                if count >= 15:
                    break

print()
print("=" * 80)
print(f"TABLES CONTAINING GP: {found}")
print("=" * 80)

if found == 0:
    print()
    print("❌ No GP table was detected.")
    print("We need to inspect the HTML structure.")
else:
    print()
    print("✅ GP table(s) detected.")
