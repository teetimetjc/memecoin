# CLOSED: resting orders in slow Kalshi markets

**Opened 2026-09-28. Closed 2026-10-05.** The last open question in this
project, and the only one that ever produced a positive intermediate result.

## The claim being tested

Adverse selection -- being filled precisely when the price is about to move
against you -- destroyed every market-making test on 15-minute crypto
(-$2.41/bet). Slow markets have different participants: the person taking
your quote is more often someone who wants out than a bot that knows
something. So resting might pay there.

## What was measured, in order

**slowbook** (70 days of candlestick history, 10 liquid families, 1,307
markets) could only BRACKET the answer, because candlesticks carry no queue
position:

| fill assumption | $/bet |
|---|---|
| optimistic (every order fills, free) | +$0.117 |
| adverse (fill only when run over) | -$0.130 |

Straddling zero, so nothing could be concluded. Q1 on the same data --
whether those markets are mispriced -- came back null: best excess +0.01pp
across 8 price buckets.

**slowcollect** then recorded forward what history lacked: the book WITH
SIZES, every print with the taker's side, and the book afterwards. 56,355
rows, 26,890 trade rows, 95,068 prints, 1.48M contracts over six days.

**slowfill** scored the fill quality -- price obtained against the mid just
after -- and it was POSITIVE:

| | |
|---|---|
| rest ask, marked +5min | +1.299c/contract, t=+4.6 |
| rest bid, marked +5min | +1.891c/contract, t=+5.6 |

Taker-side flag verified against the book on 26,048/26,915 prints (97%), so
the sign is not inverted. **This part stands: fills in slow markets are not
predominantly toxic, and the adverse assumption imported from crypto was
too pessimistic for this venue.**

It was not a profit, and said so, for three reasons -- a mid is not cash, an
86% fill rate with no deadline means "eventually", and attempts starting at
every book row shared fills and inflated t.

**slowround** removed all three: a 30-minute deadline on the entry, one
attempt per market at a time with an hour's cooldown, and the position
actually closed -- by crossing (paying the other touch plus the 0.07 taker
fee) or by resting again and crossing only if that failed.

| exit | side | fill | $/trip | t |
|---|---|---|---|---|
| cross | sell YES | 30% | **-0.833** | -10.0 |
| cross | buy YES | 22% | **-0.524** | -3.9 |
| rest, else cross | sell YES | 27% | **-0.534** | -5.1 |
| rest, else cross | buy YES | 21% | +0.294 | +1.7 (fails robustness) |

## Verdict: NO

The +1.3c of fill quality is real and the exit consumes all of it. Two
mechanisms, both measured:

  THE DEADLINE CUT FILLS FROM 86% TO 21-30%. The "eventually" fills were
  carrying the earlier result, and nobody can wait days per trade.

  THE PASSIVE EXIT FILLS 21-27% OF THE TIME, so three trades in four are
  forced to cross, paying a spread plus a taker fee larger than the spread
  collected on entry.

The single positive cell is one of four, t=+1.7, and fails the
best/worst-two-markets check. That is what noise looks like.

**Holding to settlement instead of exiting does not rescue it**, and needs
no further test: that converts market making into a directional bet, and Q1
already measured these markets as calibrated. A calibrated price offers no
edge to a holder.

## Why this one is worth recording rather than forgetting

It is the only idea here that produced a true positive intermediate result,
and the positive part was real -- fill toxicity genuinely scales with how
bot-saturated a venue is. The failure was downstream of it, in a cost that
the intermediate measurement did not include. Any future version of this
idea must price THE EXIT before anything else, because that is where the
edge died, not in the fill.
