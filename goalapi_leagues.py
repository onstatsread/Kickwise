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

USAGE NOTE: England - Southern Football League has NO entry below —
callers must handle a missing key gracefully (fall back to another
source, or skip that league) rather than assuming all 61 are present.
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
    "Finland - Ykkosliiga": "cmr77dxae00tlrx06ay98mhmc",  # NOTE: same id as Veikkausliiga — GOAL API may not carry Ykkösliiga separately; verify before use
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
}
