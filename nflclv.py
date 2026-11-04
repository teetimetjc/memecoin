"""Line gap, phase 3: STORE the snapshots and score them against the close.

WHY THIS IS THE ONLY PHASE THAT CAN ANSWER ANYTHING. Phase 2 proved the
matching works and printed 24 sides, every one of them negative. That is a
photograph. It cannot tell you whether a gap, on the day one appears, means
the price is wrong -- or whether our number is the wrong one.

Closing line value is what separates those. If Kalshi's price drifts TOWARD
our computed fair value between the snapshot and kickoff, the number carries
information. If it drifts away, or wanders, it does not. That verdict needs
the same markets read repeatedly over weeks, which needs storage, which is
the one thing phase 2 deliberately did not have.

  snap   append one row per matched side to the NFLCLV tab
  score  read the tab back and ask whether edge at snapshot predicted the
         move to the last reading before kickoff

IT IMPORTS THE MATCHER RATHER THAN REIMPLEMENTING IT. nflgap.match_rows is
the single definition of what a matched row is. A second copy here would
drift from it silently, and the drift would be invisible precisely because
both halves would keep running green -- which is this project's standing bug
class, now at roughly fifty-five instances.

THREE THINGS THE SCORE DELIBERATELY DOES NOT CLAIM.

  It is not profit. Movement toward fair value is evidence the signal has
  information; it is not money, because nothing was bought and depth was
  never tested.
  The price recorded is the YES ASK, not a mid. An ask carries half the
  spread, so a move measured ask-to-ask is clean only if the spread is
  stable. The spread is logged on every row so that assumption can be
  checked rather than trusted.
  One NFL season is about nine more weeks. That is enough to see whether
  gaps appear and whether they predict the close. It is not enough to
  establish a profitable edge, and no output here should be read as though
  it were.

Writes ONE tab, NFLCLV, appending only. Places no order. Touches no live
setting and no existing tab.
"""
import os
import sys
import time
from datetime import datetime, timezone

import nflgap as G
import oddsmath as M
import predictor as P

# ONE TAB PER LEAGUE. Football and basketball have different tie terms and
# different liquidity shapes, and mixing them in one sheet invites an
# analysis that pools them without noticing.
TAB = (os.environ.get("NFLCLV_TAB") or f"{G.LEAGUE}CLV").strip()

HEADERS = [
    "Snapshot UTC", "League", "Ticker", "Event", "Pair", "Side", "Kickoff UTC",
    "Hours To Kick", "Book", "Book Odds", "Fair %", "Kalshi Ask c",
    "Ask Size", "Devig Spread Pts", "Edge Pts", "Sharp", "Tie Rate",
    "Fee Coeff", "Contracts",
]

# A reading taken after kickoff is not a closing line, it is a live
# in-game price on a market that may already be resolving.
MIN_HOURS = float(os.environ.get("NFLCLV_MIN_HOURS") or 0.0)


def tab(sh):
    """Open or create the tab. Appending to a tab with a different header
    would shift every field, so the header is verified, not assumed."""
    try:
        w = sh.worksheet(TAB)
        head = w.row_values(1)
        if head and head != HEADERS:
            print(f"  THE {TAB} HEADER DOES NOT MATCH what this script "
                  f"writes.")
            print(f"    on the sheet: {head}")
            print(f"    expected    : {HEADERS}")
            print("  Refusing to append: a row written under a different")
            print("  header is silently misaligned in every column, and the")
            print("  history would be unreadable rather than obviously wrong.")
            return None
        if not head:
            w.update(values=[HEADERS], range_name="A1")
        return w
    except Exception:
        w = sh.add_worksheet(title=TAB, rows=20000, cols=len(HEADERS))
        w.update(values=[HEADERS], range_name="A1")
        print(f"  created tab {TAB!r}")
        return w


def snap():
    print("=" * 104)
    print(f"{G.LEAGUE} CLV -- SNAPSHOT. Appends to {TAB}. Places no order.")
    print("=" * 104)

    key = G.key_or_die()
    events, quota = G.fetch_odds(key)
    print(f"\n  QUOTA  used {quota.get('x-requests-used')}   "
          f"remaining {quota.get('x-requests-remaining')}   "
          f"this call {quota.get('x-requests-last')}")
    print(f"  {len(events)} upcoming event(s) from the odds API")

    d, err = G.C.get("/trade-api/v2/markets", series_ticker=G.SERIES,
                     limit=1000)
    if err:
        print(f"  THE KALSHI READ FAILED: {err}. Nothing written.")
        return 1
    mks = (d or {}).get("markets") or []

    rows, diag = G.match_rows(events, mks)
    print(f"  {len(mks)} Kalshi markets -> {len(rows)} matched sides "
          f"({len({r['pair'] for r in rows})} games)")
    print(f"  dropped: {len(diag['no_odds'])} with no odds event, "
          f"{len(diag['amb'])} ambiguous, "
          f"{len(diag['no_sharp_mk'])} with no usable h2h")

    now = datetime.now(timezone.utc)
    out, skipped = [], 0
    for r in rows:
        if r["px"] is None:
            skipped += 1
            continue
        hrs = ""
        if r.get("kickoff") is not None:
            hrs = (r["kickoff"] - now).total_seconds() / 3600.0
            if hrs < MIN_HOURS:
                skipped += 1
                continue
            hrs = round(hrs, 2)
        out.append([
            now.strftime("%Y-%m-%d %H:%M:%S UTC"), G.LEAGUE,
            r["ticker"], r["event"],
            r["pair"], r["team"],
            r["kickoff"].strftime("%Y-%m-%d %H:%M:%S UTC")
            if r.get("kickoff") else "",
            hrs, r["book"], r.get("book_odds", ""),
            round(100.0 * r["fair"], 3), round(100.0 * r["px"], 2),
            r["sz"] if r["sz"] is not None else "",
            round(100.0 * r["spread"], 3),
            round(r["edge"], 3) if r["edge"] is not None else "",
            "yes" if r.get("sharp") else "NO", G.TIE_RATE, M.FEE_COEFF,
            r.get("n", ""),
        ])

    if skipped:
        print(f"  {skipped} side(s) not written: no ask, or already past "
              f"kickoff")
    if not out:
        print("\n  NOTHING TO WRITE. That is a real answer -- no concurrent")
        print("  games, or no asks -- and no row is invented to fill it.")
        return 0

    client = P._get_client()
    sh = client.open_by_key(P.SPREADSHEET_ID)
    w = tab(sh)
    if w is None:
        return 1
    for attempt in (1, 2, 3):
        try:
            w.append_rows(out, value_input_option="USER_ENTERED")
            break
        except Exception as e:
            if attempt == 3:
                print(f"  SHEET WRITE FAILED after 3 tries: {str(e)[:140]}")
                print("  The snapshot is lost. It is not retried silently and")
                print("  not half-written: nothing partial went in.")
                return 1
            time.sleep(3)
    print(f"\n  wrote {len(out)} row(s) to {TAB}")
    ed = sorted(r["edge"] for r in rows if r["edge"] is not None)
    if ed:
        print(f"  edge this snapshot: median {ed[len(ed)//2]:+.2f} pts, "
              f"range {ed[0]:+.2f} to {ed[-1]:+.2f}")
        print("  (both sides of each game are priced, so a NEGATIVE median "
              "is correct)")
    return 0


def fnum(v):
    try:
        x = float(str(v).strip())
        return x if x == x else None
    except (TypeError, ValueError):
        return None


def score():
    print("=" * 104)
    print(f"{G.LEAGUE} CLV -- SCORE. Reads {TAB}. Writes nothing.")
    print("  THE QUESTION: at the snapshot we computed an edge. Did Kalshi's")
    print("  price then move TOWARD our fair value by kickoff, or not?")
    print("=" * 104)

    client = P._get_client()
    sh = client.open_by_key(P.SPREADSHEET_ID)
    try:
        w = sh.worksheet(TAB)
    except Exception:
        print(f"\n  No {TAB} tab yet. Run the snapshot mode first; there is")
        print("  nothing to score and nothing is assumed about what it would")
        print("  have said.")
        return 1
    vals = w.get_all_values()
    if len(vals) < 2:
        print(f"\n  {TAB} is empty apart from the header.")
        return 1
    head, data = vals[0], vals[1:]
    if head != HEADERS:
        print("  HEADER MISMATCH -- the columns are not what this scorer")
        print("  expects, so every index below would read the wrong field.")
        print(f"    on the sheet: {head}")
        return 1
    col = {h: i for i, h in enumerate(head)}

    def g(r, h):
        i = col[h]
        return r[i] if i < len(r) else ""

    by_tk = {}
    for r in data:
        tk = g(r, "Ticker")
        if tk:
            by_tk.setdefault(tk, []).append(r)
    print(f"\n  {len(data)} row(s) over {len(by_tk)} market(s)")

    pairs, single = [], 0
    for tk, rs in by_tk.items():
        rs = [r for r in rs if fnum(g(r, "Hours To Kick")) is not None]
        if len(rs) < 2:
            single += 1
            continue
        rs.sort(key=lambda r: -fnum(g(r, "Hours To Kick")))
        first, last = rs[0], rs[-1]
        e = fnum(g(first, "Edge Pts"))
        p0 = fnum(g(first, "Kalshi Ask c"))
        p1 = fnum(g(last, "Kalshi Ask c"))
        f0 = fnum(g(first, "Fair %"))
        if None in (e, p0, p1, f0):
            continue
        # Movement toward our fair value is positive. If fair was ABOVE the
        # ask we thought it was cheap, so a rising ask is movement toward us;
        # if fair was below, a falling ask is. One signed quantity, so the
        # two directions cannot cancel into a meaningless average.
        toward = (p1 - p0) if f0 >= p0 else (p0 - p1)
        pairs.append(dict(tk=tk, edge=e, p0=p0, p1=p1, f0=f0, toward=toward,
                          h0=fnum(g(first, "Hours To Kick")),
                          h1=fnum(g(last, "Hours To Kick")),
                          pair=g(first, "Pair"), side=g(first, "Side")))

    if single:
        print(f"  {single} market(s) have only one snapshot -- not scorable "
              f"yet, and not counted")
    if not pairs:
        print("\n  NOTHING SCORABLE. Needs at least two snapshots of the same")
        print("  market at different times. No verdict is produced from one.")
        return 0

    mv = sorted(p["toward"] for p in pairs)
    n = len(mv)
    mean = sum(mv) / n
    var = sum((x - mean) ** 2 for x in mv) / (n - 1) if n > 1 else 0.0
    se = (var / n) ** 0.5 if n > 1 else 0.0
    t = (mean / se) if se > 0 else 0.0

    print("\n" + "-" * 104)
    print(f"ALL {n} SCORED SIDES")
    print("-" * 104)
    print(f"  mean move toward our fair value: {mean:+.3f} cents "
          f"(median {mv[n//2]:+.2f})")
    print(f"  standard error {se:.3f}   t = {t:+.2f}")
    print(f"  the project's own bar is |t| >= 2.5, set before any of this")

    # The real test is a GRADIENT: a bigger edge should predict a bigger
    # move. A signal that is real is monotone; noise alternates.
    print("\n" + "-" * 104)
    print("BY EDGE AT SNAPSHOT -- a real signal is a GRADIENT, not one "
          "lucky bucket")
    print("-" * 104)
    buckets = [(-99, -3), (-3, -1), (-1, 1), (1, 3), (3, 99)]
    print(f"  {'edge bucket':<16}{'n':>5}{'mean move':>12}{'median':>10}")
    for lo, hi in buckets:
        sel = [p["toward"] for p in pairs if lo <= p["edge"] < hi]
        if not sel:
            print(f"  {f'{lo} to {hi}':<16}{0:>5}{'--':>12}{'--':>10}")
            continue
        s = sorted(sel)
        print(f"  {f'{lo} to {hi}':<16}{len(s):>5}"
              f"{sum(s)/len(s):>+12.3f}{s[len(s)//2]:>+10.2f}")

    print("\n" + "-" * 104)
    print("THE BIGGEST MOVES, both directions, so the tail is visible")
    print("-" * 104)
    ordered = sorted(pairs, key=lambda p: -p["toward"])
    for p in ordered[:5] + (["..."] if len(ordered) > 10 else []) \
            + ordered[-5:]:
        if p == "...":
            print("  ...")
            continue
        print(f"  {p['pair']:<16}{p['side']:<5} edge {p['edge']:+6.2f}  "
              f"ask {p['p0']:5.1f}c -> {p['p1']:5.1f}c  fair {p['f0']:5.1f}%"
              f"  toward {p['toward']:+6.2f}c  "
              f"({p['h0']:.1f}h -> {p['h1']:.1f}h)")

    print("\n" + "=" * 104)
    print("  HOW TO READ IT, and how not to.")
    print()
    print("  A positive mean with a gradient across the buckets and |t| over")
    print("  2.5 would say the computed fair value carries information about")
    print("  where the price goes. THAT IS NOT PROFIT: nothing was bought,")
    print("  the fee was never paid, and depth was never tested.")
    print()
    print("  A mean near zero, or buckets that alternate in sign, says the")
    print("  gap is noise -- the same answer every other cut of this project")
    print("  has returned, and it would be the cheapest one yet to accept.")
    print("=" * 104)
    return 0


if __name__ == "__main__":
    mode = (sys.argv[1] if len(sys.argv) > 1 else "snap").strip().lower()
    if mode == "score":
        raise SystemExit(score())
    if mode == "snap":
        raise SystemExit(snap())
    print(f"unknown mode {mode!r} -- use 'snap' or 'score'")
    raise SystemExit(2)
