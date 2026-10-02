"""What the paper bot's buckets would actually have PAID, in dollars.

The phone digest reports win rates: "UNSURE 197/427 46% | CERTAIN 113/208
54%". Fifty-four percent reads like a lead, and a win rate without the price
paid is the most misleading number in this project -- the favourites rule
hit 91.3% and still lost $0.122 a bet, because it needed 92.5%.

So this re-reads every settled paper trade from the bot tabs and prices it:
what was paid, what break-even would have required, and what a flat $10 bet
returned. Same splits as the digest, so the two can be compared line for
line.

THE PRICING IS NOT THE BOT'S. Three corrections, all of them already in
botrun.ten_dollar_pnl and all of them load-bearing:

  entry_price IS ALWAYS THE YES MID, whichever side was taken, so buying NO
  costs 1 minus it. Treating it as the price paid on both sides reported
  +$17 on a trade whose truth was -$1.

  A MID IS NOT A PRICE ANYONE CAN BUY AT, so a penny reaches the ask.

  THE BOT SIZES OFF A SIMULATED BANKROLL, not a flat stake, so the contract
  count is recomputed at $10.

TWO FEES ARE PRINTED. The exact rate 0.07 * C * P * (1-P), verified against
174 real taker fills, and the ceil-to-the-cent version the bot uses, which
was measured to overstate by a median of 0.38c. The exact one is the answer;
the pair is there so the difference cannot hide a conclusion.

AND THE BREAK-EVEN LINE IS PRINTED BESIDE EVERY WIN RATE, because that is
the comparison the digest cannot make. A bucket that wins 54% at an average
price of 60c is not a lead, it is a 7-point shortfall.

Read-only.
"""

import collections
import json
import math
import statistics as st
import sys
from datetime import datetime

import predictor as P

TABS = ("EdgeHunter Paper", "Kapelame Paper")
STAKE = 10.0
ASK_SLIP = 0.01
# The runs that produced the original 72% claim. A number cannot confirm
# itself, so they are reported separately rather than mixed in.
DISCOVERY_RUNS = ("20260922-1339", "20260922-1410")


def _f(v):
    try:
        x = float(v)
        return x if x == x else None
    except (TypeError, ValueError):
        return None


def price_paid(y, side):
    """What the trade cost per contract, including the penny to the ask."""
    if y is None:
        return None
    pr = (y if side == "YES" else 1.0 - y) + ASK_SLIP
    return min(pr, 0.99) if pr > 0 else None


def payout(pr, won, exact=True):
    c = int(STAKE / pr)
    if c < 1:
        return None, 0
    raw = 0.07 * c * pr * (1 - pr)
    fee = raw if exact else math.ceil(round(raw, 9) * 100) / 100.0
    return (c if won else 0) - (c * pr + fee), c


def cse(groups, n):
    G = len(groups)
    if G < 2 or not n:
        return None
    m = sum(sum(v) for v in groups.values()) / n
    ss = sum((sum(v) - len(v) * m) ** 2 for v in groups.values())
    return math.sqrt((G / (G - 1.0)) * ss / (n * n))


def load():
    sh = P._get_client().open_by_key(P.SPREADSHEET_ID)
    out = []
    for tab in TABS:
        try:
            ws = sh.worksheet(tab)
            rows = ws.get_all_values()
        except Exception as e:
            print(f"  {tab}: could not read ({str(e)[:50]})")
            continue
        # A trade is written once unsettled and again when it settles, so
        # the LAST copy of each (run, id) is the real one. Counting both
        # would inflate the record with phantom losses.
        latest = {}
        for r in rows[1:]:
            if len(r) < 4 or not str(r[2]).startswith("db:trades"):
                continue
            try:
                d = json.loads(r[3])
            except Exception:
                continue
            latest[(r[0], d.get("id"))] = (r[0], d)
        kept = 0
        for (run, _id), (run2, d) in latest.items():
            s = d.get("settled")
            if not s or str(s).lower() in ("none", "null"):
                continue
            y = _f(d.get("entry_price"))
            side = str(d.get("side") or "YES").upper()
            pr = price_paid(y, side)
            if pr is None:
                continue
            won = (_f(d.get("pnl")) or 0.0) > 0
            ex, c = payout(pr, won, exact=True)
            ce, _ = payout(pr, won, exact=False)
            if ex is None:
                continue
            mp = _f(d.get("model_p"))
            mkt = y if side == "YES" else (None if y is None else 1.0 - y)
            pwin = None if mp is None else (mp if side == "YES"
                                            else 1.0 - mp)
            ts = _f(d.get("ts_unix"))
            z = _f(d.get("z_score"))
            tk = str(d.get("ticker") or "")
            out.append(dict(
                tab=tab, run=run2, won=won, pr=pr, ex=ex, ce=ce,
                z=(abs(z) if z is not None else None),
                gap=(None if (pwin is None or mkt is None) else pwin - mkt),
                night=(None if not ts else not (8 <= (
                    (datetime.utcfromtimestamp(ts).hour - 5) % 24) < 22)),
                # Cluster on the market's window: six strikes of one event
                # settling together are one observation, not six.
                win="-".join(tk.split("-")[:2]) if tk else str(ts),
            ))
            kept += 1
        print(f"  {tab}: {kept} settled trades priced")
    return out


def rep(rows, label):
    n = len(rows)
    if n < 8:
        print(f"  {label:<34} n={n:<5} too few")
        return
    w = sum(1 for r in rows if r["won"])
    mp = st.mean([r["pr"] for r in rows])
    # Break-even: the win rate at which a flat $10 bet returns zero at this
    # average price, fee included.
    c = int(STAKE / mp)
    fee = 0.07 * c * mp * (1 - mp)
    need = (c * mp + fee) / c if c else 0.0
    g = collections.defaultdict(list)
    for r in rows:
        g[r["win"]].append(r["ex"])
    mean = sum(r["ex"] for r in rows) / n
    se = cse(g, n)
    t = (mean / se) if se else 0.0
    mce = sum(r["ce"] for r in rows) / n
    flag = ""
    if mean > 0:
        flag = "  <== POSITIVE" if t > 2.0 else "  (positive, t<2)"
    print(f"  {label:<34} n={n:<5} won {100*w/n:>4.1f}%  paid {100*mp:>4.1f}c"
          f"  needs {100*need:>4.1f}%  ${mean:>+6.3f}/bet  t={t:>+5.1f}"
          f"  (ceil ${mce:>+6.3f}){flag}")


def main():
    H = load()
    if not H:
        print("no settled paper trades found")
        return 0
    print("\n" + "=" * 118)
    print("WHAT THE PAPER BUCKETS WOULD HAVE PAID  ($10 flat, exact fee, "
          "1c slip to the ask)")
    print("=" * 118)
    print("  'needs' is the win rate that breaks even at the price actually "
          "paid. That is the")
    print("  comparison the phone digest cannot make.\n")

    rep(H, "ALL settled")
    since = [r for r in H if r["run"] not in DISCOVERY_RUNS]
    rep(since, "SINCE (excl. discovery runs)")
    print()
    rep([r for r in since if r["night"] is False], "DAY")
    rep([r for r in since if r["night"] is True], "NIGHT")
    print()
    rep([r for r in since if r["z"] is not None and r["z"] < 0.6], "UNSURE")
    rep([r for r in since if r["z"] is not None and r["z"] >= 0.6], "CERTAIN")
    print()
    rep([r for r in since if r["gap"] is not None and r["gap"] < 0.20],
        "AGREES")
    rep([r for r in since if r["gap"] is not None and r["gap"] >= 0.20],
        "DIFFERS")

    # CERTAIN is the bucket that looks like a lead on the phone, so it gets
    # taken apart by price. If 54% is real but bought at 60c, the money is
    # in the price, not the forecast.
    cert = [r for r in since if r["z"] is not None and r["z"] >= 0.6]
    if cert:
        print("\n" + "=" * 118)
        print("CERTAIN, BY WHAT IT COST  (the 54% is a win rate; this is "
              "the bill)")
        print("=" * 118)
        for lo, hi in ((0.0, 0.35), (0.35, 0.50), (0.50, 0.65),
                       (0.65, 0.80), (0.80, 1.00)):
            rep([r for r in cert if lo <= r["pr"] < hi],
                f"  paid {100*lo:.0f}-{100*hi:.0f}c")
    print("\n" + "=" * 118)
    print("A win rate below its break-even line is a loss however high it "
          "looks: the")
    print("favourites rule hit 91.3% and lost $0.122 a bet, because it "
          "needed 92.5%.")
    print("=" * 118)
    return 0


if __name__ == "__main__":
    sys.exit(main())
