"""Bet by bet: what would have hit, what would have missed, day by day.

Aggregates are easy to mistrust and easy to get wrong -- a +$23/bet pocket
passed four statistical filters in this project and was a bug. A ledger is
harder to fool: every bet has a date, a price, an outcome and a dollar
figure, and the running total either grows or it does not.

Four rules, each a thing someone might actually have done:

  favourite   buy whichever side is quoted above 80c at T-3
  longshot    buy whichever side is quoted below 20c at T-3
  fallen      the frozen spec: was 60c+ earlier, under 50c now
  everything  every takeable quote at T-3, both sides

Output is per DAY, because 70 days of individual bets is thousands of lines
nobody reads. Hits, misses, hit rate, the day's dollars and the running
total -- so a streak, a single catastrophic day, or a slow bleed are all
visible as shapes rather than hidden in a mean.

Prices are real asks. The fee is the exact 0.07*C*P*(1-P), unrounded, now
that Kalshi's own fills confirmed the rate to within a third of a cent and
showed the ceiling was mine, not theirs.
"""

import collections
import statistics as st
import sys

import predictor as P

OFFSETS = (14, 12, 9, 6, 3, 1)
ENTRY = 3
STAKE = 10.0


def _f(v):
    try:
        x = float(v)
        return x if x == x else None
    except (TypeError, ValueError):
        return None


def pnl(price, won):
    """Exact Kalshi economics: fee charged on entry, no rounding up.

    Verified against 192 real fills -- the 0.07 rate matched to a median of
    16 millionths of a dollar, and the ceil-to-the-cent I had been using
    overstated every bet by a median of 0.38c."""
    c = int(STAKE / price)
    if c < 1:
        return None
    fee = 0.07 * c * price * (1 - price)
    return (c if won else 0) - (c * price + fee)


def load(sh, name):
    rows = sh.worksheet(name).get_all_values()
    hi = {h: i for i, h in enumerate(rows[0])}
    out = []
    for r in rows[1:]:
        if not r or not r[0]:
            continue
        def c(k):
            i = hi.get(k)
            return r[i] if i is not None and i < len(r) else ""
        res = str(c("Result")).lower().strip()
        if res not in ("yes", "no"):
            continue
        path = {}
        for o in OFFSETS:
            b, a = _f(c(f"bid{o}")), _f(c(f"ask{o}"))
            if b is not None and a is not None and 0 < b <= a < 1:
                path[o] = (b, a)
        if ENTRY not in path:
            continue
        out.append(dict(tk=r[0], ser=str(c("Series")),
                        close=str(c("Close Time")), yes=(res == "yes"),
                        path=path))
    return out


def bets_for(M, rule):
    """(day, ticker, side, price, won, dollars) for one rule."""
    out = []
    for m in M:
        b, a = m["path"][ENTRY]
        for side in ("YES", "NO"):
            price = a if side == "YES" else 1 - b
            if not (0.02 < price < 0.98):
                continue
            if rule == "favourite" and price <= 0.80:
                continue
            if rule == "longshot" and price >= 0.20:
                continue
            if rule == "fallen":
                if price >= 0.50:
                    continue
                peak = None
                for o in (14, 12, 9, 6):
                    q = m["path"].get(o)
                    if not q:
                        continue
                    mid = (q[0] + q[1]) / 2.0
                    if side == "NO":
                        mid = 1 - mid
                    peak = mid if peak is None else max(peak, mid)
                if peak is None or peak < 0.60:
                    continue
            won = m["yes"] if side == "YES" else (not m["yes"])
            v = pnl(price, won)
            if v is None:
                continue
            out.append((m["close"][:10], m["tk"], side, price, won, v))
    return out


def show(bets, rule):
    print("=" * 96)
    print(f"RULE: {rule}     {len(bets)} bets")
    print("=" * 96)
    if not bets:
        print("  no bets qualified")
        return
    days = collections.defaultdict(list)
    for d, tk, side, px, won, v in bets:
        days[d].append((won, v, px))
    run = 0.0
    print(f"  {'day':<12}{'bets':>6}{'hit':>6}{'miss':>6}{'hit%':>7}"
          f"{'avg paid':>10}{'day $':>10}{'running $':>12}")
    for d in sorted(days):
        rows = days[d]
        h = sum(1 for w, _, _ in rows if w)
        s = sum(v for _, v, _ in rows)
        run += s
        print(f"  {d:<12}{len(rows):>6}{h:>6}{len(rows)-h:>6}"
              f"{100*h/len(rows):>6.0f}%{100*st.mean([p for _,_,p in rows]):>9.1f}c"
              f"{s:>+10.2f}{run:>+12.2f}")
    h = sum(1 for _, _, _, _, w, _ in bets if w)
    tot = sum(v for *_, v in bets)
    up = sum(1 for d in days if sum(v for _, v, _ in days[d]) > 0)
    print(f"\n  TOTAL {h}/{len(bets)} hit ({100*h/len(bets):.1f}%)   "
          f"${tot:+.2f}   ${tot/len(bets):+.3f}/bet")
    print(f"  profitable days {up}/{len(days)}   "
          f"best day ${max(sum(v for _,v,_ in days[d]) for d in days):+.2f}   "
          f"worst day ${min(sum(v for _,v,_ in days[d]) for d in days):+.2f}")
    print("  first 8 individual bets, so the arithmetic is checkable by hand:")
    for d, tk, side, px, won, v in bets[:8]:
        print(f"    {d}  {tk[:26]:<28} {side:<4} paid {100*px:>5.1f}c  "
              f"{'HIT ' if won else 'miss'}  ${v:>+8.2f}")


def main():
    tab = sys.argv[1] if len(sys.argv) > 1 else "M15H"
    rules = sys.argv[2:] or ["favourite", "longshot", "fallen", "everything"]
    sh = P._get_client().open_by_key(P.SPREADSHEET_ID)
    M = load(sh, tab)
    print(f"{tab}: {len(M)} settled markets with a T-{ENTRY} quote\n")
    for rule in rules:
        show(bets_for(M, rule), rule)
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
