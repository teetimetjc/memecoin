# FROZEN SPEC: the narrow slice

**Frozen 2026-10-05, the same day as `expensive_favourite.md` and scored on
the same forward window. Nothing below may change.**

## Why this is a separate spec

It is a SUBSET of the expensive favourite, not an independent idea: 90-96c
sits inside 80-96c. It is frozen separately because it pays more per bet
(+$0.249 against +$0.189 on holdout) at a third of the volume, and the two
answer different questions. If both pass, the choice between them is a
volume-versus-margin decision. If only the wider one passes, this slice was
noise inside it. If only this one passes, the edge is concentrated at the
expensive end and the wider rule is diluting it.

Freezing it now, before its forward data exists, is what keeps that
comparison honest. Scoring it later without a freeze would be choosing the
better of two numbers after seeing both.

## The rule, exactly

- Universe: the `M15` tab only. Series `KXBTC15M`, `KXETH15M`, `KXSOL15M`,
  `KXXRP15M`, `KXDOGE15M`.
- **YES side only.** The variant search found this; the NO side at the same
  band and offset did not survive its holdout, and widening it now would be
  a different rule.
- **Entry offset: T-9.** Price taken as `ask9`.
- **Price band: 0.90 <= price < 0.96.**
- Stake $10 flat, `contracts = int(10 / price)`.
- Fee `0.07 * contracts * price * (1 - price)`, unrounded, entry only.
- Held to settlement on Kalshi's own `Result` field.
- All five series pooled. No hour filter, no path filter, no indicator
  filter. Path shape was tested and did nothing: filtering on "rose into
  entry" removed 2 bets out of 1,520.

## What the discovery data said

| | n | $/bet | % of stake | edge |
|---|---|---|---|---|
| early 70% | 1,520 | +$0.129 | +1.29% | +1.30pp |
| late 30% | 739 | +$0.249 | +2.49% | +2.46pp |

Holdout survives deleting the two best close-times (+$176.27) and the two
worst (+$250.15).

## Why it is not established

The band, the offset and the side were all chosen by reading the output of a
432-variant search, which itself ran on a region found by a 1,656-cell
sweep. The holdout was on disk while those choices were made.

## The decision rule, declared in advance

Scored ONLY on markets closing after this freeze. Passes only if ALL FOUR
hold:

1. at least **500 qualifying bets** (lower than the wider rule's 1,200
   because this band is roughly a third as frequent; the bar is set by what
   is reachable in a comparable window, not by what the result turns out to
   need),
2. profit per bet > 0 with **clustered t > 2.5**,
3. still positive after deleting the two best close-times AND the two worst,
4. win rate above its own break-even line at the average price paid.

A fail is final for this spec.
