# The narrow slice, entered three minutes later

Frozen 2026-10-11T00:15:00Z. Scored only on markets closing after that
instant. Paper only.

## The rule

Identical to the live narrow slice in every respect but one:

| | live rule | this spec |
|---|---|---|
| band | 90–96¢ | 90–96¢ |
| side | YES | YES |
| markets | BTC ETH SOL XRP DOGE | same |
| hold | to settlement | to settlement |
| **entry** | **T−9** | **T−6** |

That is the whole difference. Buy three minutes later.

## Why this is being tested rather than switched to

Scored across 37,040 historical markets, T−6 beat T−9:

```
 entry   bets     won   needed      per $       t
 T-12     405   93.6%    91.7%     +1.30%    +0.80
 T-9     2500   94.5%    92.4%     +1.61%    +2.48    <- live
 T-6     4064   95.1%    92.9%     +1.77%    +3.93
 T-3     3317   92.7%    93.1%     -1.01%    -1.82
 T-1     1285   92.4%    93.2%     -1.54%    -1.96
```

More bets, a better rate per dollar, and a far stronger t. It also sits in
the middle of a plausible window rather than at its edge: T−12 through T−6
are positive, T−3 and T−1 are negative, and the sign flips as the price of a
favourite rises faster than its win rate. That is the favourite-longshot
curve running out of road, and it is a mechanism rather than a coincidence.

**But T−9 was itself chosen by searching this same data.** Finding that T−6
looks better in it is that search running a second time. The gap is 0.16
points per dollar, which is well inside what noise produces across six
columns. Acting on it would be fitting history with live money.

So it gets a forward test, which is the only thing that can tell the two
apart.

## The comparison that decides it

Not "is T−6 profitable" — "is T−6 better than T−9". Both are scored over the
**same forward window, on the same markets**, from the freeze instant above.
A head-to-head removes the thing that makes a single number untrustworthy:
if the next three weeks are simply good for the rule, both columns rise
together and neither learns anything.

## Pass conditions — all five, fixed now

1. At least **500** qualifying bets after the freeze.
2. Net profit above zero, after fees, priced at the ask.
3. Clustered t above **2.5**, clustered by close-time.
4. Beats T−9 over the same window on return per dollar staked.
5. Still passes 2–4 after deleting the two best and two worst close-times.

Anything less is a fail, and a fail means the live rule stays at T−9.

## What a pass would license

Moving the live entry to T−6 — and nothing else. It would not license a
wider band, a bigger stake, or adding the NO side, each of which has its own
evidence and its own answer.

## What this cannot tell us

The forward test scores the quote at T−6, not a fill. The live rule pays the
ask plus up to a cent of slip and sometimes fills better; three minutes
closer to the close the book may be deeper or thinner than it is at T−9, and
no backtest of quotes can measure that. If this passes, the first live week
needs its fills watched against the paper figures before the stake is
trusted at size.

## Scored by

`t6score.py`, which takes no arguments and exposes no thresholds. Every
number above is a constant in the file.
