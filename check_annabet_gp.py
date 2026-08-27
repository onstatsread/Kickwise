import csv
import random
import time
import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from annabet_leagues import ANNABET_LEAGUE_IDS

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

BASE_URL = "https://annabet.com/en/soccerstats/serie_{serie_id}_x.html"
OUTPUT_CSV = "annabet_gp_results.csv"

SESSION = requests.Session()
SESSION.headers.update(HEADERS)

retry_strategy = Retry(
    total=3,
    backoff_factor=1.5,
    status_forcelist=[429, 500, 502, 503, 504],
    allowed_methods=["GET"],
    raise_on_status=False,
)
adapter = HTTPAdapter(max_retries=retry_strategy)
SESSION.mount("https://", adapter)
SESSION.mount("http://", adapter)


def sleep_jitter(min_s=5, max_s=12):
    time.sleep(random.uniform(min_s, max_s))


def normalize_headers(headers):
    return [h.strip().upper().replace(" ", " ") for h in headers]


def extract_max_gp(html):
    soup = BeautifulSoup(html, "html.parser")
    max_gp = 0

    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        if not rows:
            continue

        header_cells = rows[0].find_all(["th", "td"])
        headers = normalize_headers([c.get_text(" ", strip=True) for c in header_cells])

        if "GP" not in headers:
            continue

        gp_idx = headers.index("GP")

        for row in rows[1:]:
            cells = row.find_all("td")
            if len(cells) <= gp_idx:
                continue

            gp_text = cells[gp_idx].get_text(" ", strip=True).replace(",", "")
            if gp_text.isdigit():
                gp = int(gp_text)
                if gp > max_gp:
                    max_gp = gp

    return max_gp


def fetch_league(name, serie_id, max_retries=3):
    url = BASE_URL.format(serie_id=serie_id)
    last_error = None

    for attempt in range(1, max_retries + 1):
        try:
            resp = SESSION.get(url, timeout=20)
            if resp.status_code == 429:
                wait = (2 ** attempt) + random.uniform(0.5, 2.5)
                time.sleep(wait)
                continue

            resp.raise_for_status()

            max_gp = extract_max_gp(resp.text)
            if max_gp > 0:
                return {
                    "status": "ok",
                    "name": name,
                    "serie_id": serie_id,
                    "gp": max_gp,
                    "url": resp.url,
                }

            snippet = resp.text[:500].replace("
", " ").replace("
", " ")
            return {
                "status": "no_data",
                "name": name,
                "serie_id": serie_id,
                "gp": 0,
                "url": resp.url,
                "debug": snippet,
            }

        except Exception as e:
            last_error = e
            if attempt < max_retries:
                wait = (2 ** attempt) + random.uniform(0.5, 2.5)
                time.sleep(wait)

    return {
        "status": "error",
        "name": name,
        "serie_id": serie_id,
        "gp": -1,
        "error": str(last_error),
    }


def main():
    print(f"🔍 Checking games-played across {len(ANNABET_LEAGUE_IDS)} leagues on AnnaBet...
")

    results = []

    for i, (name, serie_id) in enumerate(ANNABET_LEAGUE_IDS.items(), start=1):
        result = fetch_league(name, serie_id)

        if result["status"] == "ok":
            print(f"  ✅  {name} (serie_{serie_id}): {result['gp']} games played")
        elif result["status"] == "no_data":
            print(f"  ❓  {name} (serie_{serie_id}): no GP table found")
        else:
            print(f"  ❌  {name} (serie_{serie_id}): failed — {result['error']}")

        results.append(result)

        if i < len(ANNABET_LEAGUE_IDS):
            sleep_jitter(5, 12)

    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["name", "serie_id", "status", "gp", "url", "error_or_debug"])
        for r in results:
            writer.writerow([
                r.get("name", ""),
                r.get("serie_id", ""),
                r.get("status", ""),
                r.get("gp", ""),
                r.get("url", ""),
                r.get("error", r.get("debug", "")),
            ])

    ok = [r for r in results if r["status"] == "ok"]
    print(f"
📊 Summary — {len(results)} leagues checked")
    print(f"✅ Successful: {len(ok)}")
    print(f"📁 Saved CSV: {OUTPUT_CSV}")

    print("
✅ GAMES PLAYED:")
    for r in sorted(ok, key=lambda x: -x["gp"]):
        print(f"    {r['name']}: {r['gp']} GP")


if __name__ == "__main__":
    main()
