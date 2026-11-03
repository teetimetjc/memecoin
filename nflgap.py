"""Line gap, phase 2: MATCH Kalshi NFL game markets against the sharp line.

Phase 1 (linegap.py) looked at both sides and deliberately matched nothing,
because a matcher written against an imagined response shape is how a log
fills with confident, wrong rows. The shapes have since been read for real by
nflmarket.py, so this is written against them:

  series            KXNFLGAME -- 200 markets, 100 events, 2 sides each
  market ticker     KXNFLGAME-26NOV09BUFMIN-MIN
  yes_sub_title     'Minnesota'            <- city, not nickname
  event_ticker      shared by both sides of one game
  occurrence_datetime  THE KICKOFF. close_time is a settlement backstop and
                    sat three days after a Nov 9 game, so matching on it
                    would have mismatched every row.
  ties              rules_secondary pays $0.50 TO EACH SIDE, not $0 to Yes

FOUR THINGS THIS DOES DIFFERENTLY FROM THE OBVIOUS VERSION, each because the
obvious version fails quietly.

1. IT MATCHES ON THE TICKER'S SIDE TOKEN, NOT ON TEAM NAMES.
   Kalshi writes cities ('Minnesota'), the Odds API writes full names
   ('Minnesota Vikings'), and a name-similarity matcher looked at
   KXEFOOTBALLGAME -- a VIDEO GAME league whose markets read 'Seattle (Knox)
   wins' -- and would have logged it as NFL. The side token after the last
   dash is an exact abbreviation; it is mapped through a table or the row is
   dropped and reported. Nothing is matched by resemblance.

2. IT WILL NOT ACCEPT AN AMBIGUOUS MATCH.
   If a team pair resolves to more than one odds event, or to more than one
   Kalshi event, BOTH are dropped and printed. A duplicate silently resolved
   to the first hit is a row that looks measured and is not.

3. THE TIE IS SUBTRACTED, NOT IGNORED.
   A sportsbook moneyline PUSHES on a tie, so its de-vigged number is
   P(win | no tie). Kalshi pays $0.50 to each side. So the fair value of a
   Kalshi contract is

       fair = p*(1-t) + 0.5*t      =  p + t*(0.5 - p)

   which pulls both sides toward 50c. At a 0.3% tie rate it is about 0.1
   points -- small, but it biases EVERY row the same direction, and the
   threshold is 3 points.

4. EVERY MATCHED MARKET IS LOGGED, not only the ones with an edge.
   The distribution is the only control that can tell a working pipeline
   from a broken one. If every row shows a 6-point edge, the arithmetic is
   wrong, not the market.

WHAT IS STILL UNVERIFIED, and is printed as such rather than assumed:

  the fee coefficient    0.07 was fitted on 162 of 163 real CRYPTO fills.
                         It has never been checked on a sports series, and
                         fee_multiplier was not among the fields printed.
                         A wrong coefficient moves the edge by tenths of a
                         point, not points, but it is not verified.
  depth                  The ask size is printed on every row and no row is
                         called tradeable without it. A 4-point gap with 11
                         contracts behind it is what killed T-3 and oil.

PAPER ONLY. This places no order, writes no row to any sheet, and changes no
setting. It prints. Scoring against the closing line is phase 3, and needs
persistence this does not have -- so nothing here should be read as a
backtest. It answers one question: does the matching work.
"""
import os
import sys
from datetime import datetime, timedelta, timezone

import requests

import collect15 as C
import oddsmath as M

ODDS_HOST = "https://api.the-odds-api.com/v4"

SPORT = (os.environ.get("ODDS_SPORT") or "americanfootball_nfl").strip()
MARKETS = "h2h"
REGIONS = (os.environ.get("ODDS_REGIONS") or "us,eu").strip()
SHARP = [b.strip().lower() for b in
         (os.environ.get("ODDS_SHARP") or "pinnacle").split(",") if b.strip()]
SERIES = (os.environ.get("NFLGAP_SERIES") or "KXNFLGAME").strip()

# PRE-REGISTERED, before any number is read. A threshold chosen after seeing
# the distribution is not a threshold, it is a selection.
EDGE_PTS = float(os.environ.get("NFLGAP_EDGE_PTS") or 3.0)
# NFL ties: 0.2-0.4% of games historically. Mid-range, and the sensitivity to
# it is printed so the choice is visible rather than buried.
TIE_RATE = float(os.environ.get("NFLGAP_TIE_RATE") or 0.003)
# A line older than this is a memory, not a quote.
STALE_S = int(os.environ.get("ODDS_STALE_S") or 600)
# Reference order size for the fee, which rounds up per ORDER. The real size
# would be min(this, ask size); both are printed.
REF_CONTRACTS = int(os.environ.get("NFLGAP_CONTRACTS") or 25)
# Kickoff tolerance between the two sources. Same game, two clocks.
KICK_TOL_H = float(os.environ.get("NFLGAP_KICK_TOL_H") or 30.0)

# THE JOIN TABLE. Exact, hand-written, no fuzzy fallback. NY and LA are the
# reason this cannot be done on city alone: four teams, two cities.
ABBR = {
    "ARI": "Arizona Cardinals",      "ATL": "Atlanta Falcons",
    "BAL": "Baltimore Ravens",       "BUF": "Buffalo Bills",
    "CAR": "Carolina Panthers",      "CHI": "Chicago Bears",
    "CIN": "Cincinnati Bengals",     "CLE": "Cleveland Browns",
    "DAL": "Dallas Cowboys",         "DEN": "Denver Broncos",
    "DET": "Detroit Lions",          "GB": "Green Bay Packers",
    "HOU": "Houston Texans",         "IND": "Indianapolis Colts",
    "JAX": "Jacksonville Jaguars",   "KC": "Kansas City Chiefs",
    "LV": "Las Vegas Raiders",       "LAC": "Los Angeles Chargers",
    "LAR": "Los Angeles Rams",       "MIA": "Miami Dolphins",
    "MIN": "Minnesota Vikings",      "NE": "New England Patriots",
    "NO": "New Orleans Saints",      "NYG": "New York Giants",
    "NYJ": "New York Jets",          "PHI": "Philadelphia Eagles",
    "PIT": "Pittsburgh Steelers",    "SEA": "Seattle Seahawks",
    "SF": "San Francisco 49ers",     "TB": "Tampa Bay Buccaneers",
    "TEN": "Tennessee Titans",       "WAS": "Washington Commanders",
    # Spellings seen in the wild for the same two franchises.
    "JAC": "Jacksonville Jaguars",   "WSH": "Washington Commanders",
    "OAK": "Las Vegas Raiders",      "SD": "Los Angeles Chargers",
    "STL": "Los Angeles Rams",
}
NAME_TO_ABBR = {}
for _a, _n in ABBR.items():
    # First spelling wins, so the aliases above do not displace the canonical
    # abbreviation when mapping back.
    NAME_TO_ABBR.setdefault(_n, _a)


def key_or_die():
    k = (os.environ.get("ODDS_API_KEY") or "").strip()
    if not k:
        print("  ODDS_API_KEY is not set. Hard stop: nothing was fetched and")
        print("  no quota was spent. An empty slate is indistinguishable from")
        print("  a quiet day, so this refuses to produce one.")
        raise SystemExit(2)
    return k


def fetch_odds(key):
    url = f"{ODDS_HOST}/sports/{SPORT}/odds"
    params = dict(apiKey=key, regions=REGIONS, markets=MARKETS,
                  oddsFormat="american")
    try:
        r = requests.get(url, params=params, timeout=25)
    except Exception as e:
        print(f"  the odds request failed outright: {str(e)[:140]}")
        raise SystemExit(1)
    quota = {k2: r.headers.get(k2) for k2 in
             ("x-requests-remaining", "x-requests-used", "x-requests-last")}
    if not r.ok:
        print(f"  HTTP {r.status_code}: {r.text[:200]}")
        print(f"  quota headers: {quota}")
        raise SystemExit(1)
    return r.json(), quota


def parse_iso(s):
    """ISO8601 -> aware UTC datetime, or None. Never guesses."""
    t = str(s or "").strip()
    if not t:
        return None
    t = t.replace("Z", "+00:00")
    try:
        d = datetime.fromisoformat(t)
    except Exception:
        try:
            d = datetime.strptime(t[:19], "%Y-%m-%dT%H:%M:%S")
        except Exception:
            return None
    return d.replace(tzinfo=timezone.utc) if d.tzinfo is None \
        else d.astimezone(timezone.utc)


def side_abbr(ticker):
    """The team token after the LAST dash of a market ticker.

    KXNFLGAME-26NOV09BUFMIN-MIN -> 'MIN'. The middle segment concatenates
    both abbreviations with no separator, and abbreviations are two or three
    characters, so splitting it is ambiguous ('GBNE' could be GB+NE or
    GBN+E). It is not split: the side token is already unambiguous, and the
    two sides of an event supply the pair.
    """
    parts = str(ticker or "").split("-")
    return parts[-1].strip().upper() if len(parts) >= 3 else ""


def fnum(v):
    try:
        x = float(v)
        return x if x == x else None
    except (TypeError, ValueError):
        return None


def ask_price(m):
    """Yes ask as a probability in (0,1), or None.

    Kalshi serves both a dollars field and a cents field depending on the
    endpoint and the day. Both are tried, and anything outside (0,1) after
    conversion is rejected rather than clamped -- a clamped price is a
    plausible wrong number, which is the bug class this project keeps
    tripping over.
    """
    v = fnum(m.get("yes_ask_dollars"))
    if v is None:
        c = fnum(m.get("yes_ask"))
        v = None if c is None else c / 100.0
    if v is None or not (0.0 < v < 1.0):
        return None
    return v


def ask_size(m):
    for k in ("yes_ask_size_fp", "yes_ask_size"):
        v = fnum(m.get(k))
        if v is not None:
            return v
    return None


def main():
    print("=" * 104)
    print("NFL LINE GAP -- PHASE 2: MATCHED. Paper only; places nothing, "
          "logs nothing to any sheet.")
    print(f"  sport={SPORT}  regions={REGIONS}  sharp={'/'.join(SHARP)}  "
          f"series={SERIES}")
    print(f"  PRE-REGISTERED: edge threshold {EDGE_PTS:.1f} pts   "
          f"tie rate {100*TIE_RATE:.2f}%   fee coeff {M.FEE_COEFF} "
          f"(UNVERIFIED on sports)")
    print("=" * 104)

    key = key_or_die()
    events, quota = fetch_odds(key)
    print(f"\n  QUOTA  used {quota.get('x-requests-used')}   "
          f"remaining {quota.get('x-requests-remaining')}   "
          f"this call {quota.get('x-requests-last')}")
    print(f"  {len(events)} upcoming {SPORT} event(s) from the odds API")

    # ---------------------------------------------------------------- odds
    # Keyed by the unordered pair of abbreviations. Unmapped names and
    # duplicate pairs are kept separately and reported, never dropped quietly.
    odds_by_pair = {}
    odds_unmapped = []
    for ev in events:
        home, away = ev.get("home_team"), ev.get("away_team")
        ha, aa = NAME_TO_ABBR.get(home), NAME_TO_ABBR.get(away)
        if not ha or not aa:
            odds_unmapped.append((away, home))
            continue
        odds_by_pair.setdefault(frozenset((ha, aa)), []).append(ev)

    books_seen = set()
    for ev in events:
        for b in ev.get("bookmakers") or []:
            books_seen.add(b.get("key"))
    missing_sharp = [s for s in SHARP if s not in books_seen]
    if missing_sharp:
        print(f"\n  *** THE SHARP REFERENCE IS MISSING: "
              f"{', '.join(missing_sharp)}")
        print("  *** Books present: " + (", ".join(sorted(
            x for x in books_seen if x)) or "none"))
        print("  *** Every gap below would be Kalshi against a RETAIL book,")
        print("  *** which mostly measures that book's margin and its shading")
        print("  *** toward public money. Rows are still printed, because the")
        print("  *** matching is what is being tested -- but no edge from this")
        print("  *** run is evidence of a mispricing.")
    else:
        print(f"  sharp reference present: {', '.join(SHARP)}")
    if odds_unmapped:
        print(f"\n  {len(odds_unmapped)} odds event(s) whose team names are not"
              f" in the join table, DROPPED and listed:")
        for aw, hm in odds_unmapped[:12]:
            print(f"    {aw!r} @ {hm!r}")
        print("    (add them to ABBR; they are not matched by resemblance)")

    # --------------------------------------------------------------- kalshi
    d, err = C.get("/trade-api/v2/markets", series_ticker=SERIES, limit=1000)
    if err:
        print(f"\n  THE KALSHI READ FAILED: {err}")
        print("  That is a real answer, not an empty series. Nothing below")
        print("  should be read as evidence either way.")
        return 1
    mks = (d or {}).get("markets") or []
    print(f"\n  {len(mks)} Kalshi market(s) in {SERIES}")
    if not mks:
        print("  EMPTY. Check the series ticker before concluding anything.")
        return 1

    k_events = {}
    for m in mks:
        k_events.setdefault(m.get("event_ticker"), []).append(m)

    # Only two-sided events are usable: one side alone cannot be checked
    # against a two-way de-vig.
    k_by_pair, k_bad = {}, []
    for ev_t, ms in k_events.items():
        abbrs = [side_abbr(m.get("ticker")) for m in ms]
        if len(ms) != 2 or not all(a in ABBR for a in abbrs) \
                or abbrs[0] == abbrs[1]:
            k_bad.append((ev_t, len(ms), abbrs))
            continue
        k_by_pair.setdefault(frozenset(abbrs), []).append((ev_t, ms))

    print(f"  {len(k_events)} Kalshi event(s); {len(k_by_pair)} usable "
          f"two-sided pairs, {len(k_bad)} unusable")
    if k_bad:
        print("  unusable, listed rather than skipped silently:")
        for ev_t, n, abbrs in k_bad[:10]:
            print(f"    {str(ev_t)[:38]:<38} {n} side(s)  tokens {abbrs}")

    # ---------------------------------------------------------------- join
    rows, amb, no_odds, no_sharp_mk, stale_rows = [], [], [], [], []
    for pair, entries in sorted(k_by_pair.items(),
                                key=lambda kv: sorted(kv[0])):
        label = " vs ".join(sorted(pair))
        if len(entries) > 1:
            amb.append((label, f"{len(entries)} Kalshi events"))
            continue
        cands = odds_by_pair.get(pair) or []
        if not cands:
            no_odds.append(label)
            continue
        ev_t, ms = entries[0]
        kick_k = parse_iso(ms[0].get("occurrence_datetime"))
        # Narrow by kickoff when both clocks are available. Two meetings of
        # the same pair in one season are otherwise indistinguishable.
        if len(cands) > 1 and kick_k is not None:
            near = [c for c in cands
                    if (parse_iso(c.get("commence_time")) is not None
                        and abs((parse_iso(c["commence_time"]) - kick_k)
                                .total_seconds()) <= KICK_TOL_H * 3600)]
            cands = near
        if len(cands) != 1:
            amb.append((label, f"{len(cands)} odds events after the "
                               f"kickoff filter"))
            continue
        oev = cands[0]
        kick_o = parse_iso(oev.get("commence_time"))
        if kick_k is not None and kick_o is not None:
            dh = abs((kick_o - kick_k).total_seconds()) / 3600.0
            if dh > KICK_TOL_H:
                amb.append((label, f"kickoffs {dh:.1f}h apart -- "
                                   f"not the same game"))
                continue

        books = {b.get("key"): b for b in (oev.get("bookmakers") or [])}
        ref = next((books[s] for s in SHARP if s in books), None)
        if ref is None:
            # Fall back to any book, clearly marked, so the matching is still
            # exercised. The header already says such a row proves nothing.
            ref = next(iter(books.values()), None)
            if ref is None:
                no_sharp_mk.append(label)
                continue
        mk = next((x for x in (ref.get("markets") or [])
                   if x.get("key") == "h2h"), None)
        if not mk or len(mk.get("outcomes") or []) != 2:
            no_sharp_mk.append(label)
            continue
        outs = mk["outcomes"]
        try:
            raws = [M.american_to_prob(o["price"]) for o in outs]
        except Exception as e:
            no_sharp_mk.append(f"{label} (unreadable price: {e})")
            continue

        age = None
        lu = parse_iso(mk.get("last_update") or ref.get("last_update"))
        if lu is not None:
            age = (datetime.now(timezone.utc) - lu).total_seconds()

        # One row per Kalshi side.
        for m in ms:
            ab = side_abbr(m.get("ticker"))
            full = ABBR[ab]
            idx = next((i for i, o in enumerate(outs)
                        if o.get("name") == full), None)
            if idx is None:
                amb.append((label, f"{ab} -> {full!r} is not an outcome name "
                                   f"on the book's market"))
                continue
            lo, hi, spread = M.fair_consensus(raws, idx)
            # The tie, subtracted rather than ignored. The book's number is
            # P(win | no tie); Kalshi pays $0.50 each side on a tie.
            fair = lo * (1.0 - TIE_RATE) + 0.5 * TIE_RATE
            px = ask_price(m)
            sz = ask_size(m)
            if px is None:
                rows.append(dict(pair=label, team=ab, px=None, sz=sz,
                                 fair=fair, spread=spread, edge=None,
                                 book=ref.get("key"), age=age,
                                 ticker=m.get("ticker")))
                continue
            n = REF_CONTRACTS if sz is None else int(min(REF_CONTRACTS,
                                                         max(sz, 0)))
            edge = M.net_edge(fair, px, n) if n > 0 else None
            rows.append(dict(pair=label, team=ab, px=px, sz=sz, fair=fair,
                             spread=spread, edge=edge, n=n,
                             book=ref.get("key"), age=age,
                             ticker=m.get("ticker")))

    # -------------------------------------------------------------- report
    print("\n" + "-" * 104)
    print("MATCHING -- what joined and what did not. The failures are the "
          "point of this run.")
    print("-" * 104)
    print(f"  matched games            : "
          f"{len({r['pair'] for r in rows})}")
    print(f"  matched sides (rows)     : {len(rows)}")
    print(f"  no odds event for pair   : {len(no_odds)}"
          + (f"   {', '.join(no_odds[:8])}" if no_odds else ""))
    print(f"  ambiguous, DROPPED       : {len(amb)}")
    for lbl, why in amb[:12]:
        print(f"      {lbl:<18} {why}")
    print(f"  no usable h2h market     : {len(no_sharp_mk)}"
          + (f"   {', '.join(str(x) for x in no_sharp_mk[:6])}"
             if no_sharp_mk else ""))

    if not rows:
        print("\n  NOTHING MATCHED. That is the result: the matcher does not")
        print("  work yet, or there are no concurrent games. Either way no")
        print("  gap number exists to report, and none is invented here.")
        return 0

    print("\n" + "-" * 104)
    print("EVERY MATCHED SIDE, edge included when negative. The distribution "
          "is the control.")
    print(f"  edge = conservative fair (of three de-vigs, tie-adjusted) "
          f"minus ask minus fee at n contracts")
    print("-" * 104)
    print(f"  {'game':<18}{'side':<6}{'book':<11}{'ask':>7}{'size':>8}"
          f"{'fair':>8}{'devig sp':>10}{'edge pts':>10}{'age':>8}")
    for r in sorted(rows, key=lambda r: (r["edge"] is None,
                                         -(r["edge"] or 0.0))):
        px = "--" if r["px"] is None else f"{100*r['px']:.1f}c"
        sz = "--" if r["sz"] is None else f"{r['sz']:.0f}"
        ed = "  no ask" if r["edge"] is None else f"{r['edge']:+.2f}"
        ag = "?" if r["age"] is None else f"{r['age']/60.0:.0f}m"
        flag = ""
        if r["edge"] is not None and r["edge"] >= EDGE_PTS:
            flag = "  <- over threshold"
            if r["sz"] is None or r["sz"] < REF_CONTRACTS:
                flag += ", THIN"
            if r["age"] is not None and r["age"] > STALE_S:
                flag += ", STALE LINE"
        print(f"  {r['pair']:<18}{r['team']:<6}{str(r['book'])[:10]:<11}"
              f"{px:>7}{sz:>8}{100*r['fair']:>7.1f}%"
              f"{100*r['spread']:>9.2f}{ed:>10}{ag:>8}{flag}")

    live = [r for r in rows if r["edge"] is not None]
    if live:
        es = sorted(r["edge"] for r in live)
        mid = es[len(es) // 2]
        over = [r for r in live if r["edge"] >= EDGE_PTS]
        tradeable = [r for r in over
                     if r["sz"] is not None and r["sz"] >= REF_CONTRACTS
                     and (r["age"] is None or r["age"] <= STALE_S)]
        sp = sorted(r["spread"] for r in live)
        print("\n" + "-" * 104)
        print(f"  {len(live)} priced sides   median edge {mid:+.2f} pts   "
              f"range {es[0]:+.2f} to {es[-1]:+.2f}")
        print(f"  de-vig disagreement: median "
              f"{100*sp[len(sp)//2]:.2f} pts, worst {100*sp[-1]:.2f}")
        print(f"  over the {EDGE_PTS:.1f}-point threshold: {len(over)}   "
              f"of those, with real size and a fresh line: {len(tradeable)}")
        print(f"  tie adjustment moved every fair value by "
              f"{abs(100*TIE_RATE*0.5):.2f} pts at most "
              f"(t={100*TIE_RATE:.2f}%)")
        print()
        print("  HOW TO READ THE MEDIAN. Both sides of a two-way market are")
        print("  priced here, and a book's de-vigged pair sums to 1 while")
        print("  Kalshi's two asks sum to MORE than 1 by the spread. So the")
        print("  median edge should be NEGATIVE by about half the Kalshi")
        print("  spread. A median near zero or positive means the arithmetic")
        print("  or the matching is wrong, not that the market is loose.")

    print("\n" + "=" * 104)
    print("  WHAT THIS RUN DOES NOT ESTABLISH.")
    print()
    print("  It is one snapshot. An edge at this instant says nothing about")
    print("  whether it survives to the close, which is the only question")
    print("  that matters -- and scoring that needs the closing line stored")
    print("  per market over weeks. That is phase 3, and it is the point of")
    print("  the whole exercise; this is the plumbing it needs.")
    print()
    print("  The fee coefficient is unverified on sports series. The depth is")
    print("  printed, not trusted. Nothing was ordered and nothing logged.")
    print("=" * 104)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
