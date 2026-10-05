# FROZEN SPEC: the expensive favourite

**Frozen 2026-10-05. Nothing below may change. If it changes it is a new
spec with a new freeze date, and the data that suggested it cannot confirm
it.**

## The claim

In the five liquid crypto series, a contract priced 80-96c nine minutes
before close wins slightly MORE often than its price implies -- enough to
cover the spread and the fee.

## The rule, exactly

- Universe: the `M15` tab only (live forward collection), series
  `KXBTC15M`, `KXETH15M`, `KXSOL15M`, `KXXRP15M`, `KXDOGE15M`.
- Both sides eligible. YES at `ask9`; NO at `1 - bid9`.
- **Entry offset: T-9.** Not T-6, not T-12.
- **Price band: 0.80 <= price < 0.96.**
- Stake $10 flat, `contracts = int(10 / price)`.
- Fee: `0.07 * contracts * price * (1 - price)`, UNROUNDED, entry only.
  A winner settles at $1.00 with nothing left to sell.
- Held to settlement, on Kalshi's own `Result` field.
- All five series pooled. No per-series selection, no hour filter, no
  indicator filter.

## What the discovery data said

M15H, 61,720 markets, 18 series. The POOLED rule over all 18 series was
NULL: +$0.034/bet, t=+1.1. The five majors were positive:

| | n | won | needs | $/bet | t |
|---|---|---|---|---|---|
| full | 11,546 | 89.7% | 88.0% | +$0.189 | +4.2 |
| early 70% | 8,895 | 89.8% | 87.9% | +$0.208 | +4.1 |
| late 30% | 2,651 | 89.3% | 88.2% | +$0.124 | +1.3 |

Cluster bootstrap on the full sample: 95% CI [+10.3c, +27.5c] a bet, 0% of
resamples at or below zero. Survives deleting the two best close-times
(+$2,160) and the two worst (+$2,279). The mirror -- buying the cheap side of
the same markets -- loses -$2.919/bet, as it must.

The positive region is CONTIGUOUS across 78-99c at T-9 and T-6, fading
smoothly into the negatives, and is absent in all 36 grid cells of the other
thirteen series. T-3 and T-1 are sharply negative, consistent with the
spread widening into expiry.

## Why this is NOT established

The five series were selected by reading an eighteen-series table, and the
band and offset came from a 1,656-cell sweep. Pooling fixed the power
problem; nothing fixed the selection problem. The late-30% result is
positive but underpowered -- t=+1.3, with 9.4% of bootstrap resamples at or
below zero.

This is the same position the fallen favourite occupied: +3.71pp on
discovery, frozen because it looked good, **-$1.29/bet on 2,355 held-out
bets.**

## The decision rule, declared in advance

Scored ONLY on markets whose close time is after this freeze. Passes only if
ALL FOUR hold:

1. at least 1,200 qualifying bets,
2. mean profit per bet > 0 with clustered t > 2.5,
3. still positive after deleting the two best close-times AND the two worst,
4. the win rate exceeds its own break-even line at the average price paid.

The t bar is 2.5 rather than 2.0 because this candidate was selected from a
large search, and the bar should be higher for a hypothesis that was mined
than for one that was reasoned. Anything else is a fail, and a fail is final
for this spec.

## What would make it tradeable even if it passes

Nothing in this file. A pass means the effect survived forward data at $10
notional on paper. Real money adds slippage, partial fills and queue
position, none of which are in `M15`, and the measured edge is 1.9% of
stake -- thin enough that any one of those could erase it.
