"""Which fill-pricing rule is the right one? Decided by the cash, not by me.

THE BUG. reckon.py's fill_cost prices every fill as an opening BUY -- its own
docstring says so and warns that "a sell would be counted as a cost instead
of a credit". There are 111 sell fills worth $448.50 in this account, so the
warning has come true: the trading reckoning reads -$540.18 while the cash
reckoning reads +$30.66, a $570.84 gap. Every per-series ROI inherits the
same error, and so did fillslip.py, which copied the price extraction.

WHY I AM NOT JUST FIXING THE SIGN. The bot bets DOWN by sending an ASK on
the yes contract, and those fills come back as outcome_side=no with
action=sell. Whether that should be priced as a credit at its own price, a
credit at the complement, or a cost at the complement is not something the
field names settle, and a sign flip overshoots the cash figure -- so at least
one plausible rule is wrong. Guessing between them is what produced the bug
in the first place.

SO THE CASH DECIDES. The cash reckoning -- balance now, plus everything
withdrawn, minus everything deposited -- is arithmetic on money that actually
moved through the account. It cannot be wrong about the bottom line. So every
candidate rule is applied to every fill, the resulting net is compared with
the cash figure, and the rule that reconciles is the correct semantics. That
is a measurement, not a preference.

WHAT WOULD MAKE THIS INCONCLUSIVE, stated in advance: open positions,
money in the Invest instance, and fills or settlements older than the pages
read all put a floor under how closely anything can match. If two rules land
within that floor of each other, this cannot separate them and says so
instead of picking the prettier one.

READ-ONLY. No order, no row, no setting changed.
"""
import collections
import os

import tally as T
import reckon as R

f, money, pages = R.f, R.money, R.pages


def flows(x, rule):
    """(cash_out, fee) in dollars for one fill under `rule`.

    cash_out is POSITIVE for money leaving the account and NEGATIVE for
    money arriving. Fees always leave.
    """
    n = f(x.get("count_fp"))
    if n is None:
        n = f(x.get("count"))
    if n is None:
        return None, 0.0
    yp = f(x.get("yes_price_dollars"))
    np_ = f(x.get("no_price_dollars"))
    side = str(x.get("outcome_side") or x.get("side") or "").strip().lower()
    act = str(x.get("action") or "").strip().lower()
    fee = f(x.get("fee_cost"), 0.0) or 0.0
    own = np_ if side == "no" else yp
    if own is None:
        return None, fee
    is_sell = act == "sell"

    if rule == "A_all_buys":
        # What the code does today: every fill is an opening purchase at the
        # price of whichever side the account ended up holding.
        return n * own, fee
    if rule == "B_sell_credit_own":
        # A sell returns the price of the side sold.
        return (-n * own if is_sell else n * own), fee
    if rule == "C_sell_cost_complement":
        # A sell on one leg OPENS the other leg, at its complementary price:
        # selling yes at 0.84 opens no at 0.16.
        return (n * (1.0 - own) if is_sell else n * own), fee
    if rule == "D_sell_credit_complement":
        return (-n * (1.0 - own) if is_sell else n * own), fee
    if rule == "E_always_yes_leg":
        # Ignore outcome_side; price everything off the yes leg, buys as a
        # cost and sells as a credit.
        if yp is None:
            return None, fee
        return (-n * yp if is_sell else n * yp), fee
    raise ValueError(rule)


RULES = [
    ("A_all_buys", "every fill an opening buy at its own side's price "
                   "(what the code does today)"),
    ("B_sell_credit_own", "a sell credits the price of the side sold"),
    ("C_sell_cost_complement", "a sell opens the other leg, costing the "
                               "complement"),
    ("D_sell_credit_complement", "a sell credits the complement"),
    ("E_always_yes_leg", "price everything off the yes leg; sells credit"),
]


def main():
    print("=" * 100)
    print("WHICH FILL-PRICING RULE RECONCILES WITH THE CASH?")
    print("=" * 100)
    print("  READ-ONLY: no order, no row, no setting changed.\n")

    # ------------------------------------------------- the cash, the arbiter
    bal_d = T.get("/portfolio/balance") or {}
    bal = f(bal_d.get("balance_dollars"))
    if bal is None:
        b = f(bal_d.get("balance"))
        bal = (b / 100.0) if b is not None else None
    deps, _ = pages("/portfolio/deposits", "deposits")
    wds, _ = pages("/portfolio/withdrawals", "withdrawals")

    def applied_total(rows):
        """Applied rows summed, matching reckon's reading exactly.

        TWO MISTAKES LIVED HERE, both of which reckon.money already
        documents. The deposit amount field is `amount_cents`, not
        `amount_dollars` -- reckon's docstring names that precise error as
        the bug the whole file exists to prevent, and I reproduced it on the
        first try, which is why the run refused itself. And a row with an
        EMPTY status is treated as applied, as reckon does; excluding it
        dropped real money from the arbiter.
        """
        tot, bad = 0.0, 0
        for r in rows:
            st = str(r.get("status") or "").strip().lower()
            if st and st not in R.APPLIED:
                continue
            v = money(r, "amount_cents", "amount_dollars", "amount")
            if v is None:
                bad += 1
                continue
            tot += v
        return tot, bad

    dep_sum, dep_bad = applied_total(deps)
    wd_sum, wd_bad = applied_total(wds)
    if bal is None or dep_bad or wd_bad:
        print("  THE CASH SIDE COULD NOT BE READ CLEANLY "
              f"(balance={bal}, {dep_bad} deposit(s) and {wd_bad} "
              "withdrawal(s) unreadable).")
        print("  Without a trustworthy arbiter nothing here can be decided,")
        print("  so no rule is endorsed.")
        print("=" * 100)
        return 1
    cash_net = bal + wd_sum - dep_sum
    print(f"  balance now  ${bal:>10,.2f}")
    print(f"  deposited    ${dep_sum:>10,.2f}")
    print(f"  withdrawn    ${wd_sum:>10,.2f}")
    print(f"  CASH NET     ${cash_net:>+10,.2f}   <- the arbiter\n")

    fills, fp = pages("/portfolio/fills", "fills", limit=1000)
    setts, sp = pages("/portfolio/settlements", "settlements")
    returned, sett_bad = 0.0, 0
    for s in setts:
        rev = money(s, "revenue_dollars", "revenue")
        if rev is None:
            sett_bad += 1
            continue
        returned += rev
    print(f"  {len(fills)} fill(s) / {fp} page(s), "
          f"{len(setts)} settlement(s) / {sp} page(s)")
    print(f"  settlement revenue ${returned:,.2f}"
          + (f"   ({sett_bad} unreadable)" if sett_bad else ""))

    acts = collections.Counter(
        str(x.get("action") or "?").strip().lower() for x in fills)
    sides = collections.Counter(
        str(x.get("outcome_side") or x.get("side") or "?").strip().lower()
        for x in fills)
    print(f"  by action {dict(acts)}   by side {dict(sides)}")
    if not acts.get("sell"):
        print("\n  NO SELL FILLS IN THIS WINDOW, so every rule below is")
        print("  identical and this cannot distinguish them.")
        print("=" * 100)
        return 0

    print(f"\n{'-' * 100}")
    print("EACH RULE, AGAINST THE CASH")
    print("-" * 100)
    print(f"  {'rule':<28}{'spent $':>12}{'fees $':>10}"
          f"{'net $':>12}{'vs cash':>12}")
    results = []
    for name, _desc in RULES:
        spent = fees = 0.0
        bad = 0
        for x in fills:
            c, fe = flows(x, name)
            if c is None:
                bad += 1
                continue
            spent += c
            fees += fe
        net = returned - spent - fees
        gap = net - cash_net
        results.append((abs(gap), name, net, gap, spent, fees, bad))
        print(f"  {name:<28}{spent:>12,.2f}{fees:>10,.2f}"
              f"{net:>+12,.2f}{gap:>+12,.2f}")

    print("\n  'vs cash' is how far that rule's net sits from the cash")
    print("  figure. Smaller is better; the sign says which way it errs.")

    results.sort()
    best = results[0]
    second = results[1] if len(results) > 1 else None
    print(f"\n{'=' * 100}")
    print("VERDICT")
    print("=" * 100)
    print(f"  closest rule: {best[1]}")
    for name, desc in RULES:
        if name == best[1]:
            print(f"    = {desc}")
    print(f"  it lands ${best[3]:+,.2f} from the cash figure")

    # THE FLOOR. Open positions and pre-window records put a limit on how
    # close any rule can get, so a winner that is barely ahead of the runner
    # up has not been distinguished from it.
    if second is not None:
        margin = second[0] - best[0]
        print(f"  the runner-up ({second[1]}) is ${second[3]:+,.2f} out, "
              f"a margin of ${margin:,.2f}")
        if margin < 50.0:
            print("\n  TOO CLOSE TO CALL. The two best rules sit within $50")
            print("  of each other, which is inside what open positions and")
            print("  pre-window records can explain. This does NOT endorse")
            print("  either; the per-series figures stay unreliable until")
            print("  something else separates them.")
        elif best[0] > 100.0:
            print("\n  NO RULE RECONCILES. Even the closest is more than")
            print("  $100 from the cash, so the remaining error is not the")
            print("  buy/sell distinction alone and the search is not over.")
        else:
            print("\n  THIS RULE IS THE SEMANTICS. It reconciles the trading")
            print("  reckoning with money that actually moved, and it beats")
            print("  the alternatives by more than the bookkeeping floor.")
            print("  reckon.fill_cost and fillslip should both adopt it,")
            print("  after which the per-series ROIs can be trusted.")
    print("=" * 100)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
