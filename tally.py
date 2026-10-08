"""The live record: what was bet, what settled, what it actually made.

A high win rate is the thing this strategy is most likely to be misread by,
and the misreading is not careless -- it is what the numbers look like. The
narrow slice buys at about 93c, so break-even is 92.7%. Winning 95% of your
bets feels like winning almost everything, and it is: the margin between
triumphant and losing is three percentage points wide, and no run of
notifications can show you which side of it you are on. Twenty wins and two
losses reads as a hot streak and is a loss.

So this reports the only three numbers that answer it:

  WIN RATE AGAINST ITS OWN BREAK-EVEN LINE, computed from the prices actually
  paid rather than from 50% or from the historical average. A bet at 95c and a
  bet at 90c need different things to be worth making.

  NET DOLLARS FROM SETTLEMENTS, read from Kalshi rather than inferred from the
  order rows. What an order cost is knowable at entry; what it returned is not,
  and only the exchange knows which markets paid.

  HOW UNCERTAIN BOTH ARE. With a few dozen bets the error bar on the win rate
  is wider than the margin being measured, so the honest output is an interval
  that will contain both "clearly working" and "clearly not" for some time.
  Printing a point estimate alone, at this sample size, would be the same
  mistake as reading the notifications.

NOT A VERDICT ON THE RULE. The frozen specs score on their own schedule and
this changes nothing about them. This is bookkeeping: what the account did.

Read-only.
"""

import collections
import math
import sys
import time

import requests

import control
import live
import predictor as P

# When the narrow slice went live. Earlier rows belong to retired strategies
# and mixing them in would describe a bot that no longer exists.
START = "2026-10-07 15:49"


def _f(v):
    try:
        x = float(v)
        return x if x == x else None
    except (TypeError, ValueError):
        return None


def get(path, **params):
    """Signature path and URL path differ; KALSHI_BASE already has the prefix."""
    hdrs = P._kalshi_headers("GET", "/trade-api/v2" + path)
    if not hdrs:
        return None
    try:
        r = requests.get(f"{P.KALSHI_BASE}{path}", headers=hdrs,
                         params=params or None, timeout=25)
        return r.json() if r.ok else None
    except Exception:
        return None


def wilson(k, n):
    """95% interval for a proportion, which a bare ratio hides at small n.

    Wilson rather than the textbook normal interval: near 95% the normal one
    runs past 1.0 and understates how little a short run tells you, which is
    the specific error this whole file exists to avoid.
    """
    if not n:
        return 0.0, 1.0
    z = 1.96
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return max(0.0, c - h), min(1.0, c + h)


def rows_since(start):
    sh = P._get_client().open_by_key(P.SPREADSHEET_ID)
    rows = sh.worksheet(live.LIVE_SHEET).get_all_values()
    if len(rows) < 2:
        return []
    h = {k: i for i, k in enumerate(rows[0])}

    def c(r, k):
        i = h.get(k)
        return r[i] if i is not None and i < len(r) else ""

    out = []
    for r in rows[1:]:
        if not r or not r[0] or str(r[0]) < start:
            continue
        if str(c(r, "Status")).strip().upper() != "PLACED":
            continue
        out.append(dict(ts=str(r[0]), ticker=str(c(r, "Ticker")),
                        side=str(c(r, "Side")),
                        entry=_f(c(r, "Entry ¢")),
                        contracts=_f(c(r, "Contracts")),
                        cost=_f(c(r, "Cost $"))))
    return out


def main():
    bets = rows_since(START)
    print("=" * 94)
    print(f"LIVE RECORD since {START} UTC -- narrow slice, $4 a bet")
    print("=" * 94)
    if not bets:
        print("  No filled bets recorded yet.")
        return 0

    # Settlements, keyed by ticker, for what each market actually returned.
    d = get("/portfolio/settlements", limit=200)
    setts = {}
    for s in ((d or {}).get("settlements") or []):
        tk = str(s.get("ticker") or "")
        rev = _f(s.get("revenue_dollars"))
        if rev is None:
            rev = _f(s.get("revenue"))
            if rev is not None and abs(rev) > 100:
                rev = rev / 100.0
        setts[tk] = dict(result=str(s.get("market_result")
                                    or s.get("result") or "").lower(),
                         rev=rev or 0.0)

    settled = [b for b in bets if b["ticker"] in setts]
    pending = [b for b in bets if b["ticker"] not in setts]
    won = [b for b in settled if setts[b["ticker"]]["rev"] > 0]
    lost = [b for b in settled if setts[b["ticker"]]["rev"] <= 0]

    staked = sum(b["cost"] or 0 for b in settled)
    returned = sum(setts[b["ticker"]]["rev"] for b in settled)
    net = returned - staked

    print(f"  bets placed       {len(bets)}")
    print(f"  settled           {len(settled)}"
          + (f"   ({len(pending)} still open)" if pending else ""))
    if not settled:
        print("  Nothing has settled yet, so there is no result to read.")
        return 0

    pxs = [b["entry"] / 100.0 for b in settled if b["entry"]]
    avg = sum(pxs) / len(pxs) if pxs else 0.93
    need = avg + 0.07 * avg * (1 - avg)      # price plus the fee, as a rate
    wr = len(won) / len(settled)
    lo, hi = wilson(len(won), len(settled))

    print(f"\n  won               {len(won)} of {len(settled)}  "
          f"({100*wr:.1f}%)")
    print(f"  break-even needs  {100*need:.1f}%  "
          f"(at the {100*avg:.1f}c average actually paid)")
    print(f"  staked            ${staked:,.2f}")
    print(f"  returned          ${returned:,.2f}")
    print(f"  NET               ${net:+,.2f}   "
          f"({100*net/staked:+.2f}% of stake)" if staked else "")

    print(f"\n  95% interval on the win rate: {100*lo:.1f}% to {100*hi:.1f}%")
    if lo > need:
        print("  Even the bottom of that interval clears break-even.")
    elif hi < need:
        print("  Even the TOP of that interval is below break-even.")
    else:
        print(f"  Break-even ({100*need:.1f}%) sits INSIDE the interval, so")
        print("  this record is consistent with a real edge AND with no edge")
        print("  at all. That is not a disappointing result -- it is what a")
        print("  sample this size can say, and it will stay true for a while.")
        n_need = 0
        if 0 < wr < 1 and abs(wr - need) > 1e-9:
            # Rough n for the interval half-width to clear the gap.
            gap = abs(wr - need)
            n_need = int((1.96 ** 2) * wr * (1 - wr) / (gap ** 2))
            print(f"  At the current margin, roughly {n_need:,} settled bets")
            print("  would be needed before the interval excludes break-even.")

    if lost:
        print(f"\n  the {len(lost)} loss(es) -- these are what decide it:")
        for b in lost[:8]:
            print(f"    {b['ts']:<22} {b['ticker']:<34} "
                  f"paid {b['entry']:.0f}c  -${b['cost']:.2f}")

    bal = control.fetch_balance()
    if bal is not None:
        print(f"\n  account balance   ${bal:,.2f}")

    print("\n" + "=" * 94)
    print("  A win rate near 95% is what this rule looks like whether or not")
    print("  it is profitable, because break-even is 92.7%. The notifications")
    print("  cannot tell you which; only the net and the interval can.")
    print("=" * 94)
    return 0


if __name__ == "__main__":
    sys.exit(main())
