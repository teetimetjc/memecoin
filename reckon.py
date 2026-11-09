"""The whole account, from the exchange's own books. Read-only.

WHY THIS EXISTS. Every report in this project measures ONE strategy over
ONE window, and the dashboard can be filtered to a single coin. None of
them answer the question the account holder actually has, which is "am I up
or down, in total, on everything". That number has been inferred from a
filtered view of the good part, and inferring it is how someone ends up
believing the live strategy's +$50 is their balance.

IT IS COMPUTED TWO INDEPENDENT WAYS, and both are printed even when they
disagree -- especially when they disagree.

  1. THE CASH WAY, which cannot be argued with.

         net = balance now + everything withdrawn - everything deposited

     This is the only figure that is true by construction. It owes nothing
     to our sheet, nothing to our idea of which bets belong to which
     strategy, and nothing to a settlement join. If money went in and less
     came out, this says so.

  2. THE TRADING WAY, from the exchange's fills and settlements.

         net = sum(settlement revenue) - sum(fill cost including fees)

     This covers every order the account made, including the ones placed by
     hand in the app, which our sheet never saw.

A GAP BETWEEN THEM IS INFORMATION, NOT AN ERROR TO HIDE. Open positions,
unsettled markets, money parked in the Invest instance and any fee the
fills do not carry all show up as a difference. The gap is printed with the
most likely causes beside it rather than quietly reconciled away.

THEN IT SPLITS BY STRATEGY, from our sheet, and this part is the LEAST
trustworthy of the three -- it depends on the Strategy column, which live.py
only began writing on 2026-10-23, and on inference before that. So it is
reported last, under its own caveat, and the two totals above do not depend
on it.

WHAT THIS DELIBERATELY DOES NOT DO: judge anything. No verdict, no ROI
target, no advice. It states the position.

Read-only. Places no order, writes no row, changes no setting.
"""
import collections
import time

import predictor as P
import tally as T
import coincut as CC


def pages(path, key, limit=200, cap=60):
    """Every page of a cursor-paginated endpoint, not just the first.

    Reading one page and calling it 'all time' is a mistake this project has
    made twice -- settlements and fills both -- and both times the total
    silently slid as new rows pushed old ones off the back.
    """
    out, cur, n = [], None, 0
    while n < cap:
        p = dict(limit=limit)
        if cur:
            p["cursor"] = cur
        d = T.get(path, **p)
        rows = (d or {}).get(key) or []
        out.extend(rows)
        cur = (d or {}).get("cursor")
        n += 1
        if not cur or not rows:
            break
        time.sleep(0.15)
    return out, n


def f(v, default=None):
    try:
        x = float(v)
        return x if x == x else default
    except (TypeError, ValueError):
        return default


def money(row, *names):
    """Dollars from whichever field exists. _dollars is dollars; bare is cents."""
    for k in names:
        if k.endswith("_dollars") or k.endswith("_dollars_fp"):
            v = f(row.get(k))
            if v is not None:
                return v
    for k in names:
        v = f(row.get(k))
        if v is not None:
            return v / 100.0
    return None


def main():
    print("=" * 96)
    print("THE WHOLE ACCOUNT -- every dollar in and out, from Kalshi's books")
    print("  Two independent reckonings. Read-only.")
    print("=" * 96)

    # ------------------------------------------------------- 1. the cash way
    bal_d = T.get("/portfolio/balance") or {}
    bal = f(bal_d.get("balance_dollars"))
    if bal is None:
        b = f(bal_d.get("balance"))
        bal = b / 100.0 if b is not None else None
    shards = {}
    for row in bal_d.get("balance_breakdown") or []:
        i = f(row.get("exchange_index"))
        v = f(row.get("balance"))
        if i is not None and v is not None:
            shards[int(i)] = v

    deps, dp = pages("/portfolio/deposits", "deposits")
    wds, wp = pages("/portfolio/withdrawals", "withdrawals")

    def total(rows, *names):
        s, unknown = 0.0, 0
        for r in rows:
            v = money(r, *names)
            if v is None:
                unknown += 1
            else:
                s += v
        return s, unknown

    dep_sum, dep_bad = total(deps, "amount_dollars", "amount")
    wd_sum, wd_bad = total(wds, "amount_dollars", "amount")

    print(f"\n{'-' * 96}")
    print("1. THE CASH RECKONING -- true by construction")
    print("-" * 96)
    print(f"  deposits      {len(deps):>4} over {dp} page(s)   "
          f"${dep_sum:>10,.2f}")
    print(f"  withdrawals   {len(wds):>4} over {wp} page(s)   "
          f"${wd_sum:>10,.2f}")
    print(f"  balance now                          "
          f"${bal if bal is not None else float('nan'):>10,.2f}")
    if shards:
        for i in sorted(shards):
            print(f"      shard {i}  ${shards[i]:>9,.4f}"
                  + ("   <- crypto" if i == 2 else ""))
    if dep_bad or wd_bad:
        print(f"  NOTE: {dep_bad} deposit(s) and {wd_bad} withdrawal(s) had no "
              f"readable amount and are NOT counted. The figure below is")
        print(f"  therefore incomplete, and that is said rather than hidden.")
    cash_net = None
    if bal is not None:
        cash_net = bal + wd_sum - dep_sum
        print(f"\n  NET SINCE THE ACCOUNT OPENED   "
              f"${cash_net:+,.2f}")
        print(f"    = balance ${bal:,.2f} + withdrawn ${wd_sum:,.2f} "
              f"- deposited ${dep_sum:,.2f}")
    else:
        print("\n  THE BALANCE COULD NOT BE READ, so this reckoning cannot be")
        print("  completed. Nothing is estimated in its place.")

    # --------------------------------------------------- 2. the trading way
    fills, fp = pages("/portfolio/fills", "fills", limit=1000)
    setts, sp = pages("/portfolio/settlements", "settlements")

    spent = 0.0
    fee_total = 0.0
    fill_bad = 0
    by_series_cost = collections.defaultdict(float)
    for x in fills:
        n = f(x.get("count"), 0.0) or 0.0
        px = money(x, "yes_price_dollars", "price_dollars", "yes_price",
                   "price")
        fee = money(x, "fee_dollars", "fee") or 0.0
        if px is None:
            fill_bad += 1
            continue
        c = n * px + fee
        spent += c
        fee_total += fee
        by_series_cost[str(x.get("ticker") or "").split("-")[0]] += c

    returned = 0.0
    sett_bad = 0
    by_series_rev = collections.defaultdict(float)
    for s in setts:
        rev = money(s, "revenue_dollars", "revenue")
        if rev is None:
            sett_bad += 1
            continue
        returned += rev
        by_series_rev[str(s.get("ticker") or "").split("-")[0]] += rev

    print(f"\n{'-' * 96}")
    print("2. THE TRADING RECKONING -- the exchange's fills and settlements")
    print("-" * 96)
    print(f"  {len(fills)} fill(s) over {fp} page(s), "
          f"{len(setts)} settlement(s) over {sp} page(s)")
    if fill_bad or sett_bad:
        print(f"  NOTE: {fill_bad} fill(s) and {sett_bad} settlement(s) had no "
              f"readable amount and are excluded.")
    print(f"  spent on fills (incl. ${fee_total:,.2f} of fees)  "
          f"${spent:>10,.2f}")
    print(f"  returned by settlements                 ${returned:>10,.2f}")
    trade_net = returned - spent
    print(f"\n  NET ON TRADING                          ${trade_net:>+10,.2f}")

    # ------------------------------------------------------------- the gap
    if cash_net is not None:
        gap = cash_net - trade_net
        print(f"\n{'-' * 96}")
        print("THE GAP BETWEEN THEM")
        print("-" * 96)
        print(f"  cash reckoning   ${cash_net:+,.2f}")
        print(f"  trading reckoning ${trade_net:+,.2f}")
        print(f"  difference       ${gap:+,.2f}")
        print()
        print("  A gap is expected and is not by itself an error. The usual")
        print("  causes, in rough order of size:")
        print("    - positions bought but not yet settled (cost counted,")
        print("      revenue not yet)")
        print("    - money sitting in the Invest (margined) instance, which")
        print("      the trading balance above does not include")
        print("    - fills or settlements older than the pages read")
        print("  A gap much larger than the open position can explain is")
        print("  worth chasing; one of that size is bookkeeping.")

    # ------------------------------------------------- 3. by series and rule
    print(f"\n{'-' * 96}")
    print("3. WHERE IT WENT, BY MARKET SERIES -- exchange data, no sheet")
    print("-" * 96)
    keys = sorted(set(by_series_cost) | set(by_series_rev),
                  key=lambda k: (by_series_rev[k] - by_series_cost[k]))
    print(f"  {'series':<16}{'spent $':>12}{'returned $':>13}{'net $':>12}"
          f"{'ROI':>9}")
    for k in keys:
        c, r = by_series_cost[k], by_series_rev[k]
        roi = (100.0 * (r - c) / c) if c else 0.0
        print(f"  {k or '?':<16}{c:>12,.2f}{r:>13,.2f}{r - c:>+12,.2f}"
              f"{roi:>+8.2f}%")

    # The sheet's own split, last and most caveated.
    print(f"\n{'-' * 96}")
    print("4. BY STRATEGY, from our sheet -- THE LEAST RELIABLE SECTION")
    print("-" * 96)
    print("  live.py has only written the Strategy column since 2026-10-23.")
    print("  Earlier rows are blank and are shown as 'unlabelled' rather")
    print("  than guessed at, because a guess written into a reckoning is")
    print("  indistinguishable from a measurement afterwards.")
    try:
        bets, _ = CC.load()
    except Exception as e:
        print(f"\n  the sheet could not be read: {str(e)[:120]}")
        bets = []
    if bets:
        by = collections.defaultdict(lambda: dict(n=0, c=0.0, r=0.0, open=0))
        for b in bets:
            k = (b.get("strategy") or "").strip().lower() or "unlabelled"
            d = by[k]
            d["n"] += 1
            d["c"] += b.get("cost") or 0.0
            if b.get("rev") is None:
                d["open"] += 1
            else:
                d["r"] += b["rev"]
        print(f"\n  {'strategy':<16}{'bets':>6}{'open':>6}{'spent $':>12}"
              f"{'returned $':>13}{'net $':>12}{'ROI':>9}")
        for k in sorted(by, key=lambda k: by[k]["r"] - by[k]["c"]):
            d = by[k]
            net = d["r"] - d["c"]
            roi = (100.0 * net / d["c"]) if d["c"] else 0.0
            print(f"  {k:<16}{d['n']:>6}{d['open']:>6}{d['c']:>12,.2f}"
                  f"{d['r']:>13,.2f}{net:>+12,.2f}{roi:>+8.2f}%")
        print("\n  Rows still open have their cost counted and no revenue")
        print("  yet, so their net reads worse than it will finish.")

    print("\n" + "=" * 96)
    print("  HOW TO READ IT. Section 1 is the answer to 'am I up or down'.")
    print("  Everything below it explains that number; nothing below it")
    print("  overrides it. If a strategy's row looks good and section 1 is")
    print("  negative, the difference is the strategies not in that row.")
    print("=" * 96)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
