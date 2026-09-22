"""Every Kapelame paper trade, priced as a flat $10 bet you could really place.

WHY A SCRIPT RATHER THAN A SPREADSHEET EXPORT. Grading needs two tabs that
move at different speeds: trades land when a session ends, settlements when
settle_fetch runs. An export taken between the two shows trades with no
outcome, which reads exactly like a bot that has gone quiet. Reading the
live sheet removes the gap.

WHAT THE NUMBERS ARE, and why they are not the bot's own P&L:

  THE BOT BOOKS THE MID. paper_trader.py calls open_position(..., mid) and
  stores that as entry_price. Nobody can buy at the mid, so a penny is added
  to reach the ask. On a 2c spread that is the real cost of crossing.

  THE STAKE IS FLAT. The bot sizes off a simulated $100 bankroll with a
  Kelly fraction; this recomputes contracts from a fixed $10 so the rows are
  comparable to each other and to a real account.

  THE FEE IS KALSHI'S, ceil(0.07 * n * p * (1-p)) to the cent, charged on
  entry only -- a winner settles at $1.00 with nothing left to sell.

  THE OUTCOME IS KALSHI'S OWN `result`, joined by ticker, never the bot's
  self-reported settled field. That field has agreed 25 out of 25 so far,
  which is a reason to trust it and not a reason to stop checking.

Splits the discovery sample from everything after it, because the first
sample is what produced the number being tested and cannot also confirm it.

Read-only. Places nothing, writes nothing.
"""

import collections
import json
import math
import random
import statistics as st
import sys
from datetime import datetime, timedelta

FLAT = 10.0
SLIP = 0.01
TZ = -5                      # CT
DISCOVERY = ("20260922-1339", "20260922-1410")


def fee(n, p):
    return math.ceil(round(0.07 * n * p * (1 - p), 9) * 100) / 100.0


def priced(entry, won):
    """(contracts, cost, fee, payout, pnl) for a flat $10 bet at the ask."""
    ask = min((entry or 0) + SLIP, 0.99)
    c = int(FLAT / ask)
    if ask <= 0 or c < 1:
        return None
    f = fee(c, ask)
    cost = c * ask + f
    pay = c if won else 0
    return c, cost, f, pay, pay - cost


def load():
    import predictor as P
    client = P._get_client()
    sh = client.open_by_key(P.SPREADSHEET_ID)

    srows = sh.worksheet("Settled").get_all_values()
    si = {h: i for i, h in enumerate(srows[0])}
    settled = {}
    for r in srows[1:]:
        t = (r[si.get("Ticker", 0)] or "").strip()
        v = (r[si.get("Result", 1)] or "").strip().lower()
        if t and v in ("yes", "no"):
            settled[t] = v

    rows = sh.worksheet("Kapelame Paper").get_all_values()
    # Last copy wins: a trade is written once unsettled and again once it
    # settles, so keeping both would double-count every bet.
    latest = {}
    for r in rows[1:]:
        if len(r) < 4 or not str(r[2]).startswith("db:trades"):
            continue
        try:
            d = json.loads(r[3])
        except Exception:
            continue
        d["_run"] = r[0]
        latest[(r[0], d.get("id"))] = d
    trades = sorted(latest.values(), key=lambda d: d.get("ts_unix") or 0)
    return trades, settled


def table(trades, settled):
    print(f"{'#':>3} {'CT':>5} {'run':>5} {'coin':>4} {'sd':>3} {'mid':>6} "
          f"{'ask':>6} {'ct':>3} {'cost':>6} {'fee':>5} {'setl':>4} {'W/L':>4} "
          f"{'payout':>7} {'P&L':>7} {'running':>8}")
    print("-" * 96)
    run = 0.0
    graded = []
    pending = 0
    for i, d in enumerate(trades, 1):
        res = settled.get((d.get("ticker") or "").strip())
        ts = d.get("ts_unix")
        ct = (datetime.utcfromtimestamp(ts) + timedelta(hours=TZ)).strftime(
            "%H:%M") if ts else "  -  "
        mid = d.get("entry_price") or 0
        ask = min(mid + SLIP, 0.99)
        if res is None:
            pending += 1
            print(f"{i:>3} {ct:>5} {d.get('_run','')[-4:]:>5} "
                  f"{d.get('asset',''):>4} {d.get('side',''):>3} "
                  f"{mid*100:>5.1f}c {ask*100:>5.1f}c "
                  f"{'':>3} {'':>6} {'':>5} {'--':>4} {'open':>4}")
            continue
        won = (res == "yes") if d.get("side") == "YES" else (res == "no")
        pr = priced(mid, won)
        if not pr:
            continue
        c, cost, f, pay, pnl = pr
        run += pnl
        d["_won"] = won
        graded.append(d)
        print(f"{i:>3} {ct:>5} {d.get('_run','')[-4:]:>5} "
              f"{d.get('asset',''):>4} {d.get('side',''):>3} "
              f"{mid*100:>5.1f}c {ask*100:>5.1f}c {c:>3} {cost:>6.2f} "
              f"{f:>5.2f} {res.upper():>4} {('WIN' if won else 'LOSS'):>4} "
              f"{pay:>7.2f} {pnl:>+7.2f} {run:>+8.2f}")
    print("-" * 96)
    w = sum(d["_won"] for d in graded)
    n = len(graded)
    print(f"{w}W / {n-w}L = {w/max(n,1)*100:.1f}%    TOTAL {run:+.2f}    "
          f"({pending} still open)")
    return graded


def split(graded):
    def block(name, sub):
        if not sub:
            print(f"\n{name}: none yet")
            return
        w = sum(d["_won"] for d in sub)
        n = len(sub)
        tot = sum(priced(d["entry_price"], d["_won"])[4] for d in sub)
        imp = st.mean([d["entry_price"] for d in sub])
        byw = collections.defaultdict(list)
        for d in sub:
            byw[(d.get("ticker") or "")[-8:]].append(d["_won"])
        rng = random.Random(17)
        ws = list(byw.values())
        ms = []
        for _ in range(20000):
            pk = [ws[rng.randrange(len(ws))] for _ in range(len(ws))]
            ms.append(sum(sum(v) for v in pk) / sum(len(v) for v in pk))
        ms.sort()
        print(f"\n{name}")
        print(f"   {w}/{n} = {w/n*100:.1f}%   over {len(ws)} windows   "
              f"$10-basis {tot:+.2f}")
        print(f"   implied {imp*100:.1f}%   window-clustered 95% CI "
              f"{ms[500]*100:.1f}% to {ms[19500]*100:.1f}%")
        cheap = [d for d in sub if d["entry_price"] < 0.35]
        if cheap:
            cw = sum(d["_won"] for d in cheap)
            ct = sum(priced(d["entry_price"], d["_won"])[4] for d in cheap)
            print(f"   of those, entries under 35c: {cw}/{len(cheap)} "
                  f"({cw/len(cheap)*100:.0f}%), {ct:+.2f} -- "
                  f"{ct/tot*100:.0f}% of the total" if tot else "")

    print("\n" + "=" * 96)
    block("DISCOVERY SAMPLE (produced the claim; cannot confirm it)",
          [d for d in graded if d["_run"] in DISCOVERY])
    block("OUT OF SAMPLE (everything after)",
          [d for d in graded if d["_run"] not in DISCOVERY])
    print("\n" + "=" * 96)


def main():
    trades, settled = load()
    print(f"{len(trades)} Kapelame trades, {len(settled)} settlements known\n")
    graded = table(trades, settled)
    split(graded)
    print("cost = contracts x ask + fee    fee = ceil(0.07 x ct x ask x (1-ask))")
    print("payout = $1.00 per contract if the bet won, else $0")
    return 0


if __name__ == "__main__":
    sys.exit(main())
