"""Rest a bid instead of taking the ask: would it have paid? Read-only.

THE IDEA. Today live.py sends an immediate-or-cancel order and pays the
ask. That crosses the spread and pays the taker fee, and on this account
the fees come to about the same as the entire loss. Resting a bid instead
would save the spread and possibly the fee.

THE OBJECTION, which is the whole reason to measure rather than argue. A
resting bid only fills when somebody chooses to sell into it, and on a
fifteen-minute crypto market the person selling at nine minutes out is
selling because the price is going the other way. So a maker gets filled
on the windows that go wrong and misses the ones that drift up. This
project has already measured that effect from the taker side: of 65 orders
that missed because the ask rose away, the markets settled YES 98.4% of
the time against 95.8% for the orders that filled. The bets we did not get
were the better bets.

If that holds, making is worse than taking however much spread it saves,
and the saving is not the question -- the selection is.

HOW A FILL IS DECIDED, and why this is a proxy rather than a fact. The
collector samples six instants per window: 14, 12, 9, 6, 3 and 1 minutes
out. It records quotes, not trades, so whether a specific resting order
filled cannot be read directly. What CAN be read is whether the market
later offered at or below our price:

    a resting buy at P is treated as FILLED if the yes ask at any later
    sample is <= P, because an offer at or below our bid would have
    crossed it.

THIS UNDERSTATES FILLS. A dip between two samples is invisible, and a
seller can hit a bid without the quoted ask ever printing that low. So the
fill rate below is a FLOOR. It also ignores queue position: at a popular
price our order would sit behind existing size and might not be reached,
which pushes the other way.

WHAT IS CHARGED. The same fee model as the taker path, because Kalshi's
fee-rounding documentation states the accumulator applies "regardless of
whether the fills are taker or maker". If a maker rebate or an incentive
programme applies to these series it would improve the maker column, and
MAKER_FEE_MULT below exists to test that -- set it to 0 to see the result
with no maker fee at all.

THE COMPARISON IS ON THE SAME UNIVERSE. Every window in the live band is
scored both ways: what taking the ask would have returned, and what
resting a bid would have returned, with an unfilled rest returning exactly
zero rather than being dropped. Dropping the misses is how a maker study
flatters itself.

Read-only. Places no order, writes no row, changes no setting.
"""
import os
import statistics as st

import predictor as P
import collect15 as C
import oddsmath as M

# The live band and entry point, so this measures the strategy being run.
OFFSET = int(os.environ.get("MAKER_OFFSET") or 9)
LATER = tuple(int(x) for x in
              (os.environ.get("MAKER_LATER") or "6,3,1").split(",")
              if x.strip())
MIN_C = float(os.environ.get("MIN_ENTRY_CENTS") or 90.0)
MAX_C = float(os.environ.get("MAX_ENTRY_CENTS") or 96.0)
STAKE = float(os.environ.get("MAKER_STAKE") or 5.0)
# Where to rest, in cents BELOW the ask. 1 = join the bid on a 1c spread.
UNDER = float(os.environ.get("MAKER_UNDER") or 1.0)
# 1.0 charges a maker the same fee as a taker, which is what the docs say.
# 0.0 is the best case: a full maker rebate. Both are worth seeing.
MAKER_FEE_MULT = float(os.environ.get("MAKER_FEE_MULT") or 1.0)
MIN_N = int(os.environ.get("MAKER_MIN_N") or 100)


def f(v):
    try:
        x = float(v)
        return x if x == x else None
    except (TypeError, ValueError):
        return None


def main():
    print("=" * 100)
    print(f"MAKER vs TAKER at T-{OFFSET}, band {MIN_C:.0f}-{MAX_C:.0f}c, "
          f"${STAKE:.2f} a bet")
    print(f"  Resting {UNDER:.0f}c under the ask. Fills judged from the "
          f"later quotes at T-{LATER}.")
    print(f"  Maker fee multiplier {MAKER_FEE_MULT:.2f} "
          f"(1.00 = same fee as a taker, 0.00 = full rebate)")
    print("=" * 100)

    sh = P._get_client().open_by_key(P.SPREADSHEET_ID)
    rows = sh.worksheet(C.SHEET).get_all_values()
    if len(rows) < 2:
        print("\n  the collector sheet is empty.")
        return 1
    h = {k: i for i, k in enumerate(rows[0])}

    def g(r, k):
        i = h.get(k)
        return r[i] if i is not None and i < len(r) else ""

    total = graded = inband = 0
    recs = []
    for r in rows[1:]:
        if not r or not g(r, "Ticker"):
            continue
        total += 1
        res = str(g(r, "Result")).strip().lower()
        if res not in ("yes", "no"):
            continue
        graded += 1
        bid = f(g(r, f"bid{OFFSET}"))
        ask = f(g(r, f"ask{OFFSET}"))
        if bid is None or ask is None or not (0 < bid <= ask < 1):
            continue
        ask_c = ask * 100.0
        if not (MIN_C <= ask_c <= MAX_C):
            continue
        inband += 1

        # THE REST PRICE. One cent under the ask, but never above the
        # prevailing bid -- resting above the bid would be crossing, which
        # is just taking at a different price and not what is being tested.
        rest_c = min(ask_c - UNDER, bid * 100.0)
        if rest_c <= 0:
            continue

        # TAKER: pays the ask, always fills (the live path's own
        # assumption; its real fill rate is high but not 1, and that is
        # noted in the caveats rather than modelled here).
        t_n = int(STAKE / (ask_c / 100.0))
        if t_n < 1:
            continue
        t_cost = t_n * ask_c / 100.0 + M.fee_cents(t_n, ask_c / 100.0) / 100.0
        t_ret = t_n * 1.0 if res == "yes" else 0.0

        # MAKER: fills only if the market later offered at or below us.
        later_asks = [f(g(r, f"ask{m}")) for m in LATER]
        later_asks = [a * 100.0 for a in later_asks if a is not None]
        filled = any(a <= rest_c + 1e-9 for a in later_asks)
        m_n = int(STAKE / (rest_c / 100.0))
        if filled and m_n >= 1:
            fee = M.fee_cents(m_n, rest_c / 100.0) / 100.0 * MAKER_FEE_MULT
            m_cost = m_n * rest_c / 100.0 + fee
            m_ret = m_n * 1.0 if res == "yes" else 0.0
        else:
            m_cost = m_ret = 0.0

        recs.append(dict(won=(res == "yes"), filled=filled,
                         t_cost=t_cost, t_ret=t_ret,
                         m_cost=m_cost, m_ret=m_ret,
                         ask=ask_c, rest=rest_c,
                         close=str(g(r, "Close Time"))[:10]))

    print(f"\n  {total} collector row(s), {graded} graded, "
          f"{inband} in the {MIN_C:.0f}-{MAX_C:.0f}c band at T-{OFFSET}")
    if len(recs) < MIN_N:
        print(f"\n  ONLY {len(recs)} USABLE, below the {MIN_N} minimum set "
              f"before the run. No verdict.")
        return 0

    filled = [r for r in recs if r["filled"]]
    missed = [r for r in recs if not r["filled"]]

    # ------------------------------------------------- the selection test
    print(f"\n{'-' * 100}")
    print("THE SELECTION TEST -- the whole question, before any P&L")
    print("-" * 100)
    fr = 100.0 * len(filled) / len(recs)
    print(f"  a resting bid filled in {len(filled)} of {len(recs)} "
          f"window(s)  ({fr:.1f}%)")
    if filled:
        wf = 100.0 * sum(1 for r in filled if r["won"]) / len(filled)
        print(f"  FILLED   windows settled YES {wf:6.2f}%  (n {len(filled)})")
    if missed:
        wm = 100.0 * sum(1 for r in missed if r["won"]) / len(missed)
        print(f"  MISSED   windows settled YES {wm:6.2f}%  (n {len(missed)})")
    if filled and missed:
        print(f"  gap: {wf - wm:+.2f} points")
        if wf < wm:
            print("  -> ADVERSE SELECTION CONFIRMED on this sample: the")
            print("     windows a maker gets filled on settle WORSE than the")
            print("     ones it misses, which is the objection stated before")
            print("     the run and the reason the spread saving may not")
            print("     survive.")
        else:
            print("  -> no adverse selection visible here, which is the")
            print("     result that would make this worth pursuing.")

    # --------------------------------------------------------------- P&L
    tc = sum(r["t_cost"] for r in recs)
    tr = sum(r["t_ret"] for r in recs)
    mc = sum(r["m_cost"] for r in recs)
    mr = sum(r["m_ret"] for r in recs)
    print(f"\n{'-' * 100}")
    print("WHAT EACH WOULD HAVE RETURNED, on the SAME windows")
    print("-" * 100)
    print(f"  {'':<22}{'bets':>6}{'staked $':>12}{'returned $':>13}"
          f"{'net $':>11}{'ROI':>9}")
    print(f"  {'TAKER (take the ask)':<22}{len(recs):>6}{tc:>12,.2f}"
          f"{tr:>13,.2f}{tr - tc:>+11,.2f}"
          f"{100.0 * (tr - tc) / tc if tc else 0:>+8.2f}%")
    print(f"  {'MAKER (rest a bid)':<22}{len(filled):>6}{mc:>12,.2f}"
          f"{mr:>13,.2f}{mr - mc:>+11,.2f}"
          f"{100.0 * (mr - mc) / mc if mc else 0:>+8.2f}%")
    print(f"\n  An unfilled rest returns exactly zero and is counted in the")
    print(f"  {len(recs)} windows, not dropped. Dropping the misses is how a")
    print(f"  maker study flatters itself.")

    # The two effects pulling in opposite directions, sized.
    if filled:
        saved = st.mean([r["ask"] - r["rest"] for r in filled])
        print(f"\n  spread saved when filled: {saved:.2f}c per contract")
        tfee = sum(M.fee_cents(int(STAKE / (r['ask'] / 100.0)),
                               r["ask"] / 100.0) / 100.0 for r in filled)
        print(f"  taker fee on those same windows: ${tfee:,.2f}")

    # ------------------------------------------------------------ halves
    recs.sort(key=lambda r: r["close"])
    mid = len(recs) // 2
    print(f"\n{'-' * 100}")
    print("BOTH CHRONOLOGICAL HALVES")
    print("-" * 100)
    for label, part in (("first half", recs[:mid]), ("second half", recs[mid:])):
        pc = sum(r["m_cost"] for r in part)
        pr = sum(r["m_ret"] for r in part)
        qc = sum(r["t_cost"] for r in part)
        qr = sum(r["t_ret"] for r in part)
        nf = sum(1 for r in part if r["filled"])
        print(f"  {label:<12} n {len(part):>4}  filled {nf:>4}   "
              f"maker {100.0 * (pr - pc) / pc if pc else 0:+6.2f}%   "
              f"taker {100.0 * (qr - qc) / qc if qc else 0:+6.2f}%")

    print("\n" + "=" * 100)
    print("  WHAT THIS CANNOT SEE, so the number is not over-read.")
    print()
    print("  THE FILL RATE IS A FLOOR. Six samples per window; a dip between")
    print("  two of them is invisible, and a seller can hit a bid without the")
    print("  quoted ask ever printing that low. More rests would have filled")
    print("  than are counted here.")
    print()
    print("  QUEUE POSITION IS IGNORED, and it pulls the other way: at a")
    print("  popular price the order sits behind existing size and may never")
    print("  be reached even when the price trades there.")
    print()
    print("  AND THE TAKER COLUMN ASSUMES EVERY TAKE FILLS. The live path's")
    print("  real fill rate is high but not 1 -- 65 of 229 orders once missed")
    print("  entirely -- so the taker side here is slightly flattered too.")
    print("=" * 100)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
