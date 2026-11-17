"""Where is the last $310? Reconciles the trading books against the cash.

THE STATE OF PLAY. fillmath established that pricing every fill as an
opening buy is wrong -- 111 sell fills were being counted as costs instead
of credits -- and that the best candidate rule (a sell credits the price of
the side sold) leaves the trading reckoning $309.70 ABOVE the cash figure of
+$30.66. The gap's SIGN is the clue: trading reads too HIGH, so either
revenue is being counted that we did not pay for, or a cost is missing.

OPEN POSITIONS CANNOT EXPLAIN IT. An open position has its cost counted and
its revenue not yet received, which pushes the trading number DOWN. We are
reading up. So that is not it, and it is checked rather than assumed below.

THE THREE CANDIDATES, each with a number attached:

  1. UNMATCHED SETTLEMENT REVENUE. If a settlement exists for a market whose
     fills are not in the window we read, its revenue arrives with no cost
     beside it. The fills run from 2026-09-17; anything settling from before
     then is revenue we never paid for here.
  2. SELLS THAT OPEN RATHER THAN CLOSE. The bot bets DOWN by sending an ASK
     on the yes contract. Selling yes while flat does not CREDIT p -- it
     opens a NO position and costs (1-p). Crediting it is wrong by p+(1-p)
     = the whole contract. A closing sell, by contrast, genuinely credits p.
     The two need different treatment and only a position replay can tell
     them apart, which is why neither flat rule reconciled: crediting every
     sell lands $310 high, costing every sell lands $910 low, and the truth
     is a mix.
  3. FEES NOT IN fee_cost. Only $39.41 of fees appears across 1,227 fills.

So this replays every fill per market in time order, tracks the position,
and classifies each fill as opening or closing before pricing it.

READ-ONLY. No order, no row, no setting changed.
"""
import collections
import os

import tally as T
import reckon as R

f, money, pages = R.f, R.money, R.pages


def ts_of(x, *names):
    for n in names:
        v = x.get(n)
        if v:
            return str(v)
    return ""


def main():
    print("=" * 100)
    print("WHERE IS THE LAST $310?")
    print("=" * 100)
    print("  READ-ONLY: no order, no row, no setting changed.\n")

    # ------------------------------------------------------------- the cash
    bal_d = T.get("/portfolio/balance") or {}
    bal = f(bal_d.get("balance_dollars"))
    if bal is None:
        b = f(bal_d.get("balance"))
        bal = (b / 100.0) if b is not None else None
    deps, _ = pages("/portfolio/deposits", "deposits")
    wds, _ = pages("/portfolio/withdrawals", "withdrawals")

    def applied(rows):
        tot, bad = 0.0, 0
        for r in rows:
            st = str(r.get("status") or "").strip().lower()
            if st and st not in R.APPLIED:
                continue
            v = money(r, "amount_cents", "amount_dollars", "amount")
            if v is None:
                bad += 1
            else:
                tot += v
        return tot, bad

    dep_sum, dep_bad = applied(deps)
    wd_sum, wd_bad = applied(wds)
    if bal is None or dep_bad or wd_bad:
        raise SystemExit(f"  cash side unreadable (bal={bal}, "
                         f"{dep_bad} dep / {wd_bad} wd bad); nothing decided")
    cash_net = bal + wd_sum - dep_sum
    print(f"  CASH NET  ${cash_net:+,.2f}   "
          f"(balance ${bal:,.2f} + withdrawn ${wd_sum:,.2f} "
          f"- deposited ${dep_sum:,.2f})\n")

    fills, fp = pages("/portfolio/fills", "fills", limit=1000)
    setts, sp = pages("/portfolio/settlements", "settlements")
    print(f"  {len(fills)} fill(s)/{fp} page(s), "
          f"{len(setts)} settlement(s)/{sp} page(s)")

    # ------------------------------------- candidate 1: unmatched settlements
    fill_tickers = collections.Counter()
    fill_times = []
    for x in fills:
        tk = str(x.get("ticker") or "")
        if tk:
            fill_tickers[tk] += 1
        t = ts_of(x, "created_time", "ts", "timestamp")
        if t:
            fill_times.append(t[:10])
    fill_times.sort()
    print(f"  fills span {fill_times[0] if fill_times else '?'} .. "
          f"{fill_times[-1] if fill_times else '?'}")

    unmatched_rev = 0.0
    unmatched_n = 0
    matched_rev = 0.0
    sett_bad = 0
    sett_dates = []
    for s in setts:
        rev = money(s, "revenue_dollars", "revenue")
        if rev is None:
            sett_bad += 1
            continue
        tk = str(s.get("ticker") or "")
        d = ts_of(s, "settled_time", "created_time", "ts")[:10]
        if d:
            sett_dates.append(d)
        if tk and tk in fill_tickers:
            matched_rev += rev
        else:
            unmatched_rev += rev
            unmatched_n += 1
    sett_dates.sort()
    print(f"  settlements span {sett_dates[0] if sett_dates else '?'} .. "
          f"{sett_dates[-1] if sett_dates else '?'}"
          + (f"  ({sett_bad} unreadable)" if sett_bad else ""))
    print(f"\n{'-' * 100}")
    print("CANDIDATE 1: settlement revenue with no matching fill")
    print("-" * 100)
    print(f"  matched   ${matched_rev:>10,.2f}")
    print(f"  UNMATCHED ${unmatched_rev:>10,.2f}  across {unmatched_n} "
          f"settlement(s)")
    print("  Revenue whose purchase is not in the fills we read arrives")
    print("  with no cost beside it, which inflates the trading figure.")

    # --------------------------- candidate 2: opening vs closing, by replay
    # THE REPLAY. Fills are grouped per market and walked in time order with
    # a running signed position in YES terms. A fill that moves the position
    # AWAY from zero is opening and costs money; one that moves it TOWARD
    # zero is closing and returns money. Selling yes while flat is an
    # OPENING short -- it costs (1-p), it does not credit p.
    by_mkt = collections.defaultdict(list)
    for x in fills:
        tk = str(x.get("ticker") or "")
        by_mkt[tk].append(x)
    for tk in by_mkt:
        by_mkt[tk].sort(key=lambda x: ts_of(x, "created_time", "ts"))

    spent = credited = fees = 0.0
    open_buy = open_sell = close_buy = close_sell = 0
    bad = 0
    still_open = 0.0
    for tk, xs in by_mkt.items():
        pos = 0.0                      # signed, in yes contracts
        for x in xs:
            n = f(x.get("count_fp"))
            if n is None:
                n = f(x.get("count"))
            yp = f(x.get("yes_price_dollars"))
            if n is None or yp is None or n <= 0:
                bad += 1
                continue
            fees += f(x.get("fee_cost"), 0.0) or 0.0
            act = str(x.get("action") or "").strip().lower()
            side = str(x.get("outcome_side") or x.get("side") or "").lower()
            # Everything is expressed as a signed YES position: buying NO is
            # shorting YES, and selling YES is the same thing.
            if act == "sell" or side == "no":
                delta = -n
            else:
                delta = +n
            if delta > 0:
                if pos >= 0:
                    spent += n * yp          # opening/adding a yes long
                    open_buy += 1
                else:
                    credited += n * (1.0 - yp)   # closing a short
                    close_buy += 1
            else:
                if pos <= 0:
                    spent += n * (1.0 - yp)  # OPENING a short = buying no
                    open_sell += 1
                else:
                    credited += n * yp       # closing a yes long
                    close_sell += 1
            pos += delta
        still_open += abs(pos)

    print(f"\n{'-' * 100}")
    print("CANDIDATE 2: opening vs closing, from a per-market replay")
    print("-" * 100)
    print(f"  opening buys   {open_buy:>6}   closing buys   {close_buy:>6}")
    print(f"  opening sells  {open_sell:>6}   closing sells  {close_sell:>6}")
    print(f"  {bad} fill(s) unpriceable, "
          f"{still_open:,.0f} contract(s) left open at the end")
    print(f"\n  spent    ${spent:>10,.2f}")
    print(f"  credited ${credited:>10,.2f}")
    print(f"  fees     ${fees:>10,.2f}")
    print("\n  An OPENING sell costs (1-p): selling yes while flat opens a")
    print("  NO position rather than returning money. A CLOSING sell")
    print("  genuinely credits p. Crediting every sell was $310 high and")
    print("  charging every sell was $910 low, so the answer is this mix.")

    # ------------------------------------------------------------ the total
    for label, rev in (("all settlement revenue", matched_rev + unmatched_rev),
                       ("matched revenue only", matched_rev)):
        net = rev - spent - fees + credited
        print(f"\n  NET with {label:<24} ${net:>+10,.2f}   "
              f"vs cash {net - cash_net:>+10,.2f}")

    print(f"\n{'=' * 100}")
    best = min(
        abs(matched_rev + unmatched_rev - spent - fees + credited - cash_net),
        abs(matched_rev - spent - fees + credited - cash_net))
    print(f"  closest reconciliation is ${best:,.2f} from the cash")
    if best < 25.0:
        print("  THAT IS RECONCILED. Open positions, rounding and any fee")
        print("  not carried on a fill all live inside a gap this size, so")
        print("  the per-series figures can now be trusted.")
    elif best < 100.0:
        print("  CLOSER BUT NOT CLOSED. Something of this size is still")
        print("  unexplained; the per-series figures stay provisional.")
    else:
        print("  STILL NOT RECONCILED. The remaining error is larger than")
        print("  bookkeeping, so no per-series figure should be trusted and")
        print("  no money should follow one.")
    print("=" * 100)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
