"""
GOAL API league-ID mapping for all 61 leagues in daily_predictions.py's
LEAGUE_CODES, keyed by the same "Country - League" strings.

Built 2026-09-06 by:
1. Fetching GOAL API's full 1,019-league list
2. Fuzzy-matching with a hard country-match requirement + a
   disqualifying-word blacklist (cup/reserve/youth/women/etc.)
3. Manually verifying every low-confidence match against real
   football knowledge (Wikipedia cross-checks for Faroe Islands,
   New Zealand, and reasoning through language-translation cases
   like "Vysshaya Liga" = "Higher League" in Russian)

CORRECTIONS made to the raw fuzzy-match output (14 entries were wrong
or needed verification):
- Belarus: was "1. Division" (2nd tier) -> corrected to "Premier League"
- Chile - Liga de Primera: was "Primera B" (2nd tier, WRONG — also
  collided with the separate "Chile - Primera B" target) -> corrected
  to "Primera División"
- Faroe Islands: was "1. Deild" (2nd tier) -> corrected to
  "Meistaradeildin" (confirmed via Wikipedia: this IS the current top
  flight, also known as "Faroe Islands Premier League";
  "Løgmanssteypið" is a SEPARATE cup competition, not the league)
- Paraguay: was "Supercopa" (a cup, WRONG) -> corrected to
  "División Profesional"
- Peru: original algorithm pick "Primera División" was actually
  CORRECT despite a low string-similarity score — kept as-is
- Uruguay: was "Copa Uruguay" (a cup, WRONG) -> corrected to
  "Primera División"
- Venezuela: was "Copa Venezuela" (a cup, WRONG) -> corrected to
  "Primera División"
- England - Southern Football League: GOAL API genuinely does not
  carry this specific regional non-league competition (checked all 26
  England entries — none match; closest was "National League", a
  DIFFERENT, higher tier) — left UNMATCHED rather than force a wrong
  tier. This league will have NO GOAL API data; keep using another
  source (or accept the gap) for this one specifically.
- Bolivia: was "Nacional B" (2nd tier, WRONG) -> corrected to
  "Primera División"
- Iceland - Division 2: was "1. Deild" (WRONG — that's tier 2, not
  the tier-3 "Division 2" Kickwise means) -> corrected to "2. Deild"
- Singapore: original pick "Premier League" was CORRECT (S.League was
  renamed Singapore Premier League in 2018) — kept as-is
- New Zealand: was "Premiership" (outdated pre-2021 name, WRONG) ->
  corrected to "National League" (confirmed via Wikipedia: NZ's top
  flight was restructured and renamed National League in 2021)
- Turkmenistan: original pick "Ýokary Liga" was CORRECT (this IS the
  literal Turkmen translation of "Higher League") — kept as-is
- Tajikistan: original pick "Vysshaya Liga" was CORRECT (the only
  candidate found, and "Higher League" translated to Russian) — kept
  as-is

USAGE NOTE: callers must handle a missing key gracefully (fall back
to another source, or skip that league) rather than assuming all keys
are present. As of this version, some leagues remain intentionally
unmapped rather than force a wrong match:

- England - Southern Football League: no matching competition exists
  in GOAL API's database at all (see note above).
RESOLVED (2026-09-13): Finland - Ykkosliiga previously shared
Veikkausliiga's league_id (cmr77dxae00tlrx06ay98mhmc) as a known
placeholder, which meant fetch_team_stats() silently returned
Veikkausliiga's standings table for every Ykkösliiga match: wrong
teams, wrong stats, wrong prediction, with no error raised anywhere.
Fixed below using GOAL API's own name for this league, "Ykkönen"
(cmr77dxae00tmrx06j6n7oh79) — confirmed via /debug-finland-leagues
as the correct entry: 12 teams (matching Ykkönen's real 2026 team
count) and 945 fixtures, vs. a duplicate/stale "Ykkönen" entry
(cmr77dxae00tnrx06cmfvhzsn, apiId 8062) with only 10 teams and 403
fixtures — likely an old or lower-quality data-source import. Also
confirmed live via /predict-goalapi-test?league_id=... returning
team_count_in_league: 12, matching expectations.

============================================================
BATCH ADDITION (2026-09-15) — GOAL API MIGRATION, PHASE 2
============================================================
29 new leagues added below, all confirmed via the gp>=6 gate
(/debug-goalapi-top-leagues, /debug-goalapi-league-gp) against the
original AnnaBet 162-league candidate list. Every entry here passed
with a correctly-identified top-flight league (heuristic misses were
manually corrected — see conversation history for details on each).

league_ids in this batch were transcribed from screenshots of a
separate session, NOT pulled fresh in this one — spot-check a few
against /debug-goalapi-league-gp after deploy before fully trusting
them, since a single mistyped character silently breaks that league
(same failure mode as the Finland Ykkosliiga bug above).

DELIBERATELY EXCLUDED FROM THIS BATCH — stale-season-data suspects,
pending a fix in fetch_team_stats() (GOAL API appears to sometimes
return a stale prior-season "Championship Group"/split-format row
instead of the current season's row): Austria, Denmark, Switzerland,
Czech Republic, Slovakia, Israel, Cyprus, Indonesia, Moldova,
Northern Ireland, Wales. Add these only after the fix is confirmed
and their gp numbers are re-checked and look realistic (roughly 4-9
for an Aug-May season in mid-September).

ALSO EXCLUDED — correct league, just not enough games played yet as
of 2026-09-15 (recheck in a week or two): England, Italy, France,
Turkey, Saudi Arabia, Kuwait, Hong Kong, Albania, Bahrain,
San Marino, Tunisia, Uganda, Azerbaijan, Qatar.

NO COVERAGE — GOAL API has no usable top-flight data: South Africa,
UAE (in addition to England - Southern Football League above).
"""

GOALAPI_LEAGUE_IDS = {
    "Belarus - Vysshaya Liga": "cmr77dwca00ikrx06cf0v2gih",
    "Brazil - Serie A": "cmr77dvww00bfrx061thkr8z4",
    "Brazil - Serie B": "cmr77dvww00bgrx06cb9fmnv0",
    "Canada - Premier League": "cmr77dwrd00n3rx06xf5fzjjp",
    "Chile - Liga de Primera": "cmr77dvvh00avrx06jb5gtua7",
    "China - Super League": "cmr77dvlv0067rx06potai161",
    "China - League One": "cmr77dvlv0068rx0671n8gduz",
    "Colombia - Primera A": "cmr77dvv600aprx06o7y7lnfu",
    "Ecuador - Liga Pro": "cmr77dwb200hvrx06199fst9o",
    "Estonia - Meistriliiga": "cmr77dx1r00porx06tvpfwl68",
    "Faroe Islands - Premier League": "cmr77dwh200kbrx0601n7krq8",
    "Finland - Veikkausliiga": "cmr77dxae00tlrx06ay98mhmc",
    # "Finland - Ykkosliiga": REMOVED 2026-09-12 — was incorrectly
    # sharing Veikkausliiga's id above. Run find_finland_league_id.py
    # with a real API key to get the correct id, then restore this
    # entry. Until then, callers must treat this league like
    # "England - Southern Football League": no GOAL API data
    # available, fall back to another source or skip it — do NOT
    # re-add the Veikkausliiga id as a stand-in.
    "Georgia - Erovnuli Liga": "cmr77dx6300r7rx06cupwh420",
    "Iceland - Besta deild": "cmr77dwhd00kgrx06s7jzkv71",
    "Iceland - 1. Deild": "cmr77dwhd00kerx0683zt9qij",
    "Ireland - Premier Division": "cmr77dx9300szrx06xqoqq57b",
    "Ireland - First Division": "cmr77dx9300syrx06b3kjyaxo",
    "Kazakhstan - Premier League": "cmr77dwjp00kwrx06plbjknnn",
    "Latvia - Virsliga": "cmr77dwk900l2rx06i99xreez",
    "Lithuania - A Lyga": "cmr77dwgp00k5rx06ngnyuofo",
    "Malaysia - Super League": "cmr77dx7x00scrx067biz7vku",
    "Norway - Eliteserien": "cmr77dvr3007lrx061vsn0yjt",
    "Norway - 1st Division": "cmr77dvr4007mrx06srei0v3c",
    "Paraguay - Primera Div.": "cmr77dwy000onrx06oqbv0db1",
    "Peru - Liga 1": "cmr77dvvu00b1rx06plolocbg",
    "South Korea - K League 1": "cmr77dvsf008lrx06l2d31exu",
    "South Korea - K League 2": "cmr77dvsf008mrx06w9kcdv5a",
    "Sweden - Allsvenskan": "cmr77dvit0052rx06pj7safds",
    "Sweden - Superettan": "cmr77dvit0057rx06s7xypict",
    "Uruguay - Liga AUF": "cmr77dwyu00orx06onlk5cms",
    "USA - MLS": "cmr77dvtx009krx06tw1t8obh",
    "USA - USL Championship": "cmr77dvtx009wrx06v051zw0d",
    "Venezuela - Liga FUTVE": "cmr77dwzc00zrx067jdu1c1t",
    # "England - Southern Football League": NO MATCH — not in GOAL API's database
    "Germany - Bundesliga": "cmr77dvgm0002rx06rt2uqxii",
    "Belgium - First Amateur Division": "cmr77dw9g00gyrx06oiicyii0",
    "Algeria - Ligue 1": "cmr77dx0200p5rx066psribbn",
    "Australia - A-League": "cmr77dvgx000frx0621wwabup",
    "Australia - Brisbane Premier League": "cmr77dvgy000rrx06ddq7q9lp",
    "Chile - Primera B": "cmr77dvvi00axrx06sjsji50a",
    "Bolivia - LFPB": "cmr77dww100oarx0622pzc819",
    "Greece - Super League 2": "cmr77dwfb00jqrx06qci3nyj2",
    "Estonia - Esiliiga": "cmr77dx1r00pmrx06os1n8wbg",
    "Iceland - Division 2": "cmr77dwhe00kfrx06rhhr5qin",
    "Greece - Football League": "cmr77dwfb00jorx06ecraw1hn",
    "India - I-League": "cmr77dvry008brx060k7kpg3s",
    "India - Super League": "cmr77dvry008arx06q35z3h2m",
    "Jamaica - National Premier League": "cmr77dxek00v3rx06sid7w3j7",
    "Iran - Azadegan League": "cmr77dwp200mprx0641skuthc",
    "Kenya - Premier League": "cmr77dx9b00t1rx06yrhxo12z",
    "Jordan - League": "cmr77dx2800ptrx06hci0dhby",
    "Morocco - Botola": "cmr77dw7800garx06afr5or3k",
    "Singapore - S.League": "cmr77dx3q00qerx06mcl13f9u",
    "New Zealand - Championship": "cmr77dwuy00nvrx06ksn89rr",
    "Syria - Premier League": "cmr77dxex00v7rx06ythou6kl",
    "Thailand - League 1": "cmr77dwgb00jxrx067wx4esvl",
    "Vietnam - V.League 1": "cmr77dx4r00qrrx06byb1ounj",
    "Taiwan - Premier League": "cmr77dxk300wvrx06js3lqu1c",
    "Turkmenistan - Higher League": "cmr77dx1400x7rx068a9u2ovm",
    "Tajikistan - Higher League": "cmr77dxkg00wzrx064khv19p3",

    # ===== BATCH ADDITION 2026-09-15 — 29 new leagues =====
    # Batch 1
    "Spain - LaLiga": "cmr77dvnt006nrx063v3w622e",
    "Netherlands - Eredivisie": "cmr77dvrh007vrx0664phtxs5",
    "Russia - Premier League": "cmr77dvua00a4rx061d2jre7p",
    "Portugal - Primeira Liga": "cmr77dvun00adrx06xz20yfxe",
    "Japan - J1 League": "cmr77dx7h00rvrx060kholaxg",
    # Batch 2
    "Croatia - HNL": "cmr77dwa300hcrx06k4a5z7z4",
    "Serbia - Super Liga": "cmr77dwfu00jsrx06lzqv6ouq",
    "Romania - Liga I": "cmr77dwar00horx06biku04et",
    "Ukraine - Premier League": "cmr77dwdf00ixrx06z6havnxo",
    "Mexico - Liga MX": "cmr77dvsv008srx06mier6t7r",
    "Poland - Ekstraklasa": "cmr77dw8j00gerx06xvshbkow",
    # Batch 3
    "Hungary - NB I": "cmr77dwbn00i7rx06l118ra8w3",
    "Bulgaria - First League": "cmr77dw9r00h6rx06fol99vnt",
    "Slovenia - 1.SNL": "cmr77dw6e00g4rx06l17qly13",
    "Argentina - Liga Profesional Argentina": "cmr77dvtc0093rx0667jirsnv",
    # Batch 4
    "Bosnia - Premijer Liga": "cmr77dxi400w7rx061xedeerx",
    "Egypt - Premier League": "cmr77dwd0001irx06go0ax033",
    "Iraq - Iraqi League": "cmr77dxa700terx06zygnd5xe",
    "Luxembourg - National Division": "cmr77dx2t00q2rx06gam3mjqd",
    "Malta - Premier League": "cmr77dwry00ncrx06e35wpszd",
    "Montenegro - First League": "cmr77dx3c00q9rx06tjmuuiku",
    "North Macedonia - First League": "cmr77dx3200q5rx061z6mf2q2",
    # Scotland — stale-data bug confirmed fixed for this league
    # (38 -> 6 gp after the stageName/updatedAt fix)
    "Scotland - Premiership": "cmr77dwe100j5rx064jkxo63c",
    # Final batch
    "Armenia - Premier League": "cmr77dx0y00pirx061zdi1kou",
    "Costa Rica - Primera Division": "cmr77dwvj00o4rx06obpfwwsh",
    "El Salvador - Primera Division": "cmr77dxig00wbrx06ivt8vmh0",
    "Guatemala - Liga Nacional": "cmr77dxay00tvrx068w5v6fip",
    "Honduras - Liga Nacional": "cmr77dxb500tyrx064rbv0izc",
    "Tanzania - Ligi Kuu Bara": "cmr77dxbb00u0rx06vqu9f3rk",

    # ===== BATCH ADDITION 2026-09-17 — 8 more leagues =====
    # These 8 were originally flagged in the stale-season-data
    # investigation (see goalapi_fetcher.py's FIX notes) alongside
    # Scotland above. All confirmed genuinely fixed and gp>=6 after
    # the "skip teams with no Current-stage row entirely" fix
    # (2026-09-17) — verified via /debug-goalapi-league-gp AND
    # /debug-goalapi-stage-audit, not just a single spot-check.
    #
    # Still pending, correct league confirmed but not enough games
    # played yet as of 2026-09-17 (recheck in a week or two):
    # Israel - Ligat Ha'al (cmr77dwbc00i0rx06adepi1hb, gp=5),
    # Cyprus - 1. Division (cmr77dwj200korx0664b8h5mq, gp=4),
    # Indonesia - Liga 1 (cmr77dwae00hjrx061fdxl3eb, gp=3).
    "Austria - Bundesliga": "cmr77dvjm005brx062i1cpb4k",
    "Denmark - Superliga": "cmr77dw1z00exrx062x6co26u",
    "Czech Republic - Czech Liga": "cmr77dw9200gmrx06o9tqq555",
    "Switzerland - Super League": "cmr77dvyx00egrx06pta3wmnc",
    "Slovakia - 1. Liga": "cmr77dw5l00fyrx064o73i0dp",
    "Northern Ireland - Premiership": "cmr77dwuo00nqrx06v4rpsuzk",
    "Wales - Premier League": "cmr77dx8b00slrx06b733ce6d",
    "Moldova - Super Liga": "cmr77dx5k00r2rx06srbm93dq",
}
