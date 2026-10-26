# SHADOW SPEC: the low slice

**This is not a frozen spec and must not be read as one.** The two frozen
specs — `narrow_slice.md` and `expensive_favourite.md` — were written on
2026-10-05 before the data they are scored on existed, and `score.py` scores
them and nothing else. This file describes a band invented on 2026-10-26
*from* measured data, which is a different and much weaker thing. It lives
here so the shadow session has something to point at, and so the pass
condition is on record before the rows arrive.

## The rule

| | |
|---|---|
| series | KXBTC15M, KXETH15M, KXSOL15M, KXXRP15M, KXDOGE15M |
| side | YES only |
| band | 0.88 ≤ yes ask < 0.90 at T−9 |
| hold | to settlement |
| stake | $5, `contracts = int(5/price)`, 2 per close window |
| fee | 0.07·C·P·(1−P), unrounded, charged on entry only |
| status | **paper only** |

Identical to the live narrow slice in every respect but the band.

## Where it came from

`bandsweep.py`, on 18,116 markets of per-minute candles from 2026-09-13 to
2026-10-26, entry taken as the yes ask at T−9, fee included, with the live
90-96c band run through the same function in the same pull as a gating
control:

| band | n | won % | cost % | net ROI | older half | newer half |
|---|---|---|---|---|---|---|
| 0.88-0.89 | 201 | 90.55 | 88.74 | **+2.04%** | +0.40% | +4.02% |
| 0.89-0.90 | 197 | 90.86 | 89.69 | +1.31% | +2.92% | **−0.49%** |
| 0.90-0.96 | 1376 | 94.26 | 93.00 | +1.36% | +1.49% | +1.22% |
| 0.96-0.97 | 146 | — | — | under the 150 floor | | |
| 0.97-0.98 | 108 | — | — | under the 150 floor | | |

The control reproducing the live band at +1.36% is what makes the other rows
readable at all; a sweep whose control misses is a broken read, and every row
in it is broken the same way.

## Why it is shadowed rather than bet

88-89c cleared all four tests that were pre-registered for it: n ≥ 150, net
ROI positive against the band's own mean cost, positive in both chronological
halves, and within reach of the control.

**The cent between it and the live band flipped sign.** There is no mechanism
by which a market misprices 88-89c, prices 89-90c correctly, and then
misprices 90-96c again. Prices do not work in stripes. One of those three
rows is noise, and the cheapest candidate is the one whose older half is
+0.40% — positive by a rounding error, with the whole result carried by a
+4.02% newer half of roughly a hundred markets.

The adjacency check was not among the four tests. It should have been. This
spec exists to test it on data it was not found in.

## The pass condition

All three, together:

1. **~100 settled paper bets.** Fewer decides nothing.
2. **Net ROI positive** against this cohort's own mean cost per contract. A
   winning contract pays exactly $1.00, so the mean cost is the break-even
   line; the midpoint of the band would flatter every row.
3. **88-89c and 89-90c both positive.** A real mispricing at the bottom of
   the book does not skip a cent.

Failing (3) is a **stop**. It is not a reason to pool the two cents together
until the sign comes back — pooling after seeing the split is how a noise
result gets laundered into a finding.

## What a pass would still not establish

**Depth.** Everything above is a price. Whether six contracts actually sit at
the ask at 88c — a thinner part of the book than 90-96c — is not measured
here and is not measured by the shadow either, because a paper bet has no
fill to be disappointed by.

The T-3 test is the standing warning. Its quote-based edge was real, measured
at +2.3% to +3.2%, and its live fills came in 1.61c worse than quote. At
these prices one cent is about 1.09 ROI points, so slippage alone accounted
for roughly 1.75 points — most or all of the edge. It was paused after 31
bets at −6.38% ROI. **A rule can be right about prices and still
unprofitable to trade**, and this band enters a cent below the one where that
already happened once.

## Promotion

Moving this out of `favlive.SHADOW_RULES` and into `score.RULES` requires the
pass condition above to be met on paper first, and would make it a fourth
rule the forward scorer reports. Until then `favlive.rule()` refuses to start
this band with `LIVE_TRADING` set, in code.
