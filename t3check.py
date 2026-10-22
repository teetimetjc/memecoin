"""Did the T-3 session actually bet, and how did its fills compare? Read-only.

WHY A SEPARATE FILE. Two strategies now write to one Live Bets tab, and
every existing reader -- tally, patterns, rotation -- treats that tab as one
record. Blending a $5 T-9 rule with a $3 T-3 rule into one win rate would
describe neither, which is the same mistake the stake eras made before
stake_of existed to separate them.

HOW THE TWO ARE TOLD APART. Nothing stores the strategy name, but the Order
ID carries the ORDER_PREFIX: the frozen spec writes 'slice-...' and the T-3
session writes 't3-...'. That is enough and it needed no new column.

WHAT IT REPORTS, and the reason each matters:

  WHETHER T-3 PLACED ANYTHING AT ALL. The backtest put in-band markets at
  T-3 at roughly 5% of market-windows, so a quiet session is expected and a
  silent zero has to be distinguishable from a broken read -- which this
  project has now confused four separate times in two days.

  FILL RATE AND SLIP, side by side with the T-9 bot over the same hours.
  This is the whole point of running it live rather than shadowing it. A
  quote backtest cannot see an order that fills nothing because the ask
  moved past the limit, and at T-3 there are six fewer minutes for a seller
  to appear. If T-3 fills materially worse than T-9, the +3.13% is
  unreachable no matter how real the mispricing is.

  WHAT THE DEDUPE COST. The T-3 session skips markets the T-9 session
  already holds, so its live sample is NOT the cohort the backtest scored
  -- it is weighted towards markets that drifted into the band late. That
  is a known bias, it is stated rather than buried, and the count of skips
  is the size of it.

Read-only. Places nothing.
"""
import collections

import live
import tally as T


def prefix_of(oid):
    s = str(oid or "").strip()
    return s.split("-", 1)[0] if "-" in s else ""


def main():
    sh = T.P._get_client().open_by_key(T.P.SPREADSHEET_ID)
    rows = sh.worksheet(live.LIVE_SHEET).get_all_values()
    if len(rows) < 2:
        print("  no rows")
        return 0
    h = {k: i for i, k in enumerate(rows[0])}

    def c(r, k):
        i = h.get(k)
        return r[i] if i is not None and i < len(r) else ""

    by = collections.defaultdict(list)
    for r in rows[1:]:
        if not r or not r[0]:
            continue
        p = prefix_of(c(r, "Order ID")) or "(none)"
        by[p].append(r)

    print("=" * 96)
    print("WHO BET WHAT -- the Live Bets tab split by strategy, via the "
          "Order ID prefix")
    print("=" * 96)
    for p in sorted(by, key=lambda k: -len(by[k])):
        g = by[p]
        placed = [r for r in g if str(c(r, "Status")).strip().upper() == "PLACED"]
        label = {"slice": "slice  (frozen T-9 spec)",
                 "t3": "t3     (T-3 test, $3)"}.get(p, p)
        print(f"  {label:<28} {len(g):>4} rows, {len(placed):>4} PLACED")
        if placed:
            last = sorted(placed, key=lambda r: T.norm_ts(r[0]))[-1]
            print(f"  {'':<28} latest {T.norm_ts(last[0])}  "
                  f"{c(last, 'Ticker')}  entry {c(last, 'Entry ¢')}c")

    t3 = [r for r in by.get("t3", [])
          if str(c(r, "Status")).strip().upper() == "PLACED"]
    print()
    if not t3:
        print("  THE T-3 SESSION PLACED NOTHING.")
        print("  That is the expected outcome for a short run, not evidence of")
        print("  a fault: the backtest found in-band markets at T-3 in roughly")
        print("  5% of market-windows, on the order of one an hour across all")
        print("  five coins, and the dedupe removes the ones the T-9 bot took.")
        print("  It becomes a fault only if a full day passes with nothing.")
        return 0

    print("-" * 96)
    print("T-3 FILLS -- the thing no backtest can tell us")
    print("-" * 96)
    print(f"  {'when':<18} {'ticker':<26} {'entry':>6} {'limit':>6} "
          f"{'fills':>6} {'slip':>7}")
    fills = T.actual_costs()
    slips, got, miss = [], 0, 0
    for r in sorted(t3, key=lambda r: T.norm_ts(r[0])):
        oid = str(c(r, "Order ID")).strip()
        f = fills.get(oid)
        entry = T._f(c(r, "Entry ¢"))
        lim = T._f(c(r, "Limit ¢"))
        if f and f.get("n"):
            px = 100.0 * (f["cost"] / f["n"]) if f["n"] else None
            got += 1
            slip = (px - entry) if (px is not None and entry is not None) else None
            if slip is not None:
                slips.append(slip)
            shown = f"{px:.1f}c" if px is not None else "--"
        else:
            miss += 1
            shown, slip = "none", None
        print(f"  {T.norm_ts(r[0]):<18} {str(c(r, 'Ticker'))[:26]:<26} "
              f"{entry if entry is not None else '--':>6} "
              f"{lim if lim is not None else '--':>6} {shown:>6} "
              + (f"{slip:>+6.1f}c" if slip is not None else "      --"))
    n = got + miss
    print()
    print(f"  filled {got} of {n} ({100.0*got/n:.0f}%)")
    if slips:
        import statistics as st
        print(f"  mean slip vs the quote {st.mean(slips):+.2f}c")
    print()
    print("  COMPARE THE FILL RATE AGAINST 78.7%, which is what the T-9 bot")
    print("  manages. A markedly worse rate here is the finding, and it would")
    print("  mean the T-3 mispricing is real but unreachable -- the orders")
    print("  that miss are the favourites strengthening, which is exactly the")
    print("  cohort that settles YES 98.4% of the time.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
