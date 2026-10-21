"""Should BTC and ETH stay in the rotation? Counterfactual, places nothing.

THE EVIDENCE THAT RAISED IT. goldoil.py ran all five live series through one
code path over 1,500 settled markets each, in the 90-96c band at T-9:

    SOL   +3.70%   holds in both halves
    DOGE  +0.80%   flips
    XRP   -0.89%   flips
    ETH   -1.60%   NEGATIVE IN BOTH HALVES
    BTC   -1.92%   NEGATIVE IN BOTH HALVES
    all   +0.11%   flips

Two of the five are negative in both halves. Nothing else this project has
tested has been negative in both halves -- not the hours, not the
volatility buckets, not XRP when it was narrowed properly. That is the
strongest reason yet to look at dropping something.

BUT THE BACKTEST IS NOT THE ACCOUNT, and this file exists because of the
difference. The backtest says what the PRICE did across 7,500 markets. The
live record says what was actually paid, after slippage, fees and the
guards, across a few hundred real bets. They disagree in general -- pooled
backtest +0.11% against a live record of +$0.18 on 322 bets -- and a
decision to stop betting a coin should be made on the money, with the
backtest as corroboration rather than as the verdict.

So this recomputes the live P&L with BTC and ETH removed, and does it
honestly in the one way that is easy to get wrong:

  WINDOWS, NOT BETS. Bets sharing a close time resolve together 96% of the
  time, and the per-window cap means dropping a coin does not simply delete
  its rows -- it can change which OTHER bets were taken, because a window
  that hit the 2-bet cap on BTC and ETH would have had room for the coin
  that came next. That second-order effect cannot be recovered from the
  record (the signals that were never placed were never priced), so it is
  NOT simulated, and the counterfactual is stated as what it is: the same
  windows with those legs removed, which understates what dropping them
  would free up rather than overstating it.

  THE SETTLEMENT SHARE IS RECOMPUTED. One settlement is split across the
  bets on that ticker by contract count. Removing a leg changes the split
  for whatever remains, and failing to redo it is exactly the bug that once
  showed a profit that did not exist.

CHRONOLOGICALLY SPLIT, because a coin that looks bad over one stretch and
fine over the next is the recent stretch talking, not the coin -- which is
how the XRP candidate died earlier today.

Read-only. Changes no configuration and places no order.
"""
import collections
import statistics as st

import patterns as PT
import tally as T

DROP = ("BTC", "ETH")


def summarise(label, bets):
    """Net, ROI and the win rate against this group's own break-even."""
    if not bets:
        print(f"  {label:<26} (no bets)")
        return None
    n = len(bets)
    staked = sum(b["cost"] or 0.0 for b in bets)
    ret = sum(b["rev"] or 0.0 for b in bets)
    net = ret - staked
    wins = sum(1 for b in bets if b["won"])
    wr = 100.0 * wins / n
    contracts = sum(int(b["contracts"] or 0) for b in bets)
    be = (100.0 * staked / contracts) if contracts else float("nan")
    lo, hi = T.wilson(wins, n)
    print(f"  {label:<26} {n:>4} bets  ${staked:>7.2f} staked  "
          f"net ${net:>+7.2f}  ROI {100*net/staked if staked else 0:>+6.2f}%  "
          f"won {wr:>6.2f}% vs {be:>6.2f}% needed  gap {wr-be:>+6.2f}")
    print(f"  {'':<26} 95% interval {100*lo:.1f}-{100*hi:.1f}% "
          f"({'contains' if 100*lo <= be <= 100*hi else 'EXCLUDES'} break-even)")
    return net


def window_view(bets, label):
    """Per-window P&L: the unit of risk, since legs resolve together."""
    win = collections.defaultdict(list)
    for b in bets:
        win[b["ts"]].append(b)
    vals = [sum((x["rev"] or 0.0) - (x["cost"] or 0.0) for x in g)
            for g in win.values()]
    if len(vals) < 2:
        print(f"  {label:<26} (too few windows)")
        return
    sd = st.pstdev(vals)
    print(f"  {label:<26} {len(vals):>4} windows  net ${sum(vals):>+7.2f}  "
          f"sd ${sd:>5.2f}  worst ${min(vals):>+7.2f}")


def reshare(bets):
    """Recompute each bet's settlement share within its own ticker.

    MUST be redone after removing legs. A settlement is split by contract
    count across the bets on that ticker; drop one and the rest are entitled
    to more of it. Carrying the old share forward would under-credit the
    survivors and quietly flatter the drop.
    """
    by_ticker = collections.defaultdict(list)
    for b in bets:
        by_ticker[b["ticker"]].append(b)
    out = []
    for _tk, g in by_ticker.items():
        tot = sum(int(x["contracts"] or 0) for x in g) or 1
        for x in g:
            y = dict(x)
            share = (int(x["contracts"] or 0) / tot) if len(g) > 1 else 1.0
            # rev was stored as settlement * old_share; recover the whole
            # settlement first, then re-split it.
            old = x.get("_share", 1.0) or 1.0
            whole = (x["rev"] / old) if (x["rev"] is not None and old) else None
            y["rev"] = (whole * share) if whole is not None else None
            y["_share"] = share
            y["won"] = (y["rev"] or 0.0) > 0 if y["rev"] is not None else None
            out.append(y)
    return out


def main():
    _bets, settled = PT.load()
    print("=" * 100)
    print("SHOULD BTC AND ETH STAY? -- the live record with and without them")
    print("=" * 100)
    if not settled:
        print("  nothing settled yet")
        return 0

    kept = reshare([b for b in settled if b["coin"] not in DROP])
    dropped = [b for b in settled if b["coin"] in DROP]

    print(f"  {len(settled)} settled bets; {len(dropped)} on "
          f"{'/'.join(DROP)}, {len(kept)} on the rest\n")
    print("-- PER BET " + "-" * 88)
    actual = summarise("as actually bet", settled)
    without = summarise(f"without {'/'.join(DROP)}", kept)
    summarise(f"{'/'.join(DROP)} alone", dropped)
    print()
    if actual is not None and without is not None:
        d = without - actual
        print(f"  Dropping {'/'.join(DROP)} would have changed the account by "
              f"${d:+.2f}.")
        print("  That is the whole claim. It is NOT a forecast, and the")
        print("  second-order effect is deliberately left out: a window that")
        print("  hit the 2-bet cap on these coins would have had room for the")
        print("  next signal, which was never placed and so was never priced.")
        print("  The real figure is therefore at least this good, not less.\n")

    print("-- PER WINDOW, the unit of risk " + "-" * 67)
    window_view(settled, "as actually bet")
    window_view(kept, f"without {'/'.join(DROP)}")
    print()

    print("-- SPLIT IN HALF, because a bad stretch is not a bad coin " + "-" * 41)
    for lab, group in (("as actually bet", settled),
                       (f"without {'/'.join(DROP)}", kept)):
        o, n = PT.halves(group)
        print(f"  {lab}")
        summarise("    older half", o)
        summarise("    newer half", n)
        print()

    print("-- EACH DROPPED COIN ON ITS OWN " + "-" * 68)
    for c in DROP:
        g = [b for b in settled if b["coin"] == c]
        summarise(c, g)
        if len(g) >= 20:
            o, n = PT.halves(g)
            summarise(f"  {c} older half", o)
            summarise(f"  {c} newer half", n)
        print()

    print("=" * 100)
    print("  WHAT WOULD JUSTIFY DROPPING THEM: negative in both halves of the")
    print("  LIVE record, agreeing with the backtest, and a dollar figure")
    print("  worth the loss of bet rate -- fewer coins means fewer windows,")
    print("  which slows every decision still outstanding. A coin that is")
    print("  merely negative overall has not shown anything; XRP looked that")
    print("  way this morning and flipped the moment it was split properly.")
    print("=" * 100)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
