# FROZEN SPEC: the fallen favourite

**Frozen 2026-09-25. Nothing below may change. If it changes, it is a new
spec with a new freeze date and the old discovery set cannot vindicate it.**

## The claim being tested

A contract that was quoted as a favourite earlier in its window, and has
since fallen to underdog pricing, wins more often than its new price
implies -- because the market over-extrapolates the move.

## The rule, exactly

- Universe: every Kalshi 15-minute market in the `M15` tab.
- Both sides are eligible. YES falling 0.60 -> 0.45 is NO rising 0.40 ->
  0.55; a pattern that works on only one side of one book is an artefact of
  how the data was written down, not a fact about the market.
- **Entry offset:** T-3 minutes.
- **Entry price:** the ask for that side at T-3 (`ask3` for YES,
  `1 - bid3` for NO). This is a takeable quote, so no slippage is assumed
  or added.
- **Was a favourite:** the side's mid was >= 0.60 at any offset earlier
  than T-3 (T-14, T-12, T-9 or T-6).
- **Is now an underdog:** entry price is strictly between 0.02 and 0.50.
- **Stake:** $10 flat, `contracts = int(10 / price)`.
- **Fee:** `ceil(0.07 * contracts * price * (1 - price))` to the cent,
  charged on entry only.
- **Outcome:** Kalshi's own `result` field.

## What the discovery data said

Collected 2026-09-24 to 2026-09-25, 44 close-times, n=213.

| measure | value |
|---|---|
| win rate | 27.7% against a 24.0% price |
| edge vs price | +3.71pp +/- 3.46 |
| money | **-$0.60/bet +/- 2.20, t = -0.3** |
| total | -$128.02 |
| best single close-time | +$341.44 |
| without the best two | -$666.08 |
| profitable close-times | 14/44 |

**Verdict: NOT ESTABLISHED.** The probability edge is suggestive and the
dollar result is not, and one 15-minute window carries more than the whole
total. The mirror test -- buying the risen side instead -- gave -9.78pp on
the same observations viewed from the other side of the same book, and the
two sum to roughly the spread, which is what "you pay the spread either
way" looks like.

## The decision rule, declared in advance

The retest runs **only on markets whose close time is after the freeze**,
and passes only if ALL FOUR hold:

1. at least 400 close-times of held-out data,
2. mean profit per bet > 0 with clustered t > 2.0,
3. still positive after deleting the two best close-times,
4. more than 45% of close-times profitable.

Anything else is a fail. There is no partial credit, no "promising", and
no re-cut of the thresholds -- the sweep over `peak >=` already flipped
sign four times on the discovery data (-0.60, +0.15, +0.78, +4.35), which
is what noise does.

## Why it is written down at all

Twelve strategies have died here, and the two that looked alive longest
were killed by checks nobody had committed to running beforehand: a shuffle
that beat the real grid, and a day-level breakdown showing two days of
thirteen carrying an entire result. Freezing the rule and the pass
condition before the data exists is the only version of this that can
produce an answer rather than a story.
