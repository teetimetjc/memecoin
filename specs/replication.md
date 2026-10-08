# Forward replication — the frozen rules on markets they were never fitted to

**Frozen 2026-10-08. Scored only on markets closing after 2026-10-08T06:00:00Z.
Paper only: no money is placed on this, ever, under this spec.**

## Why this test exists

The only mechanism-based reason to believe either frozen rule is
favourite-longshot bias: bettors overpay for longshots, so the heavy
favourite on the other side of that trade is underpriced. That is a claim
about how people bet, not about Bitcoin. If it is true it should appear in
any thin binary market with a heavy favourite, not only in the five crypto
series the rules were discovered in.

Run backwards over the collected history, it did not. The expensive
favourite made money in all five fitted series and lost it across thirteen
unfitted ones. But `mirror.py` then showed both sides of those unfitted
markets losing, with a round trip costing about 25% of stake, so the
favourite's loss there is mostly the price of trading rather than evidence
about prices. That reading is retrospective and was arrived at after seeing
the numbers, which is precisely the standing this spec exists to avoid.

So the question gets asked again, forward, on data nobody has seen.

## What is being tested

Two rules, both already frozen elsewhere, applied unchanged except for which
markets they run on.

| | band | sides | entry |
|---|---|---|---|
| narrow slice | 0.90 ≤ p < 0.96 | YES | T−9 |
| expensive favourite | 0.80 ≤ p < 0.96 | YES and NO | T−9 |

Held to settlement. $4 flat, `contracts = int(4/price)`, fee
`0.07 × C × P × (1−P)` unrounded, charged on entry only.

**Markets: every fifteen-minute series that is not one of the five majors**
(KXBTC15M, KXETH15M, KXSOL15M, KXXRP15M, KXDOGE15M). No other exclusions.

KXCRYPTOLEAD15M is included even though it behaved anomalously in the
history — −$2.90 a bet on the narrow slice at a 64% win rate, which suggests
it is not a simple binary threshold market. Dropping it now, after seeing
that, would be exactly the selection this spec exists to prevent. If it
turns out to distort the result, that is an observation for afterwards and a
reason to write a different spec, not a reason to edit this one.

## Pass conditions, all required, per rule

Declared before any post-freeze data existed:

1. at least **500 bets** (narrow slice) or **1,200 bets** (expensive
   favourite), matching the sample sizes the original specs demanded
2. profit per bet above zero
3. window-clustered **t > 2.5**
4. still positive with the two **best** close-times deleted
5. still positive with the two **worst** close-times deleted
6. win rate above its own break-even line at the price actually paid

The t bar is 2.5 rather than 2.0 for the same reason the original specs use
it: these rules were mined from a search, and a mined hypothesis deserves a
higher bar than a reasoned one.

## What each outcome means

**It replicates.** The mechanism is real, the edge is not an artifact of five
correlated crypto series, and the live rule has somewhere to grow. This would
be the first thing in this project to survive a test it could have failed on
markets it was never fitted to.

**It fails.** The edge is specific to the five majors. That is a reason to
trust the live rule **less**, not merely a reason to skip these markets —
five coins moving together is closer to one bet than to five. A failure here
should lower confidence in the two original specs even if they pass.

**It stays short of 500 bets.** No verdict. A progress bar is not a result.

## What this spec cannot do

It cannot be edited. Re-cutting the band, dropping a series, or moving the
sample size after seeing data turns this from a test into a search, and the
search is the thing that has already produced seventeen dead leads here.

A pass does not justify betting these markets. `mirror.py` measured the round
trip at about 25% of stake in exactly this market set, so even a real edge
has to clear a cost that the five majors do not charge. Execution is a
separate question from whether the mechanism exists, and this spec answers
only the second.
